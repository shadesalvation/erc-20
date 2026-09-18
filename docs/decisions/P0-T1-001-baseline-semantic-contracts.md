# P0-T1-001 — Baseline 语义契约核对

状态：采纳；范围仅为用户授权的 P0-T1 baseline 补充，不设计研究模块接口。

## 证据与决定

1. `SoliditySemanticLifter.atomic_semantic` 的 StateWrite 分支明确保留 SlithIR rvalue，防止把已执行的调用再展开为第二次调用。既有 `s_seir_slither_semantic_integration_tests.py` 已要求写入 `result_1` / `received_1`，并拒绝渲染 `total = selected(value)`。因此 `approve` 的正确值为 `value_1`，不把生产值退化成源码名 `value`。更新该旧断言，并增加最终 SFIR 唯一写入、位置一致、FactSSA 读引用指向参数 entry definition 的检查。
2. 原 `ConstantContextCase.guarded` 的最终 SFIR 已含 `BranchCondition`、真假边 `target == FIXED` / `!(target == FIXED)`、Yul 赋值的 guarded path witness。兼容 facts 上没有逐条复制的 condition 不等于 Guard 丢失。将旧的“任意 condition 文本含 FIXED”检查替换为最终 CFG 的真假分支可达性、返回汇合、唯一赋值和比较→predicate 数据边检查，不向所有 facts 人为添加条件。
3. 同一样例存在独立且真实的分类错误：Slither 的 StateIRVariable 保留 `is_constant` 声明属性，而当前 serializer 只把字面量 Constant 识别为常量，导致 FIXED 被物化为 StateRead。修正声明属性传递，让具名编译期常量不生成 storage reads 或运行时 SSA read demand；保留名称、声明身份、类型及 initializer 文本作为 `semantic.constant_operands`。不猜测或求值 initializer，不把 immutable 当编译期 constant。
4. 首个旧值断言修正后，同一 atomic_ops 测试在原本未到达的 anchor 断言暴露兼容桥接缺口：Yul lifter 已输出 `semantic_provenance.anchor_cfg_node` 并移除 effect transport，`SemanticFactBridge` 却仍仅按旧 effects 查找 sink，退回 cfg_nodes 的 slot 证据位置。优先消费已有 semantic provenance，旧 effect 查询仅作兼容 fallback；原有 endpoint 与顺序断言保持不变，另加不含 effects 的桥接回归。

## 兼容性与回归

SFIR schema 保持 `s-seir-semantic-fact-ir/v1`，新增 constant_operands 是具名常量的附加元数据；此前错误的常量 StateRead/SSA operand 被校正。SlithIR 原始 text/SSA 继续留在证据中。兼容 facts 输出恢复已有 anchor/排序契约，现行 Fact IR 的 placement 继续消费同一 provenance。已有 CFG、MemorySSA、SinkResolver 及 StateWrite 算法不变。

不删除样例，不放宽为多种可接受值，不读 expected/frozen oracle。原始 24 PASS / 2 FAIL 结果保留；新测试要求常量无 StateRead，同时要求同名可变状态仍有 StateRead 及其数据依赖。全量回归及修改前后的最终 SFIR 保存于 `docs/task_reports/P0-T1_followup/`。
