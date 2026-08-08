# Atomic Solidity AST Demo

这个 demo 验证一条很窄但关键的路线：

```text
Solidity source
  -> solc typed JSON AST
  -> Solidity AST-to-AST expression atomization
  -> atomic_ast.json
  -> solc language=SolidityAST
  -> semantic analysis
  -> non-empty EVM bytecode
```

它不会把 AST 转回 Solidity 源码，也不会输出自定义 IR。`output/atomic_ast.json` 是重新输入 `solc` 的真实 Solidity AST JSON。

## 环境

本项目使用 `uv` 虚拟环境：

```bash
cd atomic-solidity-ast-demo
uv venv --python 3.12
uv sync --extra dev
```

CLI 会按顺序寻找：

```text
SOLC_BINARY
PATH 中的 solc
.solc/solc-linux-amd64-v0.8.36
```

当前项目目录已放置官方 Linux 静态 `solc 0.8.36+commit.8a079791`，用于没有系统 `solc` 的环境。也可以显式指定：

```bash
uv run python main.py examples/Demo2.sol --solc /path/to/solc
```

## 运行

```bash
uv run python main.py examples/Demo2.sol
```

输出示例：

```text
[1] Compiling Solidity source...
    PASS
[2] Round-tripping original AST through SolidityAST importer...
    PASS
[3] Splitting nested expressions...
    generated temporaries: 3
    generated atomic statements: 3
    unsupported expressions: 0
[4] Validating Atomic AST...
    atomicity: PASS
[5] Recompiling Atomic AST with solc...
    semantic analysis: PASS
    bytecode generation: PASS
```

主要产物：

```text
output/original_ast.json
output/original_bytecode.txt
output/atomic_ast.json
output/atomic_ast_readable.json
output/reanalyzed_atomic_ast.json
output/atomic_bytecode.txt
output/validation_report.json
```

## 支持范围

当前只支持稳定子集：

- `Literal`
- `Identifier`
- `BinaryOperation`: `+ - * / % ** & | ^ << >> < > <= >= == !=`
- 前缀 `UnaryOperation`: `! ~ - +`
- 普通变量声明初始化
- `Identifier = expression`
- `return expression`
- `if condition`
- 循环 body 内普通 statement
- 单元素、非 inline-array 的 `TupleExpression`，用于处理括号表达式

明确保持原样并报告 `UNSUPPORTED`：

- `&&`、`||`
- `condition ? a : b`
- `++`、`--`
- 复合赋值
- tuple assignment
- storage/memory reference temporary
- mapping、dynamic array、struct temporary
- loop condition atomization
- `try/catch`、`inline assembly`、`new`、`delete`

## 原子化算法

`SolidityExpressionSplitter` 对每个 `Block` statement 建立 prefix 列表：

1. 自底向上处理表达式子节点。
2. 子表达式如果是支持的主要操作，就生成 `__atomN` 局部变量声明。
3. 将父表达式中的复杂子表达式替换为新的 `Identifier(__atomN)`。
4. 把生成的 `VariableDeclarationStatement` 插入当前 statement 前。
5. `return`、`if condition`、简单 assignment 的 RHS 会把最外层操作也 outline；变量声明 initializer 可以保留最外层单个操作。

临时变量类型来自第一遍 solc AST 的 `typeDescriptions.typeString`，只允许可靠构造 `ElementaryTypeName` 的 value types。新节点 `id` 从 `max(original_ast_id) + 1` 开始递增；新节点 `src` 继承被 outline 表达式的 `src`，`nameLocation` 使用 solc 输出里已有的 synthetic `-1:-1:-1` 形式。

## 转换示例

输入：

```solidity
return (a + b) * (c - d);
```

关键 AST 形状从：

```text
Return
  BinaryOperation *
    TupleExpression
      BinaryOperation +
    TupleExpression
      BinaryOperation -
```

变为：

```text
VariableDeclarationStatement uint256 __atom0 = BinaryOperation +(a, b)
VariableDeclarationStatement uint256 __atom1 = BinaryOperation -(c, d)
VariableDeclarationStatement uint256 __atom2 = BinaryOperation *(__atom0, __atom1)
Return Identifier __atom2
```

## 测试

```bash
uv run pytest -q
```

测试会对 `Demo1` 到 `Demo5` 和 `UnsupportedDemo` 执行真实端到端流程，检查 AST round-trip、atomicity、SolidityAST import、语义分析和 bytecode 生成。

