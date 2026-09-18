# Project Status

## Current Task

**P0-T2 — SFIR Capability Map：COMPLETE（修订要求增量收尾，2026-09-18）**。

保留原 20 项语义表示、7 项基础分析与三个 Module 的代码/测试盘点，补齐 **Feasible Control / Guard、Definition Flow、Value Flow、Sink Dependency、Transition Reconstruction** 五类能力，以及 alias 对四类状态依赖分析的具体影响和 G1–G8 最小补足边界。未实现缺失功能，未改变现行接口、实验契约或 oracle。

按用户修订要求，盘点不再依赖未来 Task 编号；能力不足本身是有效结论，不是盘点失败。专项验收全部 PASS（不适用项 N/A），必要测试通过，无未解释 regression，交接已落盘。P0-T1 仍为 COMPLETE。

仓库主线源码与先前 P0-T2 快照一致，Repository overview 已补记后增的 Task Map、本地 skills 与独立 review/修复记录。既有工作树删除、迁移、源码/测试修改及其他未跟踪文件均保留。

## Test Baseline

本轮修改前及收尾均 **26 PASS / 0 FAIL / 0 SKIP / 0 TIMEOUT**：25 个当前主线脚本 + 1 个归档模块（内部 5 个 unittest），计数单位为入口。

只读能力探针 **12/12 PASS**：原 6 项能力观察 + 新增 6 种 sink 的边关系观察；收尾重跑输出一致。PASS 表示缺口被复现，不表示缺失分析已实现或语义等价验证通过。

- [baseline](docs/task_reports/P0-T2_revision_evidence/baseline/results.json)、[final](docs/task_reports/P0-T2_revision_evidence/final/results.json)
- [基础能力探针](docs/task_reports/P0-T2_revision_evidence/capability_probes.json)、[sink 影响探针](docs/task_reports/P0-T2_revision_evidence/sink_impacts.json)
- [专项验收](docs/task_reports/P0-T2_revision_evidence/acceptance.json)、[范围及文档核对](docs/task_reports/P0-T2_revision_evidence/verification.json)

复现（仓库根）：

```bash
.venv/bin/python docs/task_reports/P0-T1_baseline/run_baseline.py /tmp/p0-t2-rerun
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/task_reports/P0-T2_revision_evidence/probe_sink_impacts.py /tmp/p0-t2-probes
```

旧 `P0-T2_evidence/` 的 PARTIAL 验收为原请求的历史结果；本轮验收见 `P0-T2_revision_evidence/`，旧结果未覆盖。

## Stable Artifacts / Existing Interfaces

- [SFIR capability map](docs/architecture/sfir_capability_map.md)：20 项语义、7 项分析、五类后续能力、alias 四类影响及 G1–G8 最小补足边界。
- [P0-T2 报告](docs/task_reports/P0-T2.md)、[repository overview](docs/architecture/repository_overview.md)、[P0-T1 报告](docs/task_reports/P0-T1.md)、[AGENTS](AGENTS.md)。
- `scripts/s_seir/s_seir_pipeline.py:build_sseir`、`SemanticFactIRBridge`、schema=`s-seir-semantic-fact-ir/v1` 不变；没有新增冻结承诺。Fact CFG、FactSSA、RD、StateRead/StateWrite/location 继续复用。
- MemorySSA/SinkResolver 保持恢复层职责；P0-T1 SSA StateWrite、constant_operands 及 CFG anchor 修复仍通过回归。
- P0-T3 尚未冻结新接口/实验契约；`docs/research/TASK_MAP.md` 为导航，不作为本次编号验收或实现授权。

## Known Limitations

- Storage Address Equivalence **不足以完整支撑 ERC-20 状态依赖**。final storage identity 主要按 access 文本；key copy、key 重定义、同址参数、unknown slot/write 与 call clobber 边界仍有缺口。
- 影响已明确映射到 persistent Reaching Definitions、Storage Load/Store Value Flow Graph、StorageWrite backward slicing、Multi-Sink persistent-state dependency；没有因这些缺口修改算法。
- msg.sender/msg.value 有语义表达式但无专门 FactSSA 环境 read refs；空 refs 不等于无依赖。
- 有结构控制、符号条件及有限 opaque normalization；没有完整 feasible-control/局部 SMT、sink-guided slicing 或 Canonical STIR/evaluator。solver UNKNOWN/TIMEOUT 测试 N/A，不能宣称通过。
- 跨块融合有 CFG 依据；Yul 块内 source-position/semantic_id tie 回退不构成完整顺序证明。无出口 CFG/postdom 及 unknown/opaque/truncation/fallback 的最终诊断传播仍有边界。
- 独立 Yul object→SFIR 入口缺失；依赖锁/统一构建/CI 缺失。常量元数据不作通用求值；恢复层有限 slot 常量运算不等于 symbolic engine。没有新冻结 benchmark，未运行全 TOKENS 或宣称等价率。

## Blocked Issues

**P0-T2 无当前阻塞。** 原编号级验收要求已被本次用户修订替代；缺口及最低证据要求已完整记录，补足方案交 P0-T3。

其他历史事项 **STORAGE-ADDR-EQ-001** 的 [独立 review](docs/task_reports/STORAGE-ADDR-EQ-001_review.md) 与 [修复准备](docs/task_reports/STORAGE-ADDR-EQ-001.md) 仍为 PARTIAL：其指定 LocationNode/ExpressionArena/ValueRecord 模型源码缺失，历史目标测试 ModuleNotFoundError 仍未解决。它不是当前 SFIR 入口，不在本轮重新执行或豁免为 PASS，也不据现行 FactSSA 探针裁定该缺失模型的能力。相关报告原样保留；继续该独立问题需要单独授权和正确源码。

## Next Allowed Task

**等待用户授权 P0-T3，消费本次能力图与缺口边界后冻结安排。** P0-T2 已完成；本次不自动开始 P0-T3，不顺手实现 alias 或其他缺失能力。
