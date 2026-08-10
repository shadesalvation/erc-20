# 人工构造 ERC-20 语法样例

这些样例用于覆盖 `solidity语法.md` 中整理的 Solidity 与 Yul 语法。每个子目录是一份独立样例，目录名描述该样例重点覆盖的语法及常见用法。

## 样例索引

| 目录 | 入口 | 覆盖重点 |
| --- | --- | --- |
| `01_源文件布局_接口_库_文件级定义` | `contracts/LayoutToken.sol` | SPDX、pragma、import、接口、库、文件级 type/struct/enum/error/constant/function、NatSpec |
| `02_基础ERC20_状态变量_构造函数_事件_错误_modifier` | `contracts/GuardedERC20.sol` | 状态变量、constant、immutable、transient、constructor、event、error、modifier |
| `03_继承_抽象合约_可见性_receive_fallback` | `contracts/PayableInheritedERC20.sol` | abstract、继承、virtual/override、super、可见性、receive、fallback、payable |
| `04_类型系统_数组_mapping_struct_enum_UDVT` | `contracts/TypedSnapshotERC20.sol` | 值类型、bytes/string、array、mapping、struct、enum、数据位置、函数类型、UDVT、类型转换 |
| `05_表达式_运算符_控制流_checked_unchecked` | `contracts/ControlFlowERC20.sol` | 算术/逻辑/位运算、复合赋值、三元表达式、delete、new memory array、if/for/while/do、break/continue、unchecked |
| `06_ABI_低级调用_trycatch_new_合约类型` | `contracts/AbiFactoryAndProbe.sol` | ABI encode/decode、selector、低级 call/staticcall、try/catch、new、CREATE2 salt、合约类型、多返回值/解构 |
| `07_内联Assembly_memorysafe_Yul操作` | `contracts/AssemblyERC20.sol` | 内联 assembly、memory-safe、Yul let/if/switch/for/function/leave、EVM opcode 内建 |
| `08_Yul对象_ERC20选择器分发` | `yul/MiniERC20.yul` | Yul object、code/data、selector 分发、sload/sstore、log、return/revert |

## ERC-20 约束

Solidity 样例均围绕 ERC-20 标准接口展开，包含或实现以下核心元素：

- `Transfer`、`Approval`
- `totalSupply`
- `balanceOf`
- `allowance`
- `transfer`
- `approve`
- `transferFrom`

第 08 个纯 Yul 样例实现 ERC-20 的 selector 分发和核心存储读写，适合用于测试 Yul 与反编译/恢复流程。

