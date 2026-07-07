# Codex Assembly Recovery Pack

这个目录用于把 S-SEIR 输出交给 Codex/LLM，直接尝试恢复 Solidity 源码中的 inline assembly。

当前工作方式是一阶段恢复：

1. 输入原始 `Token.sol`。
2. 输入对应的 `sseir_assembly_compact.json`。
3. Codex 根据 S-SEIR 中的 function context、assembly block、effect、semantic overlay 直接恢复 assembly 部分。
4. 输出完整的恢复后 Solidity 文件。

## 文件结构

```text
codex_assembly_recovery_pack/
  README.md
  prompt_one_step_recovery.md
  task_template.md
  samples/
    0xc7ecc47c0079444cf7dd882130dcdc232ccf9867/
      Token.sol
      sseir_assembly_compact.json
      sseir_full.json
    0x0068e979c72bbb31373ea8cb47eaefb44978566e/
      Token.sol
      sseir_assembly_compact.json
      sseir_full.json
```

## 推荐使用方式

把 `task_template.md` 的内容发给 Codex，并替换其中的样例路径。

如果上下文足够，优先提供：

- `Token.sol`
- `sseir_assembly_compact.json`

如果 Codex 需要更完整的底层证据，再补充：

- `sseir_full.json`

## 核心原则

- 只恢复 inline assembly 相关部分，不重写无关 Solidity 代码。
- 能无损恢复为 Solidity 高级语句时才替换 assembly。
- 无法无损恢复时保留 assembly，并可在周围补充必要的 Solidity 语义。
- 禁止把 Yul 内建函数直接写到 assembly 外部。
- 禁止让 assembly 内部的 `let` 变量逃逸到 Solidity 代码中。
- 禁止重复副作用，例如保留含 `sstore` 的 assembly 后又额外写一次状态变量。

