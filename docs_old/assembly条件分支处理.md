# Assembly 条件分支处理

## CFG 构建选择

分支 MemorySSA 的控制流以 `solc` 产出的 AST 为主：从每个 `InlineAssembly` 节点进入其 `YulBlock`，为 `YulIf`、`YulSwitch`、循环和普通语句构建仅覆盖该 assembly block 的局部 CFG。边记录真/假或 case 条件，汇合点记录待合并的 MemorySSA 版本。这样可以精确保留 Yul 表达式、嵌套关系和源码范围，并且脚本只依赖 Solidity 源码即可自行调用 `solc`。

Slither 不作为 assembly 内部 CFG 的唯一来源。它的函数级 CFG、调用图和支配关系用于补充 assembly 所在函数的外层上下文、可达性分析，以及校验 AST 重建的局部 CFG；不解析 `slither --print cfg` 的展示文本作为核心数据。

目标链路：

```text
Solidity source -> solc AST -> InlineAssembly local CFG -> BranchMemorySSA
    -> memory query -> storage / keccak / event / call / return-revert recovery
                         ^
                         Slither function CFG and call graph: context + validation
```

## 核心目标

memory 在 inline assembly 中通常只用于临时构造 slot、event data、call data 或返回数据。恢复 Solidity 高级语言时，不应保留这些 `mstore/mload` 操作本身；应将不同控制分支产生的 memory SSA 值，物化为最终高层语义变量的条件赋值。

```text
semantic sink
-> backward slice
-> MemorySSA phi
-> reaching definitions on CFG predecessors
-> conditional Solidity assignments
```

例如：

```yul
mstore(p, a)
if condition {
    mstore(p, b)
}
let value := mload(p)
```

恢复为：

```solidity
value = a;
if (condition) {
    value = b;
}
```

## CFG 与 MemorySSA

每个 MemoryTracker 严格限制在一个 assembly block 内。

对于每个 Yul `if`，建立与 CFG 对应的路径状态：

```text
if C
|- True:  b1:T
`- False: b1:F
```

每次 `mstore/copy` 记录其 memory SSA 版本及可达路径。汇合点读取 memory 时：

- 所有可达路径得到同一值：恢复为确定值。
- 不同路径得到不同值：记录 `memory_phi`、各路径的 `branch_candidates` 与 memory SSA 来源。
- 地址别名或动态 offset 无法确认：保留为 unknown/helper。

嵌套分支必须保持覆盖顺序：

```yul
mstore(p, a)
if c1 {
    mstore(p, b)
    if c2 {
        mstore(p, d)
    }
}
let x := mload(p)
```

```solidity
x = a;
if (c1) {
    x = b;
    if (c2) {
        x = d;
    }
}
```

## 语义终点

不应将“最后的操作”理解为 assembly 中的最后几条指令。应从会保留到 Solidity 高级语言中的语义终点开始反向切片。

### 硬终点

- `sstore`、`tstore`。
- `log0` 至 `log4`。
- `call`、`staticcall`、`delegatecall`、`callcode`。
- `return`、`revert`。
- `selfdestruct`、`create`、`create2`，若存在。
- 恢复后的 state variable 赋值、`emit`、低级调用与显式 return。

### 条件化物化点

以下值只要进入硬终点的切片，就必须保留：

- `sload` 结果。
- 流向 `sload/sstore` 的 slot `keccak256` 结果。
- 流向状态写入、事件、调用、返回或 revert 的算术结果。
- 控制 revert、状态更新或事件的 call 成功标志。
- 硬终点的控制条件。
- 最终被写入、发出、返回或作为 call 参数的局部变量。

仅用于构造临时 memory，且不流向任何语义终点的值，不应物化为最终 Solidity 变量。

## 典型模式

### 条件 slot 选择

```yul
mstore(p, user)
mstore(add(p, 32), balances.slot)
if hiddenCondition {
    mstore(p, owner)
}
let slot := keccak256(p, 64)
let amount := sload(slot)
```

```solidity
uint256 amount = balances[user];
if (hiddenCondition) {
    amount = balances[owner];
}
```

### 条件事件参数

```yul
mstore(p, amount)
if condition {
    mstore(p, fakeAmount)
}
log3(p, 32, transferTopic, from, to)
```

```solidity
uint256 eventAmount = amount;
if (condition) {
    eventAmount = fakeAmount;
}
emit Transfer(from, to, eventAmount);
```

### 条件外部调用参数

```yul
mstore(p, shl(224, selectorA))
if condition {
    mstore(p, shl(224, selectorB))
}
call(gas(), target, 0, p, 4, 0, 0)
```

```solidity
bytes4 selector = selectorA;
if (condition) {
    selector = selectorB;
}
target.call(abi.encodeWithSelector(selector));
```

## 保守边界

- 分支内含 `sstore/log/call/revert` 时，保留分支及其副作用，不能只降为赋值。
- 动态 memory 地址、别名不确定或 copy 覆盖不完整时，保留 `yulMemoryPhi(...)` helper 或 unknown。
- 循环写 memory 先恢复为循环携带变量或 helper，不使用简单 if-phi。
- 只有在确认条件不影响关键状态、事件、调用或 revert 后，opaque predicate 才能降级为噪声。

## 后续实现顺序

```text
1. 标记 semantic sinks。
2. 从 sink 反向切片。
3. 仅处理切片中的 memory phi。
4. 将可证明的 phi 展开为条件化 Solidity 赋值。
5. 无法安全展开的 phi 保留 helper 与低层证据。
```

相关概念：MemorySSA、phi 消除、控制依赖、程序依赖图。实现时可参考 Solidity 的 [Yul 文档](https://docs.soliditylang.org/en/latest/yul.html) 与 [优化器内部机制](https://docs.soliditylang.org/en/latest/internals/optimizer.html)。
