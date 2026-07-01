# Function-level Assembly 处理单元调整记录

## 背景

当前 inline assembly 处理逻辑基本以单个 `assembly { ... }` 块为独立处理单元。每个 assembly 块单独构建 AST/CFG、MemorySSA、MemoryTracker，并在块内完成 memory、slot、event、revert、external call 等语义恢复。

后续需要调整这一基础假设：

> 对于同一个函数体内的多个 assembly 块，应将整个函数体作为一个处理单元；函数体内的多个 assembly 块共享同一个 function-level memory/value 上下文。

## 调整原因

### 1. 同一函数调用内 memory 是共享的

EVM memory 属于当前调用上下文，而不是单个 inline assembly 块。一个函数体内前一个 assembly 块写入的 memory，后一个 assembly 块理论上可以继续读取或修改。

如果仍然以单个 assembly 块为单位分析，跨块 memory 数据流会被截断。

### 2. Assembly 块可能依赖 Solidity 局部变量

assembly 中可以读写 Solidity 函数作用域内定义的局部变量，例如：

```solidity
function toString(uint256 value) internal pure returns (string memory) {
    unchecked {
        uint256 length = MathUpgradeable.log10(value) + 1;
        string memory buffer = new string(length);
        uint256 ptr;
        assembly {
            ptr := add(buffer, add(32, length))
        }
        while (true) {
            ptr--;
            assembly {
                mstore8(ptr, byte(mod(value, 10), _SYMBOLS))
            }
            value /= 10;
            if (value == 0) break;
        }
        return buffer;
    }
}
```

在该例中：

- `ptr` 是 Solidity 局部变量，不是在单个 assembly 块内部声明的 Yul 变量。
- 第一个 assembly 块给 `ptr` 赋值。
- 后续 Solidity `while` 循环修改 `ptr`。
- 第二个 assembly 块继续使用当前 `ptr` 执行 `mstore8`。
- 如果只分析第二个 assembly 块，将无法知道 `ptr` 的来源和循环中的变化。

### 3. 函数体中可能存在 Solidity 语句夹在多个 assembly 块之间

多个 assembly 块之间可能存在 Solidity 语句，这些语句可能修改：

- Solidity 局部变量。
- 命名返回值。
- memory 指针变量。
- storage 状态。
- 控制流条件。

因此，简单把多个 assembly 源码拼成一个大 assembly 块并不完全正确；更合理的做法是构建 function-level IR/CFG，把 Solidity 语句和 assembly 块都放入同一个函数级控制流与数据流框架中。

## 后续实现方向

### 1. 分析单元调整

从：

```text
AssemblyBlock-level analysis
```

调整为：

```text
Function-level analysis unit
  - Solidity statements
  - InlineAssembly block 1
  - Solidity statements
  - InlineAssembly block 2
  - ...
```

### 2. Function-level 上下文需要记录

后续模块需要补充记录：

- 函数参数。
- 命名返回值。
- Solidity 局部变量声明与赋值。
- 函数体内所有 inline assembly 块。
- 每个 assembly 块的原始源码范围。
- assembly 块之间的 Solidity 语句。
- 函数级 CFG。
- function-level MemorySSA / ValueSSA。

### 3. MemorySSA 调整

当前 MemorySSA 是 assembly-block-local。

后续应升级为：

```text
function-level MemorySSA
```

但仍需保留每个 assembly 块的来源边界，用于最终替换和报告展示。

### 4. Solidity 局部变量适配

需要将 Solidity 局部变量纳入 SSA/value tracking，例如：

```solidity
uint256 ptr;
assembly { ptr := add(buffer, add(32, length)) }
ptr--;
assembly { mstore8(ptr, ...) }
```

这里 `ptr` 的定义和使用跨越 Solidity 与 Yul，需要统一建模。

### 5. 不能简单拼接 assembly 块

注意：多个 assembly 块之间可能存在 Solidity 控制流和副作用，因此不能仅将多个 `assembly {}` 内容拼成一个大 Yul 块。

更合理的方式是：

- 以函数体 AST 为入口。
- 将 Solidity 语句抽象为 function-level IR 节点。
- 将 inline assembly 内部继续展开为 Yul CFG 子图。
- 在函数级 CFG 中连接 Solidity 节点与 Yul 子图。
- 让 MemorySSA / ValueSSA 在函数级 CFG 上传播。

## 当前状态

本文件仅记录设计调整方向，当前暂不修改代码。

后续等规则补充完整后，再统一修改以下模块：

- `assembly_ast_cfg.py`
- `assembly_memory_ssa.py`
- `assembly_branch_materialization.py`
- `assembly_semantic_ir.py`
- `assembly_storage_ir.py`
- `assembly_event_ir.py`
- `assembly_external_call_ir.py`
- `assembly_recovery_pipeline.py`
