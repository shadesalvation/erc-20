# Semantic Fact 到 Semantic IR：核心设计总结

## 1. 总体路线

当前 `Semantic Fact` 已经完成了 Solidity / Yul 的统一语义提升，因此下一步可以直接设计 `Semantic IR`。

```text
Solidity / Yul
      ↓
Unified Semantic Facts
      ↓
Semantic IR Construction
      ↓
Semantic IR
      ↓
Deobfuscation
      ↓
Deobfuscated Semantic IR
      ↓
Source Reconstruction
      ↓
Solidity
```

不需要再人为插入一层新的低层 Core IR。

---

## 2. Semantic Fact 与 Semantic IR 的关系

### Semantic Fact

更偏向分析结果：

- 每个 fact 独立记录一个语义事实；
- 已经包含 `StateRead`、`StateWrite`、`Require`、`EventEmit`、`ExternalCall`、`Return` 等高级语义；
- 同时包含 `reads / writes / condition / cfg_nodes / control_predecessors` 等关系；
- 适合查询、验证和溯源。

### Semantic IR

更偏向程序变换：

- 把独立 fact 重新组织成完整函数；
- 显式表示 BasicBlock、CFG、表达式和数据依赖；
- 支持 rewrite；
- 直接作为后续解混淆对象。

因此更准确的关系是：

```text
Semantic Facts
      ↓ materialization / IR construction
Semantic IR
```

而不是再次做一次 semantic lifting。

---

## 3. Semantic IR 第一版建议包含的核心对象

```text
Function
BasicBlock
Instruction
Expression
Location
CFG Edge
Def-Use
Origin Facts
```

整体结构：

```text
Function
 ├── BasicBlock
 │    ├── SemanticInstruction
 │    └── Terminator
 ├── CFG Edges
 └── Def-Use
```

---

## 4. 不应采用“一条 Fact = 一条 IR Instruction”

当前多个 fact 可能只是同一高级操作的不同语义侧面。

例如：

```text
ValueCompute:
    result = sload(keccak256(...))

StorageLocationResolve:
    keccak256(...) -> _balances[account]

StateRead:
    result = _balances[account]
```

Semantic IR 中应融合为：

```text
result = StateRead(
    _balances[account]
)
```

并保留：

```text
origin_facts = [
    fact_x,
    fact_y,
    fact_z
]
```

因此 IR Builder 需要先做：

```text
Fact Grouping / Fact Fusion
```

推荐原则：

```text
Effect / Control Fact
        ↓
IR Statement

Support Fact
        ↓
Expression / Location / Annotation / Evidence
```

---

## 5. Statement IR

第一版可以支持：

```text
Assign

StateRead
StateWrite

Require
EventEmit
ExternalCall

Branch
Goto

Return
Revert
```

例如：

```text
fromBalance =
    StateRead(
        _balances[msg.sender]
    )
```

```text
StateWrite(
    _balances[msg.sender],
    fromBalance - value
)
```

---

## 6. Expression IR

将 `ValueCompute / EvaluationStep` 组织成表达式树或 DAG。

第一版可以支持：

```text
Constant
Variable

Add
Sub
Mul
Div
Mod

Eq
Ne
Lt
Gt
Le
Ge

And
Or
Xor
Not
IsZero

Keccak
```

例如：

```text
iszero(eq(currentAllowance, not(0)))
```

可以表示为：

```text
IsZero(
    Eq(
        currentAllowance,
        Not(Const(0))
    )
)
```

后续解混淆 / 归一化：

```text
Not(Const(0))
    ↓
UINT256_MAX
```

最终：

```text
currentAllowance != UINT256_MAX
```

Expression IR 将直接服务于：

- Constant Folding
- Algebraic Simplification
- MBA Simplification
- Opaque Predicate Analysis

---

## 7. Location IR

状态位置不建议只保留字符串，而应作为一等 IR 对象。

例如：

```text
MappingLocation {
    base = _balances
    keys = [msg.sender]
}
```

二维 mapping：

```text
MappingLocation {
    base = _allowances
    keys = [
        from,
        msg.sender
    ]
}
```

后续可以扩展：

```text
StateVariableLocation
MappingLocation
ArrayLocation
StructFieldLocation
RawStorageLocation
```

---

## 8. 控制流采用 BasicBlock + Branch/Goto

第一版 Semantic IR 不需要直接恢复成 `If / While / For`。

因为混淆程序的 CFG 可能已经被平坦化或重排，`BasicBlock + CFG` 更适合解混淆。

### If 示例

原始逻辑：

```solidity
if (x > 10) {
    y = x + 1;
} else {
    y = x - 1;
}

return y;
```

Semantic IR：

```text
BB_entry:
    cond = Gt(x, 10)
    Branch(cond, BB_true, BB_false)

BB_true:
    y = Add(x, 1)
    Goto(BB_merge)

BB_false:
    y = Sub(x, 1)
    Goto(BB_merge)

BB_merge:
    Return(y)
```

---

## 9. Loop 示例

原始逻辑：

```solidity
uint256 i = 0;
uint256 result = 0;

while (i < n) {
    result += i;
    i++;
}

return result;
```

Semantic IR：

```text
BB_entry:
    i = 0
    result = 0
    Goto(BB_loop_header)

BB_loop_header:
    cond = Lt(i, n)

    Branch(
        cond,
        BB_loop_body,
        BB_loop_exit
    )

BB_loop_body:
    result = Add(result, i)
    i = Add(i, 1)

    Goto(BB_loop_header)

BB_loop_exit:
    Return(result)
```

其中：

```text
BB_loop_body -> BB_loop_header
```

形成 back edge，可用于后续：

- loop detection
- induction-variable analysis
- loop simplification
- control-flow deobfuscation

---

## 10. CFG 是内部程序结构，不是展示格式

`BB0 / BB1 / Branch / Goto` 主要服务于算法，而不是为了人工阅读。

内部真实结构可以是：

```text
BasicBlock BB0
    instructions = [...]
    terminator = Branch(...)

BB0.successors = [BB1, BB2]
```

解混淆算法直接修改这些对象。

例如 opaque predicate 被证明恒真：

```text
Before:

BB0
 ├── true  -> BB_real
 └── false -> BB_fake
```

可以变成：

```text
After:

BB0 -> BB_real
```

并删除：

```text
BB_fake
```

因此当前不需要专门设计 C-like Pseudocode IR。

如果未来调试需要，可以再提供简单的 pretty-printer，但它只是展示层。

---

## 11. 第一版不必强制完整 SSA

当前 Semantic Fact 已经有：

```text
reads
writes
```

以及大量临时 value。

第一版只要建立明确的：

```text
ValueId
+
Definition -> Uses
```

即可。

例如：

```text
v1 = StateRead(_balances[msg.sender])

v2 = Lt(v1, value)

Require(Not(v2))

v3 = Sub(v1, value)

StateWrite(
    _balances[msg.sender],
    v3
)
```

如果后续处理复杂循环、控制流平坦化、opaque predicate 等需要更强的数据流分析，再引入：

```text
SSA
Phi
```

即可。

---

## 12. 一个完整的 Semantic IR 示例

```text
Function transfer(to, value) -> bool

BB0:
    Require(
        Ne(
            to,
            address(0)
        )
    )

    Goto(BB1)

BB1:
    fromBalance =
        StateRead(
            MappingLocation(
                base = _balances,
                keys = [msg.sender]
            )
        )

    Require(
        Ge(
            fromBalance,
            value
        )
    )

    Goto(BB2)

BB2:
    newFromBalance =
        Sub(
            fromBalance,
            value
        )

    StateWrite(
        MappingLocation(
            base = _balances,
            keys = [msg.sender]
        ),
        newFromBalance
    )

    toBalance =
        StateRead(
            MappingLocation(
                base = _balances,
                keys = [to]
            )
        )

    newToBalance =
        Add(
            toBalance,
            value
        )

    StateWrite(
        MappingLocation(
            base = _balances,
            keys = [to]
        ),
        newToBalance
    )

    EventEmit(
        Transfer,
        [
            msg.sender,
            to,
            value
        ]
    )

    Return(true)
```

---

## 13. Semantic IR 的 JSON 形式示意

```json
{
  "id": "ir_10",
  "op": "StateRead",
  "result": "v3",

  "location": {
    "kind": "Mapping",
    "base": "_balances",
    "keys": [
      "msg.sender"
    ]
  },

  "origin_facts": [
    "fact_28",
    "fact_29",
    "fact_30"
  ]
}
```

StateWrite：

```json
{
  "id": "ir_14",
  "op": "StateWrite",

  "location": {
    "kind": "Mapping",
    "base": "_balances",
    "keys": [
      "msg.sender"
    ]
  },

  "value": {
    "op": "Sub",
    "args": [
      "fromBalance",
      "value"
    ]
  },

  "origin_facts": [
    "fact_35"
  ]
}
```

---

## 14. IR 中应保留 provenance

每个 IR 节点应保留：

```text
origin_facts
```

后续经过解混淆以后，可增加：

```text
rewrite_history
```

例如：

```text
rewrite_history:
    iszero(eq(x, not(0)))
        ->
    x != UINT256_MAX
```

形成完整溯源链：

```text
Recovered Source
      ↓
Deobfuscated IR
      ↓
Semantic IR
      ↓
Semantic Facts
      ↓
Source Evidence
```

---

## 15. 后续解混淆直接发生在 Semantic IR 上

可以逐步增加：

```text
ConstantPropagation
CopyPropagation
ConstantFolding
AlgebraicSimplification
DeadCodeElimination
UnreachableBlockElimination
OpaquePredicateRemoval
CFGSimplification
ControlFlowUnflattening
MBA Simplification
```

例如：

```text
x1 = Add(x, 0)
x2 = Xor(x1, 0)
x3 = Mul(x2, 1)
```

变为：

```text
x3 = x
```

又例如：

```text
Branch(
    Eq(x, x),
    BB_real,
    BB_fake
)
```

证明恒真后：

```text
Goto(BB_real)
```

并删除 `BB_fake`。

---

## 16. 推荐的第一版实现范围

当前只需要先完成：

```text
Semantic Facts
      ↓
Fact Grouping
      ↓
Semantic IR Builder
      ↓
Function
BasicBlock
Instruction
Expression
Location
CFG
Def-Use
Provenance
```

不要求第一版立即加入：

- 完整 SSA；
- Phi；
- MemorySSA；
- EffectSSA；
- C-like Pseudocode IR；
- 结构化 `If / While / For`；
- 复杂语义模式识别。

---

## 17. 最终推荐架构

```text
                    Solidity
                       │
                       ▼
            Solidity Atomic Analysis
                       │
                       │
                       ├─────────────┐
                       │             │
Yul ──────> S-SEIR ────┘             │
                       │             │
                       ▼             │
              Unified Semantic Facts
                       │
                       ▼
                Fact Grouping
                       │
                       ▼
              Semantic IR Builder
                       │
                       ▼
        ┌───────────────────────────┐
        │       Semantic IR         │
        │                           │
        │ Function                  │
        │ BasicBlock                │
        │ Semantic Instruction      │
        │ Expression DAG            │
        │ Location                  │
        │ CFG                       │
        │ Def-Use                   │
        │ Provenance                │
        └─────────────┬─────────────┘
                      │
                      ▼
             Deobfuscation Passes
                      │
                      ▼
          Deobfuscated Semantic IR
                      │
                      ▼
             Source Reconstruction
                      │
                      ▼
                   Solidity
```

---

# 核心结论

当前 Semantic Fact 已经完成了主要的 Solidity / Yul 统一语义提升。

下一阶段真正需要做的是：

> **把 Semantic Fact 从“事实记录模型”转换成“可进行程序变换的程序模型”。**

即：

```text
Semantic Fact
=
已经理解好的语义事实

Semantic IR
=
Function
+ BasicBlock
+ Instruction
+ Expression
+ Location
+ CFG
+ Def-Use
+ Provenance
```

第一版最重要的是先跑通：

```text
Semantic Facts
      ↓
Fact Grouping
      ↓
Semantic IR
      ↓
Simple Deobfuscation Pass
```

在此基础上再逐步增加：

```text
Opaque Predicate Removal
Control Flow Unflattening
MBA Simplification
Dead Code Removal
Semantic Canonicalization
```
