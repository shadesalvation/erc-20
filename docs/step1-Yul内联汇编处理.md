# Solidity 内联汇编处理任务整理

## 1. 目标

在 Solidity 智能合约中，部分核心逻辑可能通过 `assembly { ... }` 内联汇编实现。由于汇编代码直接操作 memory、storage、event log、call、revert 等底层语义，不能简单依赖 Solidity 高级语法分析工具直接恢复其行为。

因此，处理内联汇编的核心目标不是直接还原原始 Solidity 源码，而是提取其中的安全相关低层语义，并进一步归纳为 Solidity-like 的安全核心逻辑视图。重点关注状态变量访问、余额变化、授权变化、事件触发、条件检查、外部调用和隐藏控制逻辑。

------

## 2. Assembly 所属上下文定位

首先需要确定每个 `assembly` 代码块所在的位置。

需要提取的信息包括：

```text
合约名称
函数名称
函数参数
函数可见性
函数是否为 ERC20 标准入口函数
assembly block 的源码位置
assembly block 所在函数是否可由 transfer、transferFrom、approve 等入口函数触达
```

该步骤的作用是判断该段汇编是否参与 ERC20 核心行为。例如，若某段汇编位于 `_transfer`、`_spendAllowance`、`approve` 等函数中，则通常需要重点分析。

------

## 3. Memory 操作恢复

内联汇编中大量使用 `mload` 和 `mstore` 操作 memory。它们通常用于构造 `keccak256` 输入、事件数据、外部调用输入或返回缓冲区。

需要重点识别以下操作：

```solidity
let p := mload(0x40)
mstore(p, value)
mstore(add(p, 32), value)
```

需要恢复的内容包括：

```text
memory 地址
memory 写入值
memory 写入顺序
memory 数据后续是否被 keccak256 使用
memory 数据后续是否被 log 使用
memory 数据后续是否被 call/staticcall/delegatecall 使用
```

示例：

```solidity
mstore(p, _from)
mstore(add(p, 32), 0)
let slot := keccak256(p, 64)
```

可恢复为：

```text
mem[p] = _from
mem[p+32] = 0
slot = keccak256(_from, 0)
```

该步骤是后续恢复 mapping slot、event data 和 call input 的基础。

------

## 4. Storage Slot 计算恢复

Solidity 中的 mapping 和 nested mapping 在 storage 中通常通过 `keccak256` 计算存储位置。汇编中如果直接使用 `keccak256` 计算 slot，需要恢复其高级语义。

### 4.1 普通 mapping

典型形式：

```solidity
mstore(p, key)
mstore(add(p, 32), baseSlot)
let h := keccak256(p, 64)
```

可恢复为：

```text
h = keccak256(key, baseSlot)
≈ mapping[key]
```

如果 `baseSlot` 对应 `mapping(address => uint256) balances`，则可以进一步恢复为：

```solidity
balances[key]
```

### 4.2 嵌套 mapping

典型形式：

```solidity
mstore(p, owner)
mstore(add(p, 32), baseSlot)
let h1 := keccak256(p, 64)

mstore(p, spender)
mstore(add(p, 32), h1)
let h2 := keccak256(p, 64)
```

可恢复为：

```text
h1 = keccak256(owner, baseSlot)
h2 = keccak256(spender, h1)
≈ allowance[owner][spender]
```

该步骤是恢复 ERC20 中 `balanceOf`、`allowance`、`transfer`、`transferFrom` 行为的关键。

------

## 5. 状态读取提取

需要提取所有 `sload(slot)` 操作，并判断其读取的是哪个状态变量或 mapping entry。

示例：

```solidity
let bal := sload(balanceSlot)
```

可恢复为：

```text
read balance[_from]
```

需要记录：

```text
sload 的 slot 来源
slot 是否由 keccak256 计算得到
slot 对应的状态变量
读取值是否参与条件判断
读取值是否参与状态更新
读取值是否参与事件或外部调用
```

常见状态读取类型包括：

```text
读取余额 balance[from]
读取授权 allowance[owner][spender]
读取 totalSupply
读取 owner
读取 blacklist / whitelist 标志
读取 tradingEnabled / paused 等交易控制变量
读取隐藏控制变量
```

------

## 6. 状态写入提取

需要提取所有 `sstore(slot, value)` 操作，并判断其修改了哪个状态变量。

示例：

```solidity
sstore(fromSlot, sub(fromBal, amount))
sstore(toSlot, add(toBal, amount))
```

可恢复为：

```solidity
balance[from] = balance[from] - amount;
balance[to] = balance[to] + amount;
```

需要识别的状态更新类型包括：

```text
余额减少
余额增加
授权额度设置
授权额度扣减
totalSupply 增加或减少
owner 修改
blacklist / whitelist 修改
交易开关修改
隐藏状态变量修改
```

对于 Solidity 0.8 及以上版本，需要特别注意：

```text
assembly 中的 add/sub 不具备 Solidity 高级语法中的自动溢出检查。
```

因此，汇编中的：

```solidity
sstore(slot, sub(oldValue, amount))
```

更准确地应标注为：

```solidity
unchecked {
    value = oldValue - amount;
}
```

不能直接等价为 Solidity 0.8 中的 checked subtraction。

------

## 7. 条件分支与 Revert 提取

内联汇编中常通过 `if` 和 `revert` 实现条件检查。

典型形式：

```solidity
if condition {
    revert(0, 0)
}
```

可恢复为：

```solidity
require(!condition);
```

需要识别的条件类型包括：

```text
零地址检查
余额充足检查
授权额度充足检查
owner 权限检查
黑名单检查
白名单检查
交易开关检查
最大交易额度检查
隐藏后门条件
honeypot 条件
opaque predicate
```

示例：

```solidity
if iszero(_from) {
    revert(0, 0)
}
```

可恢复为：

```solidity
require(_from != address(0));
```

示例：

```solidity
if lt(balance, amount) {
    revert(0, 0)
}
```

可恢复为：

```solidity
require(balance >= amount);
```

该步骤不仅要恢复 `require` 语义，还要分析条件是否保护了后续状态写入。

------

## 8. 事件提取

内联汇编中通过 `log1`、`log2`、`log3`、`log4` 触发事件。

ERC20 中重点关注：

```text
Transfer(address indexed from, address indexed to, uint256 value)
Approval(address indexed owner, address indexed spender, uint256 value)
OwnershipTransferred(address indexed previousOwner, address indexed newOwner)
```

典型形式：

```solidity
mstore(p, amount)
log3(p, 32, TransferTopic, from, to)
```

可恢复为：

```solidity
emit Transfer(from, to, amount);
```

需要检查：

```text
event topic 是否正确
indexed 参数是否正确
data 区域是否正确写入
mstore 和 log 的顺序是否正确
event value 是否与状态变化中的 amount 一致
是否存在伪造事件或错误事件数据
```

尤其需要注意：如果 `mstore(p, amount)` 出现在 `log3` 之后，则事件 data 中实际记录的值可能不是 `amount`。这类情况不能被简单修正为标准事件，而应标注为异常事件行为。

------

## 9. 外部调用提取

需要识别汇编中的低级调用：

```text
call
staticcall
delegatecall
callcode
```

需要记录：

```text
调用类型
调用目标地址
gas 参数
value 参数
input memory 区域
output memory 区域
返回值是否被检查
返回值是否影响条件分支
返回值是否影响状态写入
```

例如：

```solidity
staticcall(gas(), 2, inputPtr, 20, outputPtr, 32)
```

可恢复为：

```text
staticcall to address 0x02
input = memory[inputPtr : inputPtr+20]
output = memory[outputPtr : outputPtr+32]
return value affects control flow
```

如果调用目标是 EVM precompile，还需要标注其语义。例如地址 `0x02` 通常对应 SHA2-256 precompile。

外部调用需要重点判断：

```text
是否参与隐藏条件判断
是否引入外部控制
是否可能改变合约上下文
是否用于哈希、签名验证或地址判断
delegatecall 是否引入代理执行风险
```

------

## 10. 算术与比较操作恢复

需要提取汇编中的算术、位运算和比较操作。

常见操作包括：

```text
add
sub
mul
div
mod
lt
gt
eq
iszero
and
or
xor
not
shl
shr
```

重点分析这些操作是否作用于：

```text
余额
授权额度
总供应量
转账金额
storage slot
条件判断
事件数据
外部调用参数
```

示例：

```solidity
sub(balance, amount)
```

可恢复为：

```text
balance - amount
```

但同时需要标注：

```text
unchecked subtraction
```

示例：

```solidity
eq(x, and(x, x))
```

通常为恒真条件，可进一步标注为：

```text
opaque predicate candidate
```

------

## 11. Opaque Predicate 与噪声识别

混淆合约中常插入恒真、恒假或无实际语义的条件，以干扰分析。

常见形式包括：

```solidity
if eq(x, and(x, x)) {
    ...
}
if iszero(xor(basefee(), basefee())) {
    ...
}
if eq(t, mul(t, 1)) {
    ...
}
```

这类条件通常可以标注为：

```text
opaque predicate
混淆噪声
恒真条件
恒假条件
```

但需要注意：

```text
只有在确认该条件不影响关键状态写入、事件触发、外部调用或 revert 行为时，才能将其降级为噪声。
```

如果 opaque predicate 控制了 `sstore`、`log`、`call` 或 `revert`，仍然需要保留其控制依赖。

------

## 12. 数据依赖恢复

需要构建参数、内存、storage、事件、调用之间的数据流关系。

重点追踪：

```text
函数参数 → memory
memory → keccak256
keccak256 → sload/sstore
sload → arithmetic
arithmetic → sstore
参数 → event data
参数 → event indexed field
参数 → call input
call output → condition
condition → revert/state update
```

示例：

```text
_amount → sub(balance, _amount) → sstore(balanceSlot)
_amount → mstore(memory) → log3 Transfer
```

可解释为：

```text
_amount 同时参与余额扣减和 Transfer 事件。
```

数据依赖恢复的作用是判断某个状态更新、事件或条件是否真的与用户输入参数相关。

------

## 13. 控制依赖恢复

需要分析条件分支对后续操作的控制影响。

重点判断：

```text
某个 sstore 是否受 require 条件保护
某个 log 是否只在特定条件下触发
某个 call 返回值是否控制状态更新
是否存在绕过余额检查的路径
是否存在只对特定地址生效的隐藏分支
```

示例：

```solidity
if lt(balance, amount) {
    revert(0, 0)
}
sstore(balanceSlot, sub(balance, amount))
```

可恢复为：

```text
balance update is protected by balance >= amount
```

但如果存在隐藏条件绕过：

```text
if hidden_condition {
    skip balance check
}
```

则需要标注为：

```text
potential balance-check bypass
```

------

## 14. 高级语义模板归纳

在完成底层语义恢复后，需要将低层操作归纳为 Solidity-like 的高级语义模板。

常见模板包括：

```text
mapping read
mapping write
nested mapping read
nested mapping write
require nonzero address
require sufficient balance
require sufficient allowance
balance decrease
balance increase
allowance set
allowance decrease
emit Transfer
emit Approval
external call guard
unchecked arithmetic
hidden condition
opaque predicate
```

示例低层操作：

```text
SLOAD balance[from]
SSTORE balance[from] = balance[from] - amount
SSTORE balance[to] = balance[to] + amount
LOG Transfer(from, to, amount)
```

可归纳为：

```solidity
unchecked {
    balances[from] -= amount;
    balances[to] += amount;
}
emit Transfer(from, to, amount);
```

但该归纳结果应保留低层证据，避免把异常逻辑错误地美化为标准 ERC20 实现。

------

## 15. 最终输出形式

处理 assembly 内联汇编后，建议输出结构化结果，而不是只输出自然语言描述。

可以包括以下字段：

```json
{
  "contract": "Token",
  "function": "_transfer",
  "assembly_block": "source range",
  "storage_reads": [],
  "storage_writes": [],
  "slot_computations": [],
  "events": [],
  "reverts": [],
  "external_calls": [],
  "data_dependencies": [],
  "control_dependencies": [],
  "semantic_templates": [],
  "anomaly_candidates": []
}
```

其中：

```text
storage_reads：记录 sload 读取的状态变量
storage_writes：记录 sstore 修改的状态变量
slot_computations：记录 keccak256 slot 计算
events：记录 log 恢复出的事件
reverts：记录 revert 条件
external_calls：记录 call/staticcall/delegatecall
data_dependencies：记录参数、storage、event、call 之间的数据流
control_dependencies：记录条件分支对状态更新、事件、调用的控制关系
semantic_templates：记录归纳出的 Solidity-like 高级语义
anomaly_candidates：记录隐藏条件、unchecked arithmetic、opaque predicate、异常事件等
```

------

## 16. 核心工作清单

处理 Solidity 内联汇编时，需要完成以下核心工作：

```text
1. 定位 assembly 所属合约、函数和可达入口。
2. 恢复 mload/mstore 等 memory 操作。
3. 恢复 keccak256 计算出的 storage slot。
4. 提取 sload 状态读取。
5. 提取 sstore 状态写入。
6. 识别余额、授权、totalSupply 等状态变量角色。
7. 提取 if-revert 条件并恢复 require 语义。
8. 提取 log1-log4 事件并检查 topic、indexed 参数和 data。
9. 提取 call/staticcall/delegatecall 等外部调用。
10. 恢复算术、比较、位运算的高级表达。
11. 标注 unchecked arithmetic。
12. 识别 opaque predicate 和无关噪声。
13. 构建参数、memory、storage、event、call 之间的数据依赖。
14. 构建条件分支对状态更新、事件和调用的控制依赖。
15. 归纳 Solidity-like 安全核心逻辑模板。
16. 输出结构化分析结果，保留低层证据。
```

------

## 17. 一句话总结

处理 Solidity 内联汇编的关键，是恢复从 `memory` 构造到 `keccak256 slot` 计算，再到 `sload/sstore/log/revert/call` 的完整低层执行链，并将其归纳为状态变量访问、余额与授权变化、事件触发、条件检查、外部调用和隐藏控制逻辑。