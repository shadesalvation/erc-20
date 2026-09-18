# Repository Overview — P0-T1

本记录依据当前工作树与实际测试，非历史聊天。基线日期、Git HEAD、初始脏工作树及完整目录清单见 [baseline](../task_reports/P0-T1_baseline/)。本 Task 不定义三个研究模块的算法接口。

2026-09-18 授权补充已完成：最新 **26/26 测试入口通过**，状态 COMPLETE。下文 baseline 数字保留首次扫描事实；最新结果见 [followup/final/results.json](../task_reports/P0-T1_followup/final/results.json)。语义契约及局部修复依据见 [ADR P0-T1-001](../decisions/P0-T1-001-baseline-semantic-contracts.md)。

## P0-T2 增量核对（2026-09-18）

当前主线源码/测试与首次 P0-T2 快照一致，入口与 25 个主线脚本 + 1 个归档模块的清单不变。新增仓库文档包括 `docs/research/TASK_MAP.md`（导航层，非已冻结接口）、`skills/SKILL.md`（本地工作流说明），以及 `STORAGE-ADDR-EQ-001` 独立 review/修复报告。它们不表示相关能力已经实现；指定 `scripts/semantic_ir/` 源码仍缺失，该独立事项保持 PARTIAL。本次只继续 P0-T2，按分析能力名称记录缺口，不依赖未来任务编号；接口与补足安排由 P0-T3 决策。

## 仓库目录

扫描包含隐藏目录、依赖树和历史产物，仅排除 `.git` 内部对象；初始快照共 23,302 文件、8,180 目录（含扫描时已创建的证据目录），完整路径保存于 `inventory.json.gz`。

| 目录/文件 | 职责及当前状态 |
| --- | --- |
| `scripts/s_seir/` | 当前 Python 主线：Solidity/Yul 恢复、S-SEIR、SFIR、导出、批处理及 25 个测试脚本；`fixtures/` 是 memory array Solidity 样例 |
| `scripts/legacy_yul/` | 实际在用的 Yul AST/CFG、MemorySSA、storage/call/revert/event 恢复后端；名称 legacy 不代表废弃 |
| `scripts/废弃_sfir_pipeline未使用/` | 迁移后的旧入口包装器及旧 `semantic_ir/` 模型与 unittest；不属于现行 pipeline |
| `scripts/semantic_ir/` | 仅残留 `__pycache__`，没有可维护 Python 源码，不能当作有效入口 |
| `scripts/ver 1.0/` | 早期 anchor/security/backward slicing 脚本，非当前主线 |
| `scripts/llm_assembly_recovery/` | `local_multi_agent_recovery.py`，历史 LLM 恢复驱动；本 Task 未执行外部模型实验 |
| `scripts/s_seir_demos/` | `condition_tree_demo.py` 展示脚本 |
| `tests/fixtures/` | 7 个 Solidity fixture（Slither 操作/控制/继承/builtin、复杂 SFIR、高层语义、地址 alias）；根 tests 没有测试运行器 |
| `人工构造样例/` | 01–10 语法与语义样例：布局、ERC-20、继承、类型、控制流、ABI/call、内联 Yul、独立 Yul、mapping、重 Yul；含 README/部分 DOT |
| `TOKENS/` | 实际 ERC-20 风格样本与导入依赖；含 `assembly样本/`、部分历史 SlithIR/CFG 输出，不是冻结 benchmark oracle |
| `outputs/` | 既有运行输出、手工案例、批处理 JSON、CFG/DOT/PNG、历史回归目录、编译器缓存与 npm 风格源码依赖；不等于当前测试结果 |
| `outputs/*solc_cache/` | 缓存 native solc，可复用，不应自动覆盖 |
| `outputs/*deps/node_modules/` | OpenZeppelin contracts/upgradeable 和 Solady 源码 checkout，含各自上游测试/配置/锁文件 |
| `outputs/_sample10_slither_ssa_cfg/` | 另有 `export_slither_ssa_cfg.py` 分析导出脚本 |
| `codex_assembly_recovery_pack/` | 2 个手工 LLM 内联汇编恢复任务、输入和恢复产物 |
| `codex_sseir_direct_rewrite_pack/` | 2 个 direct rewrite 提示词实验及样本；历史目标不替代当前研究边界 |
| `docs/` | 原有 `RESEARCH_FRAMEWORK_template.md` 是待 P0-T3 确认的模板；本 Task 新建 architecture/task_reports/decisions |
| `docs_old/` | 31 篇历史 SFIR/S-SEIR、memory、CFG、slot、实现记录；可能含过时命令，须核对代码 |
| `backups/` | sfir-v0.1.4/v0.1.5 压缩归档，不运行或恢复 |
| `build_ast/`, `Token.ast.pretty.json` | 历史 solc AST 输出 |
| `.venv/` | 当前 Python 环境，非依赖锁；含第三方库自带测试 |
| `.git/`, `.gitignore` | 版本管理；开始时已有删除和未跟踪迁移，见初始快照 |
| `solidity语法.md`, `prompt.txt`, `提示词缓存.txt` | 参考资料/历史提示词，不是正式任务状态 |
| `BranchMemorySSAn`, `InlineAssembly`, `memory`, `solc`, `storage` | 根目录零字节残留文件；根 `solc` 不是可执行编译器 |

`outputs/` 的主要分组包括 `*_cases`（storage/event/call/arithmetic/branch/memory/pipeline）、`_sample10_*`、`_semantic_ir_*`、`semantic_fact_*`、`assembly样本_sseir*`、`人工构造样例_sseir*`、`batch_sseir*` 及 `*.llm_recovery*`；完整子目录不省略地保存在 inventory。

## 当前入口和数据流

`pipeline.main → build_sseir → solc AST / SourceStatementCollector → ControlBuilder → SolidityAtomicOperationExtractor + MemorySSA → EffectLifter / overlays / normalizers → semantic fact adapter → SemanticFactIRBridge → JSON/text/Fact CFG`。

| 能力 | 实际入口（以下路径相对 scripts） |
| --- | --- |
| 主命令/API | `s_seir/s_seir_pipeline.py: main, build_sseir` |
| 批处理/依赖/编译器选择 | `s_seir/s_seir_batch_contracts.py: main`；默认输入 TOKENS，支持指定 solc、缓存及 no-install-deps |
| Solidity frontend | `legacy_yul/assembly_ast_cfg.py: discover_solc, compile_source_ast` 发出 Solidity standard-json；`s_seir/s_seir_source_collector.py: SourceStatementCollector` 收集函数/语句；`s_seir_control_builder.py: ControlBuilder` 调 Slither |
| Solidity SSA 与语义 | `s_seir_solidity_atomic_ops.py: SolidityAtomicOperationExtractor`；`s_seir_solidity_semantic_lifter.py: SoliditySemanticLifter` |
| Yul frontend/CFG | `legacy_yul/assembly_ast_cfg.py` 提取 Solidity InlineAssembly 的 Yul AST、构造局部 CFG；`s_seir_yul_semantic_lifter.py: YulSemanticLifter` 及 `s_seir_yul_local_function_lifter.py` |
| S-SEIR 中间对象 | `s_seir/s_seir_model.py`：FunctionUnit、FunctionSSEIR、effects/overlays 等；不是最终 SFIR |
| 最终 SFIR | `s_seir_semantic_fact_adapter.py: build_function_level_semantic_fact_ir_payload` → `s_seir_semantic_fact_ir.py: SemanticFactIRBridge`；schema=`s-seir-semantic-fact-ir/v1`，独立函数 Fact CFG、FactSSA、typed edges |
| 兼容 facts/bridge | `s_seir_semantic_fact_adapter.py: build_function_level_semantic_fact_payload`、`s_seir_semantic_fact_bridge.py: SemanticFactBridge`；不可和最终 SFIR 混淆 |
| CFG/顺序 | `s_seir_control_builder.py` 整合函数控制与 Yul 边界；`s_seir_semantic_overlay_provenance.py` 提供语义 provenance；`s_seir_yul_eval_order.py`；最终 SFIR 表达 Fact CFG partial order 与块内顺序 |
| 表达式/类型 | `s_seir_expr_roles.py`、`s_seir_type_env.py`、`s_seir_yul_normalize.py`、`s_seir_predicate_lifter.py`、`legacy_yul/assembly_arithmetic_compare_ir.py` |
| Memory/依赖查询 | `legacy_yul/assembly_memory_ssa.py`、`assembly_cfg_memory_adapter.py`；`s_seir_memory_ssa.py` 函数级桥接；`s_seir_sink_resolver.py: SinkResolver` 消费查询 |
| Storage | `legacy_yul/assembly_storage_ir.py`；`s_seir_storage_layout.py` 获取编译器布局；effect/overlay/semantic lifter 生成 StateRead/StateWrite |
| Call | `legacy_yul/assembly_external_call_ir.py`，SinkResolver 和 overlays 处理 calldata、返回 memory、precompile；Solidity lifter 处理高层调用 |
| Revert/Guard | `legacy_yul/assembly_condition_revert_ir.py`，`s_seir_predicate_lifter.py`，`s_seir_condition_storage_revert_tests.py`；零长 payload 与未解析值有专门测试 |
| Event | `legacy_yul/assembly_event_ir.py`（事件声明、Keccak topic、log 恢复）、overlay 与 Solidity lifter |
| 输出/检查 | `s_seir_semantic_fact_render.py`、`s_seir_cfg_export.py`、`s_seir_solidity_like_export.py`、`s_seir_inspect_functions.py`、`s_seir_llm_assembly_export.py` |

以上表中未写目录前缀的 `s_seir_*.py` 均位于 `scripts/s_seir/`。
独立 `MiniERC20.yul` 样例存在，但当前 `compile_source_ast` 固定 `language=Solidity`，未找到独立 Yul object → SFIR 入口。不能将内联 Yul 支持等同于独立 Yul frontend 完成。

SFIR 保留既有边界：只消费已提升 Solidity 语义和完成的 Yul overlays；底层 effects、MemorySSA、SinkResolver 保留在恢复侧作为证据。本 Task 未冻结新接口或重设计 IR。

## 语言、构建与工具链

- 主实现 Python，输入 Solidity/内联 Yul，产物 JSON/text/DOT。脚本直接运行，无根级 pyproject/requirements/package.json/Makefile/CI 测试配置，也没有项目级依赖锁。
- 实测 Python 3.12.3；`.venv/pyvenv.cfg` 记录 uv 0.11.18，当前 uv 同版本；`.venv` 无 pip、pytest。依赖版本由 importlib.metadata 全量记录，未安装/升级任何依赖。
- Slither 0.11.5、crytic-compile 0.3.11、solc-select Python 包 1.2.0；`.venv/bin/solc` 是 solc-select 包装器，实际当前编译器为 `0.8.28+commit.7893614a.Linux.g++`。`.venv/bin/solc-select` CLI 文件缺失，不能从包版本推断 CLI 可用。
- 缓存 solc：0.6.2、0.6.6、0.8.4、0.8.6、0.8.20、0.8.28；每个二进制实测版本与 SHA-256 见 environment.json。
- 两套 Solidity 依赖目录中 OpenZeppelin 版本与 Solady 版本、6 份 package-lock SHA-256/lockfileVersion 已记录；OpenZeppelin lockfileVersion=3、Solady=2。这些上游锁文件不能代替本项目 Python 锁。
- 当前源码未发现 SMT solver 导入/执行，环境无 z3-solver 包；不虚构 SMT 版本或声称完成 SMT baseline。
- 编译器优先级：显式参数 → SOLC_BIN → `.venv/bin/solc` → PATH；include/remappings 与 SSEIR_SLITHER_TIMEOUT 会影响结果，实际相关环境值见 environment.json。Slither 内部默认 45 秒，stack-too-deep 可重试 via-ir/optimize，并在 control notes 记录错误/降级。

## 测试入口、命令与首次 baseline

从仓库根目录执行，禁用 Python `-O`（断言就是测试）：

```bash
.venv/bin/python docs/task_reports/P0-T1_baseline/run_baseline.py /tmp/p0-t1-rerun
.venv/bin/python scripts/s_seir/s_seir_semantic_fact_ir_tests.py
.venv/bin/python scripts/s_seir/s_seir_control_builder_tests.py
.venv/bin/python scripts/s_seir/s_seir_sink_resolver_tests.py
.venv/bin/python -m unittest -v scripts.废弃_sfir_pipeline未使用.semantic_ir.tests
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/task_reports/P0-T1_baseline/diagnose_failures.py
.venv/bin/python scripts/s_seir/s_seir_pipeline.py --help
.venv/bin/python scripts/s_seir/s_seir_batch_contracts.py --help
```

自动 runner 精确枚举全部 25 个当前 `*tests.py`（每个有直接入口）与 1 个归档 unittest 模块；逐入口命令/耗时/退出码/日志见 results.json。根 tests 只有 fixture；不应使用默认 unittest/pytest discovery 得出“零测试通过”。现行脚本是普通 assert/main/run，部分一次 run 包含多个断言，不能把脚本数或 PASS 打印行数宣称为独立用例数。

| 范围 | 入口数 | PASS | FAIL | SKIP | TIMEOUT |
| --- | ---: | ---: | ---: | ---: | ---: |
| 当前主线 | 25 | 23 | 2 | 0 | 0 |
| 归档 Semantic IR | 1 | 1 | 0 | 0 | 0 |
| 合计 | 26 | 24 | 2 | 0 | 0 |

归档模块内部另有 5 个 unittest 用例，全部通过。失败入口在首个 assertion 停止，后续断言未到达，不能计为通过或框架 skip。

1. `s_seir_solidity_atomic_ops_tests.py:97`：期待 `semantic.value == "value"`，实际 `value_1`；`s_seir_solidity_semantic_lifter.py` StateWrite 分支明确保留 SlithIR SSA rvalue。属于当前实现与测试预期冲突，未自行裁定或改 oracle。
2. `s_seir_solidity_slithir_tests.py:206`：期待 guarded facts 的 `condition` 包含 FIXED；实际有 `target_1 == FIXED_1` 的 ValueCompute 及 BranchCondition，相关 facts 无 condition。具体语义契约正确性尚待后续获准任务处理。

首次两项原样重跑仍失败，完整实际 facts 见 failure_diagnostics.json。授权补充已解决这两项冲突及首个失败后未到达的 anchor 回归；最新同一 26 入口全部通过。覆盖 CFG、MemorySSA/SinkResolver、storage/revert/event/call、Yul local function、Slither 集成、SFIR/FactSSA 和渲染。不把入口通过解释为所有 unsupported 情况已解决。

本次补充保持 StateWrite 的 SSA rvalue，并以最终 FactSSA 验证其参数来源；Guard 检查移到最终 Fact CFG 的真假边和 path witness。具名常量由 Slither 声明属性识别，不再生成 StateRead/运行时 SSA 读取，`semantic.constant_operands` 保存声明及 initializer 文本。兼容 `SemanticFactBridge` 优先读取已有 `semantic_provenance.anchor_cfg_node`，恢复 endpoint 顺序；CFG 的计算及 MemorySSA/SinkResolver 未变。

上游附带测试目录均在 inventory 的 test_directories 中列出：`.venv` 下 5 个库测试目录属已安装包测试，不是项目入口；outputs 下两套 OpenZeppelin 的 Hardhat/Foundry 测试、Solady Foundry 测试共 6 个根目录。它们不是本项目回归；依赖 checkout 无自己的 node_modules，forge 未安装，未安装上游开发依赖/执行上游套件。这些是显式排除的第三方范围，不混入项目 SKIP=0。测试范围机器可读说明见 scope.json。

## Benchmark、文档和限制

未找到独立的冻结 benchmark manifest、统一 evaluator、配对 oracle 或根级 benchmark runner；已有人工矩阵、TOKENS、批处理脚本、输出快照和 LLM pack。它们可作为后续研究素材，但本 Task 不生成评价规则、不运行全 TOKENS 实验、不宣称语义等价率。

初始 Git 状态中 31 篇 docs 文档与旧顶层脚本被删除，并出现 docs_old 和废弃目录未跟踪内容。以磁盘源码为准保留现状；Git HEAD 单独不能复现当前工作树，须保留初始状态及 source hashes。历史 docs 命令应替换为本页核实后的入口。

首次两项失败曾阻止 COMPLETE；授权补充完成后为 COMPLETE，原失败证据仍保留。未实施正式研究框架/实施计划（P0-T3），未设计 Module 1/2/3 接口。独立 Yul 缺口、依赖未锁定及未实现常量 initializer 求值仍为限制。
