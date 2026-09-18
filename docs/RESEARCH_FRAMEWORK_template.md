# RESEARCH_FRAMEWORK

> 本文件由 P0-T3 根据实际仓库与 SFIR 能力确认后写入 `docs/research/RESEARCH_FRAMEWORK.md` 并冻结。

## 1. Research Question

给定经过源码级混淆的 Solidity / Yul 合约，不要求恢复原始源码，而是恢复其真实的状态转移语义。

## 2. Final Semantic Target

- Guard
- Persistent State / State Read
- State Update
- External Effect
- Failure
- Execution Order

## 3. End-to-End Pipeline

```text
Solidity / Yul
→ SFIR
→ Module 1: Core Semantic Control Recovery
→ Module 2: Sink-Guided State Dependency Recovery
→ Module 3: Order-Aware State Transition Reconstruction
→ Canonical STIR
→ Semantic Validation
```

## 4. Research Questions and Module Responsibilities

### Module 1
哪些关键行为能够发生，在什么条件下发生。

输出：`Feasible Semantic Control Edge + Normalized Guard`

### Module 2
这些关键行为由哪些输入、持久状态和真实数据依赖决定。

输出：`State / Sink Dependency Summary`

### Module 3
这些语义事实如何按照执行偏序组成完整状态转移。

输出：`Canonical State Transition IR`

## 5. Experiment Mapping

- Experiment 1 ↔ Module 1
- Experiment 2 ↔ Module 2
- Experiment 3 ↔ Module 3
- Phase 6 Ablations 只用于验证设计选择，不改变生产 pipeline

## 6. Evaluation Principle

- clean 与 obfuscated 两侧都经过同一 recovery pipeline
- 在 canonical semantic representation 上比较
- 不以源码文本相似度作为主指标
- 不把原始 CFG 直接当最终语义答案
- frozen oracle 不得进入 recovery pipeline

## 7. Scope

- 主体研究范围：ERC-20 风格合约及其核心持久状态语义
- 鲁棒性验证：Solidity / Yul / Mixed representation
- 不以覆盖所有合约、所有混淆模式的通用解混淆器为目标
- 较完整/现实合约只作为补充外部有效性验证，不扩大方法适用性 claim

## 8. Global Invariants

- Module 2 必须复用 Module 1 的 feasible control / Guard
- Module 3 必须消费 Module 1 + Module 2 的稳定语义事实
- Order 是最终语义的一部分，不能降成无序事件集合
- Failure / Revert / rollback 必须显式保留
- UNKNOWN / TIMEOUT / UNSUPPORTED 不得静默当作成功
- 不得通过修改 oracle、删除失败样例或放宽判定制造通过
- SFIR 是共享基础设施，不为单一 Task 另起平行 IR
- Task 必须服从 RESEARCH_FRAMEWORK，不得为了局部实现便利反向修改研究目标

## 9. Authority and Change Control

权威层级：

```text
RESEARCH_FRAMEWORK.md
    ↓
IMPLEMENTATION_PLAN.md
    ↓
Current Task Prompt
```

若当前 Task、现有代码或局部实现便利性与本文件冲突：

1. 不得自行修改研究框架；
2. 标记 `BLOCKED`；
3. 由研究人员决定是否通过 `docs/decisions/` 修改；
4. 若修改框架，必须记录原因、影响模块、影响实验，并重新审查后续 Task。
