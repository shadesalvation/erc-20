# 本地 CFG 优势

## 1. 背景

在当前 S-SEIR 的 function-level CFG 中，Solidity 函数级控制流主要借用 Slither 的函数 CFG；但是进入 inline assembly / Yul 范围后，不直接使用 Slither 展开的 assembly 节点，而是使用项目中的 `build_yul_cfg` 构建本地 Yul CFG 子图。

整体拼接方式是：

```text
Slither Solidity CFG predecessor
  -> 本地 Yul CFG entry
  -> 本地 Yul CFG exits
  -> Slither Solidity CFG successor
```

这样做的核心原因是：当前目标不是普通 Solidity 控制流分析，而是要服务 Yul 语义恢复、MemorySSA、slot/event/call/revert 识别，以及最终的 S-SEIR 统一语义建模。

## 2. 节点粒度更适合 Yul 语义恢复

Slither 的 CFG 更适合 Solidity / SlithIR 层面的静态分析，assembly 节点通常更偏整体或展开后的中间表示。当前模块需要在 Yul 层面精确识别每一类原始语句，例如：

```text
YulVariableDeclaration
YulAssignment
YulExpressionStatement
YulIf
YulSwitch
YulForLoop
YulBreak
YulContinue
YulLeave
```

这些节点粒度直接影响后续语义恢复。例如，MemorySSA 需要知道哪个节点是 `mstore`，slot 恢复需要知道哪个节点是 `keccak256`，事件恢复需要知道哪个节点是 `log3`，异常恢复需要知道哪个节点是 `revert`。

本地 Yul CFG 保留的是更接近 solc AST 的 Yul 语句节点，因此更适合作为后续语义分析的基础。

## 3. 能直接保留 Yul AST 表达式

后续模块需要频繁读取 Yul 表达式树，例如：

```text
statement_expression(node)
direct_call(expr)
yul_expression(expr)
```

例如：

```yul
let loc := keccak256(ptr, 0x40)
let bal := sload(loc)
```

本地 CFG 节点可以直接关联到 Yul AST，因此可以继续向下解析：

```text
keccak256(ptr, 0x40)
sload(loc)
```

而不是只拿到一个已经被 Slither 抽象后的文本或中间节点。对于当前的 memory / slot / event / call / revert 恢复来说，保留原始表达式树非常关键。

## 4. MemorySSA 依赖本地 Yul CFG

当前 `assembly_memory_ssa.analyze_block(block)` 内部就是以本地 Yul CFG 为基础运行的：

```text
assembly block AST
  -> build_yul_cfg(block.yul_ast)
  -> PathState / MemorySSA / LoopMemoryRecord
```

MemorySSA 中的大量数据都依赖本地 CFG 的节点编号和结构，包括：

```text
PathState
memory reaching definitions
value SSA
branch candidates
LoopMemoryRecord
MemoryPhi
MemoryRangeSummary
MemoryTop
```

如果改用 Slither 的 assembly CFG，节点 id、Yul AST 节点、stmt_ref、branch sink 之间的映射都会变复杂，甚至可能断开。尤其是 loop-aware lazy MemorySSA 需要明确知道 loop header、body、backedge、exit，这些结构由本地 CFG 更容易稳定表达。

## 5. 对 Yul 特有控制流处理更可控

Yul 有一些与 Solidity 不完全相同的控制结构和终止语义，例如：

```text
YulIf
YulSwitch
YulForLoop
YulBreak
YulContinue
YulLeave
revert
return
stop
```

本地 CFG 可以按照当前项目需要明确建模这些结构：

```text
if      -> true / false / merge
switch  -> case / default / merge
for     -> init / condition / body / post / backedge / exit
break   -> loop exit
continue-> loop post
leave   -> terminal
revert  -> terminal
return  -> terminal
```

这些结构不仅用于控制流展示，也直接服务于分支物化、路径条件记录、循环摘要和懒加载 MemorySSA。

## 6. 更容易导出和调试 Yul CFG

本地 CFG 的节点和边是项目自己定义的，因此可以稳定导出：

```text
node_id
node.kind
node.text
node.src
edge.source
edge.target
edge.label
```

这使得 DOT / PNG 调试更直接。测试时可以清楚看到：

```text
mstore 节点
sload 节点
if true / false 边
loop backedge
terminal 节点
```

这比直接依赖 Slither 对 assembly 的展开结果更容易定位语义恢复问题。

## 7. 能保证 stmt_refs 与 SourceStatement 对齐

S-SEIR 中所有 effect 和 overlay 都需要通过 `stmt_refs` 回指原始语句，例如：

```json
{
  "kind": "StorageRead",
  "stmt_refs": ["asm_s_12"]
}
```

本地 CFG 可以保持如下链路：

```text
CFG node
  -> Yul AST node
  -> SourceStatement stmt_id
  -> Effect stmt_refs
  -> Overlay stmt_refs
```

如果直接使用 Slither 展开的 assembly CFG，未必能稳定回到当前项目自己收集的 `asm_s_*` 语句编号。这样会影响 S-SEIR 的可追溯性。

## 8. Slither 仍然被使用，但职责不同

当前选择并不是放弃 Slither，而是分工使用：

```text
Slither：负责 Solidity 函数级 CFG
build_yul_cfg：负责 inline assembly 内部 Yul CFG
```

这样可以同时获得两个优势：

```text
1. Solidity 层不重新造完整函数 CFG，直接借用 Slither 成熟结果；
2. Yul 层保留 AST 级控制流和表达式，方便语义恢复；
3. 在 assembly 边界处完成两者拼接，形成 function-level S-SEIR control layer。
```

## 9. 结论

当前处理过程中选用本地 `build_yul_cfg` 的主要优势是：

```text
1. 节点粒度更接近 Yul 原始语句；
2. 能直接访问 Yul AST 表达式树；
3. 与 MemorySSA、LoopMemoryRecord、分支物化等原模块算法天然对齐；
4. 对 Yul if / switch / for / break / continue / leave / revert / return 的建模更可控；
5. 更容易导出 DOT 图并调试；
6. 更容易维护 stmt_refs 与 SourceStatement 的对应关系；
7. 更适合作为 S-SEIR 中 assembly 语义建模的底层控制流基础。
```

因此，当前策略可以概括为：

```text
函数级控制流用 Slither；assembly 内部控制流用 build_yul_cfg。
```
