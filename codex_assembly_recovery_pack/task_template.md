# Task Template For Codex

请读取以下文件，并根据 S-SEIR 语义模型恢复 Solidity 源码中的 inline assembly 部分。

## 输入文件

原始源码：

```text
codex_assembly_recovery_pack/samples/0xc7ecc47c0079444cf7dd882130dcdc232ccf9867/Token.sol
```

S-SEIR compact 语义模型：

```text
codex_assembly_recovery_pack/samples/0xc7ecc47c0079444cf7dd882130dcdc232ccf9867/sseir_assembly_compact.json
```

如 compact 信息不足，可继续参考完整 S-SEIR：

```text
codex_assembly_recovery_pack/samples/0xc7ecc47c0079444cf7dd882130dcdc232ccf9867/sseir_full.json
```

## 恢复要求

使用下面提示词中的所有规则：

```text
codex_assembly_recovery_pack/prompt_one_step_recovery.md
```

请直接输出完整恢复后的 Solidity 源码文件。

