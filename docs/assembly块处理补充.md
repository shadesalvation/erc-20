# Assembly 块处理补充

当前结果不能视为可直接替换 `assembly {}` 的高级 Solidity 源码。更准确地说，它是 Solidity-like 语义恢复视图。

主要还需要完成以下改进：

- 移除伪内存语句：`memory[bbwz + 0] = ...`、`memory[0x40]` 不是 Solidity 语法，需要按用途折叠为局部变量、`abi.encodePacked`、哈希输入或调用参数。
- 补全变量声明与类型：目前如 `YvIh = ...`、`qVPd = ...` 没有可靠的 `uint256`、`address`、`bytes32` 等类型和作用域。
- 真正替换源码 AST：当前是报告中的语义视图，尚未把对应 assembly 块重建为 Solidity AST/源码并回填。
- 完整控制流恢复：`if` 的花括号、嵌套、循环、`switch`、`break/continue/leave` 仍需恢复为合法 Solidity 结构。
- 完整状态与内存消解：已识别的 mapping/slot 可以恢复，但所有 `mload/mstore`、动态 offset、copy 指令、分支合流后的内存值仍需消除或物化为 Solidity 变量。
- 异常恢复：目前重点是 `revert(0,0)`；还需恢复带错误数据的 `revert`、`panic`、自定义错误和返回数据。
- 外部调用恢复：预编译 `sha256` 已可提升，但普通 `call` 仍多是低级调用；需进一步恢复 selector、参数、返回值及可能的接口函数。
- 类型与算术语义：需区分有符号/无符号、窄位宽、溢出、移位、地址截断及 `unchecked` 的准确边界。
- 最终验证：重建后必须 `solc` 编译，并用静态对比或测试验证关键路径与原 Yul 的读写、事件、revert 行为一致。

当前模块已经覆盖“理解 assembly 在做什么”的基础层。下一阶段核心是把这些语义结果统一成可编译的 Solidity AST，再做源码级替换。

但其余部分不宜称为“简单”，更准确是“规则明确，但工程细节很多”。最难的仍是三项：
内存消解：需要区分编码缓冲区、临时字、返回数据和动态数据，且要正确处理分支合流。
控制流重建：if/switch/for/leave 和变量作用域必须生成可编译结构。
类型推断：uint256、address、bytes32、bool、动态 bytes 的误判会让“看起来合理”的源码无法编译或改变语义。

## Memory 与 Slot 占位清理模块

该模块运行在 MemoryTrackerSSA、分支物化和 slot/state 语义恢复之后。它不重新分析 Yul 内存，也不修改原始 Solidity 源码；目标是清理恢复视图中仅供中间审查使用的伪内存和 slot 占位表达式。

### 前提

- 每个 assembly 块已有独立的 MemoryTrackerSSA，所有 `mstore`、`mload`、copy 和 `keccak256` 参数来源均已记录。
- slot 恢复已将可识别的 `keccak256` slot 构造、`sload`、`sstore` 替换为状态变量或 mapping 访问。
- 后续语义模块读取 MemoryTrackerSSA 和已恢复的语义结果，不依赖恢复视图中的 `memory[...]` 文本再次追踪内存。

### 清理规则

1. 删除只用于已恢复 slot 构造的 `memory[...] = ...`、`mload` 与 `slot(...)` 占位语句。
   例如，在 `UbLX[_from]` 已恢复后，删除其对应的 `mstore(bbwz, _from)`、`mstore(add(bbwz, 32), 0)` 和 `XCUz = slot(UbLX[_from])`。
2. 删除没有被任何保留高层语义使用的 `memory[...]` 读写。函数末尾仍未被读取或消费的内存内容没有可观察效果。
3. 删除只作为已替换状态读写临时地址的变量，例如 `iLho = slot(UbLX[_to])`；其后的 `sload/sstore` 已分别恢复为 `UbLX[_to]` 的读写时，不再保留 `iLho`。
4. 不删除仍未完成语义替换的内存操作：事件 data、外部调用 calldata/返回区、哈希或预编译输入、revert payload、return data、动态范围 copy 等。它们必须先被对应模块替换为 `abi.encode...`、调用参数、错误参数或返回值表达式。
5. 对含 `unknown`、动态范围或无法确认消费者的 memory 记录，只有在没有任何保留语义终点消费它时才删除；否则保留恢复表示并标注为未消解。

### 实现步骤

1. 以 assembly block 的语义 IR 为输入，读取 MemoryTrackerSSA、slot/state 恢复结果和各模块的替换记录。
2. 标记已被状态访问替代的 slot 构造链：相关 `mstore`、`keccak256`、地址临时变量和伪 memory 读写。
3. 标记已被事件、异常、外部调用、哈希或返回恢复模块消费的内存链；这些链在其消费者完成替换前保留。
4. 对剩余 `memory[...]` 读写做局部 def-use 检查：没有保留消费者且没有原始副作用的节点删除。
5. 输出裁剪后的恢复视图，并保留每个删除节点的 Yul 来源索引，便于审查和后续 AST 级源码替换。

该模块可命名为 `scripts/assembly_memory_slot_cleanup.py`，作为 Memory/Slot 语义恢复的后处理模块接入 pipeline。
