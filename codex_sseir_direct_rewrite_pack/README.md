# Codex S-SEIR Direct Rewrite Pack

这个目录用于测试更激进的一步式恢复策略：

```text
不再判断 assembly 是否可恢复。
直接按照 S-SEIR 中记录的语义，重写 Solidity 源码中的 assembly 部分。
```

旧目录 `codex_assembly_recovery_pack/` 保持不动。这个新目录用于单独测试 direct rewrite 提示词。

## 文件结构

```text
codex_sseir_direct_rewrite_pack/
  README.md
  prompt_direct_sseir_rewrite.md
  task_template.md
  task_0xc7ecc47c0079444cf7dd882130dcdc232ccf9867.md
  task_0x0068e979c72bbb31373ea8cb47eaefb44978566e.md
  samples/
    <address>/
      Token.sol
      sseir_assembly_compact.json
      sseir_full.json
```

## 使用方式

把某个 `task_*.md` 的内容发给 Codex，并要求它读取同目录下的提示词和样例文件。

推荐优先使用 compact 文件：

```text
sseir_assembly_compact.json
```

如果需要更多底层证据，再读取：

```text
sseir_full.json
```

## 输出目标

输出完整的 Solidity 源码文件。

这一次不要求模型先判断能否恢复，而是要求模型以 S-SEIR 语义为核心，将 assembly 块直接重写为 Solidity / Solidity-like 高级语句。只有在完全无法表达某段语义时，才保留最小必要 assembly。

