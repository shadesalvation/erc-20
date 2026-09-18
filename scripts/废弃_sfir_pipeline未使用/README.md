# 废弃：SFIR Pipeline 未使用脚本

本目录保存当前 `scripts/s_seir/s_seir_pipeline.py` 的运行时依赖图**不会加载**的兼容层脚本。

- `assembly_*.py`：旧的顶层入口包装器；当前 pipeline 直接从 `scripts/legacy_yul/` 加载对应的 Yul 分析模块。
- `s_seir_*.py`：旧的顶层入口包装器；当前入口直接从 `scripts/s_seir/` 加载对应的 S-SEIR/SFIR 模块。
- `semantic_ir/`：已由 `s_seir_semantic_fact_*` 实现替代的旧 Semantic IR 对象模型；现行 pipeline 不会导入或输出它。

它们仅为历史命令路径保留，已经不属于当前 pipeline。请使用：

```bash
python scripts/s_seir/s_seir_pipeline.py <source.sol>
```

本目录中的脚本不应作为新功能的依赖或入口；如需恢复历史调用，请显式迁回并重新验证依赖关系。
