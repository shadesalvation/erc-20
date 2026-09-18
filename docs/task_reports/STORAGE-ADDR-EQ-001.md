# STORAGE-ADDR-EQ-001 — Storage Address Equivalence

- 日期：2026-09-18
- 状态：**PARTIAL**
- 范围：仅本问题修复准备、相关 baseline 与阻塞交接；未重新执行 P0-T2 capability review，未开展其他任务。

## 结论与输入核对

指定输入 [review](STORAGE-ADDR-EQ-001_review.md) 实际为 PARTIAL。其 Confirmed Gap 明确说明：已确认的是目标源码/测试/输出证据缺失，不是已确认的目标算法 failure。LocationNode、ExpressionArena、ValueRecord、现有 RD 及相关行为均标记 evidence-insufficient，不能据此假定其接口或实现。

本次最小确认：`ls -la scripts/semantic_ir` 仅显示 `__pycache__/`；`rg --files scripts/semantic_ir -g '*.py' -g 'AGENTS.md'` 无结果（exit 1）。Git 初始状态中该目录的 model.py、builder.py、tests.py、exporter.py、__init__.py、README.md 均已删除。未恢复这些既有删除，未反编译缓存，未使用 HEAD 或归档模型替代当前实现。

已向用户请求包含目标类型及现有 `_reaching_definitions()` 的正确源码路径或版本。取得目标之前，无法完成真实 SemanticFunction / LocationNode / ValueRecord 集成或复用其 RD。缺源码不是一个 relation 的 UNKNOWN 返回值，也不是已证实 unsupported 的算法能力。

## 本次修改文件

- `docs/task_reports/STORAGE-ADDR-EQ-001.md`：本报告。
- `docs/task_reports/STORAGE-ADDR-EQ-001_repair_evidence/baseline.json`：本次 baseline 原始命令、输出、退出码。
- `PROJECT_STATUS.md`：登记本次独立修复状态与阻塞交接。

没有修改生产实现、测试、oracle、schema、serialization、Location interning、ValueRecord public semantics 或输出格式；保留所有任务前已有改动。

## API、RD 与语义验收

以下是尚未完成的要求，不是已实现功能或已发现算法缺陷：

| 项目 | 本次结果 / 未满足项 |
| --- | --- |
| Relation API | 未实现；目标模型缺失，无法定义并验证 location、稳定 use-site ID、optional scoped context 的真实接口 |
| Access-point query/index | 未新增；无法核验目标 use-site/value 关联 |
| Existing RD reuse | 未实现；没有以另一套 RD 替代缺失实现，未扩展分析范围 |
| Persistent scope / logical semantics | 采用用户要求的同一 SemanticFunction、同次执行 persistent storage 范围；无法验证目标指令集合及结构 |
| 四态 relation | MUST_ALIAS / NO_ALIAS / MAY_ALIAS / UNKNOWN 的实现及 reason/evidence 均待目标源码；unsupported 只能是 UNKNOWN 原因 |
| Stable identity / copy / chain | evidence-insufficient；未假定当前支持或不支持，未使用同名变量、access 文本或 Location identity 代替 use-site value identity |
| Candidate pairing | 未实现完整候选及全部可行 pairing 的检查、聚合；候选不全或任一必要 pairing UNKNOWN 时不能降为 MAY_ALIAS |
| Mapping base/path / distinct storage | 未核验 normalized structure / slot-layout，不能按 base/access 文本猜测 compatibility 或 NO_ALIAS |
| Nested / multi-key | 既有构造能力仍 evidence-insufficient，不能宣称不支持；尚未验证全部必要 key 或不完整结构 UNKNOWN，不新增 recovery |
| Context applicability | 未实现；需要确认 storage instruction condition/guard 的作用域及显式 context 对左右 access 的适用性；冲突或适用不明应 UNKNOWN |
| State-derived key / value_aliases | 未核验稳定 ValueRecord、独立 StateRead、名称桥接边界；未新增 StateSSA 或追踪 |
| Read-only / construction independence | 没有 relation 可供验证，不能声称 UNKNOWN 不影响 build 的验收已通过 |
| Symmetry | 未实现、未测试 |

预期语义仍遵循用户约束：在已有证据证明 compatible 的 mapping base/path 下比较全部必要 logical keys，无须 hash collision proof；只有必要信息完整且能力范围内的未决关系才是 MAY_ALIAS。必要结构、候选、provenance、表达式或 context applicability 不足时是 UNKNOWN。此处仅记录后续实施约束，不构成实现结果。

## Production integration 与 evidence 边界

最小复读 review 已定位的 `scripts/s_seir/s_seir_semantic_fact_ir.py::_storage_location_binding` 及 `test_recovered_storage_location_has_an_exact_fact_ssa_binding`。前者按 `@storage:{access}` 建立 exact binding；该位置不是指定 LocationNode/ValueRecord 模型中的两 use-site relation query，保持不变。

目标生产源码仍缺失，因此**不能确认当前不存在 production semantic-equality consumer**，也无法接入该 consumer。没有新增无关 consumer。

尚无 relation result，故未生成或伪造 value/provenance/context evidence。现有 SFIR 测试只能证明其 exact binding 和版本引用，不能证明目标 same reaching definition、direct copy、copy chain 或稳定 value identity。没有为了细分 evidence 标签新增 provenance tracking、StateSSA 或其他分析。

## Baseline / regression results

原始输出见 [baseline.json](STORAGE-ADDR-EQ-001_repair_evidence/baseline.json)。以下测试均在本次任何文件修改前运行。

| 命令 | 修改前结果 | 归因 |
| --- | --- | --- |
| `.venv/bin/python -B -m unittest -v scripts.semantic_ir.tests` | exit 1；1 个 loader error：`ModuleNotFoundError: No module named 'scripts.semantic_ir.tests'` | 修改前已有目标源码/测试缺失，与 review 一致；不是执行了某个语义用例后失败 |
| `.venv/bin/python -B scripts/s_seir/s_seir_semantic_fact_ir_tests.py` | exit 0；27/27 PASS | 直接相关现行 SFIR baseline，不替代目标 Semantic IR 测试 |

本次修改后：仅修改交接文档并保存 baseline 输出，无生产或测试代码变动，未重复执行同一 baseline。没有已观察到的本次新引入代码 regression；目标 regression suite 仍不可运行，不能宣称修复通过或目标无 regression。

没有新增模拟替代模型的 regression tests。用户要求的 stable access、copy/chain、重定义、完整/不完整候选、MAY/UNKNOWN、Eq/Ne context、distinct storage、base/path compatibility、独立 StateRead、multi-key、read-only build 和对称性测试均未完成。

## Remaining MAY_ALIAS / UNKNOWN 与交接

目前没有可执行 relation，无法列举实际返回 MAY_ALIAS/UNKNOWN 的用例或声称其 reason 已正确实现。unsupported expression、incomplete provenance/candidates/location、unknown base/path compatibility、context conflict/applicability 等所需原因仍待实现及测试。

继续本问题所需输入：包含题述 LocationNode、ExpressionArena、ValueRecord、SemanticFunction.values、build_with_bindings、现有 `_reaching_definitions()` / `_rebuild_def_use()` 及相应测试的正确工作树或源码路径；若目标应是其他模型，需要用户明确修正任务对象，不能自行替换。

实现、production consumer 核验、全部专项 regression 及相关目标测试验收未满足，因此 **PARTIAL**。完成本次证据与状态交接后停止，不自动开始其他 Task。
