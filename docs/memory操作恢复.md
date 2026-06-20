# Memory 操作恢复

## 目标

对每个 `assembly { ... }` 块单独恢复 memory 操作语义，追踪 `mstore`、`mload`、`keccak256`、`log`、`call`、`copy` 等指令之间的数据关系，为后续 storage slot、事件、外部调用和 Solidity-like 代码恢复提供输入。

该过程只分析源码，不修改源码，不动态执行合约。

## 基本思路

每个 assembly block 独立维护一个 `MemoryTracker`，可理解为该 block 内部的虚拟内存区：

```text
assembly block #0 -> MemoryTracker #0
assembly block #1 -> MemoryTracker #1
assembly block #2 -> MemoryTracker #2
```

不同 assembly block 之间不共享 memory 状态。即使它们位于同一个函数内，后一个 block 也不会继承前一个 block 的 `mstore` 结果。

## MemoryTracker 记录内容

MemoryTracker 按如下形式记录内存写入：

```text
base + offset / size -> value or source
```

示例：

```solidity
mstore(p, _from)
mstore(add(p, 32), 0)
```

记录为：

```text
mem[p + 0]  = _from
mem[p + 32] = 0
```

对于动态 offset：

```solidity
mstore(add(p, off), value)
```

记录为：

```text
mem[p + off] = value
```

不会把 `add(p, off)` 错误地当作新的 memory base。

## keccak256 输入恢复

当遇到：

```solidity
let h := keccak256(p, 64)
```

MemoryTracker 会读取：

```text
mem[p + 0]
mem[p + 32]
```

如果此前有：

```solidity
mstore(p, _from)
mstore(add(p, 32), 0)
```

则恢复为：

```text
h = keccak256(_from, 0)
```

这是后续识别 mapping slot 的基础。

## copy 类指令

以下指令也视为 memory 写入：

```text
calldatacopy
codecopy
returndatacopy
```

示例：

```solidity
calldatacopy(add(p, 96), 4, 64)
```

记录为：

```text
mem[p + 96 : p + 160] = calldata[4 : 68]
```

后续如果 `keccak256(p, len)` 读取到该区域，会恢复为对应的 calldata/code/returndata 来源。

## 循环和动态写入

对于循环中的 memory 写入，例如：

```solidity
for { let i := 0 } lt(i, 3) { i := add(i, 1) } {
    mstore(add(p, mul(i, 32)), value)
}
```

记录为符号写入：

```text
mem[p + mul(i, 32)] = value
ControlPath: for lt(i, 3)
```

如果后续读取固定 offset，且可能受到动态写入影响，当前阶段不强行具体化，而是标记为候选影响源：

```text
unknown candidates=[mul(i, 32): value]
```

这样可以避免错误猜测，同时保留后续符号分析或循环展开的入口。

## 输出形式

恢复结果写入类 SlithIR-SSA 的 txt IR 中，典型形式：

```text
MEMORY_WRITE: MSTORE mem[p + 0] := _from
MEMORY_WRITE: MSTORE mem[p + 32] := 0
MEMORY_HASH: h := KECCAK256 p len 64
    ResolvedInputs: [0: _from, 32: 0]
    SolidityLike: h = keccak256(_from, 0);
```

该 IR 是语义中间表示，不用于编译，只用于后续逐步替换和恢复 assembly 语义。
