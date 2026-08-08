# Atomic Solidity Demo

这个项目是一个 Solidity AST 原子化 demo，用来验证下面的路线是否可行：

```text
Solidity source
  -> solc --standard-json typed AST
  -> recursive expression lowering
  -> custom Atomic AST / Atomic IR JSON
```

它不是完整 Solidity 前端，也不会修改原始 Solidity AST 或把 Atomic AST 重新交给 `solc` 编译。Atomic IR 是给后续 CFG、SSA、def-use、effect analysis 和语义提取使用的中间表示。

## 环境要求

- Python 3.11+
- `uv`
- 本地 `solc` 可执行文件

安装 Python 依赖：

```bash
uv venv --python 3.11
uv sync --extra dev
```

安装 `solc` 的常见方式：

```bash
# macOS
brew install solidity

# Ubuntu / Linux: 也可以下载 Solidity 官方静态二进制
curl -L -o /usr/local/bin/solc \
  'https://binaries.soliditylang.org/linux-amd64/solc-linux-amd64-v0.8.20+commit.a1b79de6'
chmod +x /usr/local/bin/solc
```

如果 `solc` 不在 `PATH`，可以显式指定：

```bash
export SOLC_BINARY=/path/to/solc
```

## 运行

```bash
uv run python -m atomic_solidity examples/Simple.sol
```

或者：

```bash
uv run python main.py examples/Simple.sol
```

输出文件会写入：

```text
output/<source-name>.atomic.json
```

例如：

```text
output/Simple.atomic.json
```

## 测试

```bash
uv run pytest -q
```

测试覆盖：

- 复合算术表达式拆成独立 `BINARY_OP`
- mapping 状态变量写入产生 `STORAGE_LOCATION` / `STORAGE_WRITE`
- `balances[user] += amount` 只计算一次 storage location
- 前缀/后缀自增返回值不同
- `&&` 短路表达式下降为 `BRANCH` + `PHI`
- 主要操作保留 `ast_id`、`src`、`original_node_type`
- 更复杂样例覆盖嵌套 mapping、三元表达式、if CFG、短路控制和不支持语句 diagnostic

## Atomic AST 数据结构

核心 dataclass 位于 `atomic_solidity/atomic_ir.py`：

- `AtomicProgram`: compiler version、source files、diagnostics
- `AtomicSourceFile`: source path、contracts
- `AtomicContract`: contract name、AST id、functions
- `AtomicFunction`: parameters、return parameters、basic blocks、entry block
- `BasicBlock`: operations、terminator、predecessors、successors
- `AtomicOperation`: kind、inputs、output、attributes、source、effects、may_revert
- `SourceInfo`: origin AST id、`src`、source file、original node type

每条 operation 尽量在 `attributes` 中保留 `typeDescriptions`、`referencedDeclaration`、storage layout 和原始 `nodeType`。

## 已支持节点

表达式：

```text
Literal
Identifier
BinaryOperation
UnaryOperation
Assignment
IndexAccess
MemberAccess
FunctionCall
TupleExpression
Conditional
```

语句：

```text
Block
ExpressionStatement
VariableDeclarationStatement
Return
IfStatement
UncheckedBlock
ForStatement
WhileStatement
DoWhileStatement
Break
Continue
```

遇到不支持的节点时，lowerer 会输出 `UNSUPPORTED_NODE` diagnostic，并尽量生成 `UNKNOWN_OPERATION`，避免静默丢语义。

## 当前不支持

第一版没有完整实现：

```text
TryStatement
InlineAssembly
EmitStatement
RevertStatement
ModifierDefinition
复杂继承展开
tuple destructuring 的逐元素写入
完整 external call effect 模型
```

## 关键 lowering 规则

局部读取：

```text
v0 = READ_LOCAL a
```

状态变量读取：

```text
loc0 = STATE_LOCATION count
v0 = STORAGE_READ loc0
```

mapping 位置和读写分离：

```text
v0 = READ_LOCAL user
loc0 = STATE_LOCATION balances
loc1 = STORAGE_LOCATION balances, [v0]
v1 = STORAGE_READ loc1
```

复合赋值只计算一次位置：

```text
v0 = READ_LOCAL user
loc0 = STATE_LOCATION balances
loc1 = STORAGE_LOCATION balances, [v0]
v1 = STORAGE_READ loc1
v2 = READ_LOCAL amount
v3 = BINARY_OP "+"
STORAGE_WRITE loc1, v3
```

`&&` 和 `||` 不会被当成普通 `BINARY_OP`。demo 会构造 basic blocks、`BRANCH`、`JUMP` 和 `PHI`，让右侧表达式受到短路控制。

## 求值顺序限制

Solidity 表达式的同级子表达式如果可能有副作用，本 demo 不声称线性 Atomic Operation 顺序就是 Solidity 语言保证的求值顺序。当前实现会在检测到两个兄弟表达式都可能有副作用时输出：

```text
UNSPECIFIED_SIBLING_EVALUATION_ORDER
```

后续版本应加入 `EVALUATION_GROUP` 或偏序依赖图，表达“两个子表达式都先于父操作，但二者之间没有确定顺序”。

## 为什么不重新输入 solc

Atomic AST 不是 Solidity AST。它拆掉了复合表达式，加入了 storage location、临时值、basic block、`PHI`、effect 和 diagnostic 信息。这些结构服务于分析，不符合 Solidity 源语言语法，也不应该伪装成 solc 可编译输入。

## 为什么适合 SSA 和语义提取

Atomic IR 把“读局部变量”“计算 storage 位置”“读 storage”“二元运算”“写回”拆成独立操作，并且每个操作有稳定 id、输入、输出和 source mapping。后续做 LocalSSA、StorageSSA、def-use 链和 effect analysis 时，不需要再从复合 Solidity AST 表达式里反复恢复求值边界。

## 示例

输入 `examples/Simple.sol`：

```solidity
result = (a + b) * (c - d);
```

输出逻辑上等价于：

```text
v0 = READ_LOCAL a
v1 = READ_LOCAL b
v2 = BINARY_OP "+", v0, v1
v3 = READ_LOCAL c
v4 = READ_LOCAL d
v5 = BINARY_OP "-", v3, v4
v6 = BINARY_OP "*", v2, v5
WRITE_LOCAL result, v6
```

运行示例：

```bash
SOLC_BINARY=/tmp/atomic-solidity-solc/solc-0.8.20 uv run python main.py examples/Simple.sol
# output/Simple.atomic.json
```

额外复杂样例：

- `examples/NestedStorageAndConditional.sol`: `balances[user][bucket] += amount + (boosted ? 10 : 1)`，检查嵌套 storage location、复合赋值、条件表达式 `PHI`
- `examples/ControlFlowAndShortCircuit.sol`: `if (enabled && scores[user] > minimum)` 加三元赋值，检查 storage 读取、短路 CFG、if CFG
- `examples/UnsupportedLoopDiagnostic.sol`: `ForStatement` 回归样例，检查初始化、条件、循环体和步进表达式均被原子化并生成循环 CFG
- `examples/ForAtomicBody.sol`: `for` 循环中的局部变量声明、复合算术和累加写回
- `examples/WhileStorageUpdate.sol`: `while` 循环中的 mapping 定位、storage 读取和写回
- `examples/DoWhileAtomicBody.sol`: `do while` 的先执行循环体语义、除法和双返回值写入
- `examples/NestedLoopControl.sol`: 嵌套 `for/while` 以及内层 `break/continue` 控制流
- `examples/LoopShortCircuit.sol`: 循环条件中的 `&&` 短路 CFG 和循环体中的三元表达式 `PHI`

可以逐个生成 JSON：

```bash
uv run python -m atomic_solidity examples/NestedStorageAndConditional.sol
uv run python -m atomic_solidity examples/ControlFlowAndShortCircuit.sol
uv run python -m atomic_solidity examples/UnsupportedLoopDiagnostic.sol
uv run python -m atomic_solidity examples/ForAtomicBody.sol
uv run python -m atomic_solidity examples/WhileStorageUpdate.sol
uv run python -m atomic_solidity examples/DoWhileAtomicBody.sol
uv run python -m atomic_solidity examples/NestedLoopControl.sol
uv run python -m atomic_solidity examples/LoopShortCircuit.sol
```

本地检查结果示例：

```text
NestedStorageAndConditional:
  STORAGE_LOCATION=2, STORAGE_READ=1, STORAGE_WRITE=1, BRANCH=1, PHI=1

ControlFlowAndShortCircuit:
  BRANCH=3, PHI=2, STORAGE_READ=4, STORAGE_LOCATION=3

UnsupportedLoopDiagnostic:
  BRANCH=1, UNKNOWN_OPERATION=0, 循环体与步进表达式均为 Atomic Operation
```

## 后续路线

- LocalSSA
- MemorySSA
- StorageSSA
- modifier 展开
- internal call graph
- external call effects
- event/revert/create/selfdestruct
- inline assembly/Yul 到统一 Atomic IR
- Atomic IR 到规范化 Solidity 的 pretty printer
