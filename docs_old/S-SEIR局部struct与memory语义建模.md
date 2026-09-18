# S-SEIR 局部 struct 与 memory 语义建模

## 1. 背景

当前 S-SEIR 已能识别部分 inline assembly 中的 struct 构造和 struct 字段修改，例如：

- 手动读取 `mload(0x40)` 并更新空闲内存指针；
- 向 memory struct 的字段槽位写入数据；
- 将 `mstore(add(p, 0x20), value)` 恢复为 `p.offset = value`。

但现有语义粒度仍偏粗，容易把某个局部操作提升成“整个函数的语义”。实际源码中，struct 构造、字段修改、指针推进、memory 写入，可能只是复杂函数中的一个子操作。因此 S-SEIR 应将这类语义下沉为局部语义片段，再由更高层聚合为函数级解释。

## 2. 目标

新的建模目标是：

```text
先记录局部事实，再组合高级语义。
```

也就是说，S-SEIR 不应直接判断：

```text
这个函数是在构造 struct
```

而应优先记录：

```text
某个 memory struct 字段被读取
某个 memory struct 字段被写入
某段空闲内存被手动分配
某段手动分配内存被写入数据
某个 struct 字段指向该内存区域
```

之后再根据这些局部事实组合出：

```text
StructInitializationFragment
StructMutationFragment
CursorBasedMemoryWrite
ManualMemoryObjectConstruction
```

## 3. 分层设计

建议将相关语义分为三层。

### 3.1 Atomic Overlay

Atomic Overlay 只记录单个局部事实，不表达整个函数目的。

建议包括：

```text
StructFieldRead
StructFieldWrite
MemoryRegionAllocate
MemoryRegionWrite
MemoryPointerBinding
```

示例：

```yul
let offset := mload(add(0x20, p))
```

记录为：

```json
{
  "kind": "StructFieldRead",
  "attrs": {
    "struct_object": "p",
    "struct_type": "struct DN404._PackedLogs",
    "field": "offset",
    "field_offset": 32,
    "value": "offset",
    "stmt_refs": ["asm_s_1"]
  }
}
```

示例：

```yul
mstore(add(0x20, p), add(offset, 0x20))
```

记录为：

```json
{
  "kind": "StructFieldWrite",
  "attrs": {
    "struct_object": "p",
    "struct_type": "struct DN404._PackedLogs",
    "field": "offset",
    "field_offset": 32,
    "value": "add(offset, 0x20)",
    "value_normalized": "offset + 0x20",
    "stmt_refs": ["asm_s_3"]
  }
}
```

### 3.2 Composite Fragment

Composite Fragment 由多个 Atomic Overlay 组合得到，表示一个局部语义片段。

建议包括：

```text
StructInitializationFragment
StructMutationFragment
CursorBasedMemoryWrite
ManualMemoryObjectConstruction
```

示例：

```yul
let offset := mload(add(0x20, p))
mstore(offset, or(or(shl(96, a), shl(8, id)), burnBit))
mstore(add(0x20, p), add(offset, 0x20))
```

可以组合为：

```json
{
  "kind": "CursorBasedMemoryWrite",
  "attrs": {
    "struct_object": "p",
    "cursor_field": "offset",
    "cursor_value": "offset",
    "memory_write": {
      "address": "offset",
      "address_semantic": "old(p.offset)",
      "value_normalized": "((a << 96) | (id << 8)) | burnBit"
    },
    "field_update": {
      "field": "offset",
      "new_value_normalized": "offset + 0x20"
    },
    "semantic_hint": "write_word_then_advance_struct_cursor"
  }
}
```

注意：这里的 `semantic_hint` 只是说明局部行为，不应直接判断整个函数就是 append 函数。

### 3.3 Function Summary

Function Summary 只作为索引和摘要，不替代局部 overlay。

示例：

```json
{
  "kind": "FunctionSemanticSummary",
  "attrs": {
    "contains": [
      "StructFieldRead",
      "StructFieldWrite",
      "MemoryRegionAllocate",
      "CursorBasedMemoryWrite"
    ],
    "notes": [
      "function contains memory struct mutation fragments"
    ]
  }
}
```

## 4. 类型与 struct 定义记录

struct 定义不应重复塞进每个 overlay 中。建议在 S-SEIR 中增加统一 `type_table` 或 `struct_table`。

示例：

```json
{
  "type_table": {
    "struct DN404._PackedLogs": {
      "name": "_PackedLogs",
      "canonical_name": "DN404._PackedLogs",
      "fields": [
        {
          "name": "logs",
          "type_string": "uint256[]",
          "offset": 0,
          "index": 0
        },
        {
          "name": "offset",
          "type_string": "uint256",
          "offset": 32,
          "index": 1
        }
      ]
    }
  }
}
```

overlay 中只引用类型和字段：

```json
{
  "struct_type": "struct DN404._PackedLogs",
  "field": "offset",
  "field_offset": 32
}
```

这样可以降低输出冗余，也更适合后续 LLM 消费。

## 5. 模式匹配方法

### 5.1 StructFieldRead

匹配条件：

```text
1. 存在 mload(addr)
2. addr 的线性 alias 可表示为 structVar + fieldOffset
3. structVar 是 memory struct
4. fieldOffset 能命中 struct layout
```

输入：

```yul
let offset := mload(add(0x20, p))
```

输出：

```text
offset = p.offset
```

### 5.2 StructFieldWrite

匹配条件：

```text
1. 存在 mstore(addr, value)
2. addr 的线性 alias 可表示为 structVar + fieldOffset
3. structVar 是 memory struct
4. fieldOffset 能命中 struct layout
```

输入：

```yul
mstore(add(0x20, p), add(offset, 0x20))
```

输出：

```text
p.offset = offset + 0x20
```

### 5.3 MemoryRegionAllocate

匹配条件：

```text
1. 读取 mload(0x40)
2. 后续写入 mstore(0x40, newFreePointer)
3. 中间存在对该分配区域的 memory 写入
```

输入：

```yul
let logs := add(mload(0x40), 0x40)
mstore(logs, n)
let offset := add(0x20, logs)
mstore(0x40, add(offset, shl(5, n)))
```

输出：

```json
{
  "kind": "MemoryRegionAllocate",
  "attrs": {
    "base": "mload(0x40)",
    "new_free_pointer": "offset + (n << 5)",
    "stored_values": [
      {
        "address": "logs",
        "value": "n"
      }
    ]
  }
}
```

### 5.4 ManualMemoryObjectConstruction

由 `MemoryRegionAllocate` 和相关 `MemoryRegionWrite` 组合得到。

输入：

```yul
let logs := add(mload(0x40), 0x40)
mstore(logs, n)
mstore(0x40, add(add(0x20, logs), shl(5, n)))
```

输出：

```text
构造了一段手动管理的 memory object：
  pointer = logs
  first_word = n
  free_memory_pointer 被推进
```

该语义不应强行判断为动态数组，只能记录为手动 memory object。若后续该 pointer 被写入 struct 的某个字段，再组合为 struct 相关语义。

## 6. 示例一：结构体构造片段

源码：

```solidity
function _packedLogsMalloc(uint256 n) private pure returns (_PackedLogs memory p) {
    assembly {
        let logs := add(mload(0x40), 0x40)
        mstore(logs, n)
        let offset := add(0x20, logs)
        mstore(0x40, add(offset, shl(5, n)))
        mstore(p, logs)
        mstore(add(0x20, p), offset)
    }
}
```

Atomic Overlay：

```text
MemoryRegionAllocate:
  base = mload(0x40)
  new_free_pointer = offset + (n << 5)

MemoryRegionWrite:
  memory[logs] = n

StructFieldWrite:
  p.logs = logs

StructFieldWrite:
  p.offset = offset
```

Composite Fragment：

```text
ManualMemoryObjectConstruction:
  pointer = logs
  stored_values:
    memory[logs] = n

StructInitializationFragment:
  target = p
  type = struct DN404._PackedLogs
  fields:
    logs = logs
    offset = offset
```

Solidity-like 视图：

```solidity
assembly /* s-seir solidity-like view */ {
    logs = (mload(0x40) + 0x40);
    memory[logs] = n;
    offset = (0x20 + logs);
    memory[0x40] = (offset + (n << 5));
    p.logs = logs;
    p.offset = offset;
}
```

## 7. 示例二：结构体字段修改片段

源码：

```solidity
function _packedLogsAppend(_PackedLogs memory p, address a, uint256 id, uint256 burnBit)
    private
    pure
{
    assembly {
        let offset := mload(add(0x20, p))
        mstore(offset, or(or(shl(96, a), shl(8, id)), burnBit))
        mstore(add(0x20, p), add(offset, 0x20))
    }
}
```

Atomic Overlay：

```text
StructFieldRead:
  offset = p.offset

MemoryRegionWrite:
  memory[offset] = ((a << 96) | (id << 8)) | burnBit

StructFieldWrite:
  p.offset = offset + 0x20
```

Composite Fragment：

```text
CursorBasedMemoryWrite:
  cursor = old(p.offset)
  memory[old(p.offset)] = ((a << 96) | (id << 8)) | burnBit
  p.offset = old(p.offset) + 0x20
```

Solidity-like 视图：

```solidity
assembly /* s-seir solidity-like view */ {
    offset = p.offset;
    memory[offset] = (((a << 96) | (id << 8)) | burnBit);
    p.offset = (offset + 0x20);
}
```

## 8. 预期结果

修改后，S-SEIR 的结果应具备以下特征：

1. 局部语义可独立识别：

```text
复杂函数中的某个 struct 字段写入，也能被识别为 StructFieldWrite。
```

2. 函数级解释不再覆盖局部事实：

```text
函数可以包含多个 semantic fragments，而不是被单一 overlay 定义。
```

3. struct 定义集中记录：

```text
overlay 引用 struct_type 和 field，不重复塞完整 layout。
```

4. solidity-like 输出更接近高级语义：

```text
memory[p + 32] = value
```

应优先显示为：

```text
p.offset = value
```

5. 对无法完全等价为 Solidity 的操作保持低层表达：

```text
memory[offset] = packedValue
```

不强行恢复成不可靠的 Solidity 语句。

## 9. 实现建议

后续代码可按以下顺序调整：

```text
1. 在 SourceCollector / TypeEnv 中建立 struct_table。
2. 在 EffectLifter 或 OverlayBuilder 中生成 Atomic Overlay。
3. 将现有 ReturnMemoryStructConstruction 降级为 StructInitializationFragment。
4. 将现有 StructMemoryMutation 拆分为 StructFieldWrite + 可选 StructMutationFragment。
5. Renderer 优先消费 Atomic Overlay，再消费 Composite Fragment。
6. Function Summary 只记录 contains，不作为主语义来源。
```

该方案保留当前已有识别能力，但将判断粒度从“函数级目的”降低为“局部语义片段”，更适合后续 LLM 根据 S-SEIR 进行源码恢复。
