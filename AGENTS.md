# Project Goal

本项目是 Solidity / Yul 智能合约语义解混淆研究原型。目标不是恢复原始源码，而是恢复：
Guard + Persistent State + State Update + External Effect + Execution Order。
Repository 是唯一正式项目状态来源；不依赖旧聊天记录。

# Research Scope

主体研究对象以 ERC-20 风格合约及其核心持久状态语义为主；不以覆盖所有 Solidity/Yul 合约、所有混淆模式或逐行还原源码为目标。
新增能力必须直接服务于三个研究模块、对应实验或必要的工程支撑；不得因个别复杂样例自行扩展为通用解混淆器。

# Existing Infrastructure

SFIR 是已有基础设施。除非任务明确证明有必要，不要重新设计或替换 SFIR。
当前入口为 `scripts/s_seir/s_seir_pipeline.py`；现行 SFIR 实现在 `scripts/s_seir/s_seir_semantic_fact_*`。
`scripts/legacy_yul/` 仍是运行时依赖；`scripts/废弃_sfir_pipeline未使用/` 是归档，不作为新依赖。
保持 MemorySSA 与 SinkResolver 的既有职责，优先局部扩展。

# Research Modules

1. Core Semantic Control Recovery
2. Sink-Guided State Dependency Recovery
3. Order-Aware State Transition Reconstruction

# Design Rules

- 恢复语义控制关系，不追求 source-identical CFG。
- k-switch Abstract Interpretation 负责大范围候选结构。
- Symbolic Execution + SMT 只做局部 refinement，不做无差别全程序路径爆炸式探索。
- Module 2 复用 Module 1 的 feasible control 与 Guard。
- 第一版 Semantic Sink 以 Storage Write 为主。
- Backward Slicing 同时考虑 Data Dependency 和 Control Dependency。
- Execution Order 是最终语义的一部分。
- Order 使用 partial-order constraints，不强制 total order。
- clean 与 obfuscated 都转换到 canonical semantic representation 再比较。
- 不以源码文本相似度作为主要评价指标。
- 执行顺序、到达定义和依赖依据函数级/Yul CFG、SSA、支配关系等分析，不用源码顺序或节点编号代替。
- 替代表示进入下游后，旧表示只能保留为证据，不能重复产生语义效果。

# Engineering Rules

- 修改前运行相关 baseline。优先复用现有代码。
- 一个 Task 只解决一个明确问题；添加必要测试和 task report。
- 不静默忽略 unsupported / unknown / timeout 或异常。
- 不通过修改 oracle、删除失败样例、放宽判定或吞掉异常制造“通过”。
- recovery pipeline 不得读取 expected/frozen oracle；oracle 只允许 evaluator/test 层读取。
- 冻结接口、语义或实验规则若必须改变，记录 `docs/decisions/`，同步文档并运行受影响回归。
- 当前 Task 结束前，把下一 Task 需要的重要状态写回 Repository，更新 `PROJECT_STATUS.md` 和 `docs/task_reports/`。
- 不自动开始下一 Task。仅必要测试通过、专项验收全部满足且交接完成时标记 COMPLETE，否则 PARTIAL/BLOCKED。
- 保留任务开始前工作树已有改动，不能把它们当成本 Task 改动或擅自恢复。
- `IMPLEMENTATION_PLAN.md` 在 P0-T3 创建；初始化阶段不因其缺失而阻塞。
