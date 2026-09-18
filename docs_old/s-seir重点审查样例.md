# S-SEIR 重点审查样例

本文记录前期单独点名重跑、检查过的 S-SEIR 样例，便于后续回归测试和人工审查时快速定位。

## 1. 重点反复审查样例

### 1.1 `0x1e0847e537f75e6a983828c7f8ebf5a8108107d6__TKM`

重点检查内容：

- ERC20 基础函数恢复；
- `setMaxs`；
- `transferFrom`；
- `D/C/B`；
- router flag 相关函数；
- mapping slot 恢复；
- 多 assembly 块处理；
- function-level memory / slot 追踪问题。

### 1.2 `0x1bacdd4463d36f591d2bc156b716d53f1d6ae13d__contracts__Kof`

重点检查内容：

- DN404 相关 assembly；
- Ownable / OwnableRoles；
- struct memory 构造与修改；
- manual memory allocation；
- role check；
- handover slot；
- event 恢复；
- return / revert 语义恢复。

### 1.3 `0x2bac62804952d980c0d207641101aa3b4d53652f__Token`

重点检查内容：

- 复杂 condition 展示；
- condition tree demo；
- 展示层 condition 简化。

### 1.4 `0x3bb48be44d06b6a1f5272a64de4839a13a62590b__TKM`

重点检查内容：

- 近期改动后语义模型是否回退；
- revert payload 处理；
- 无数据 / 有数据 revert 区分。

### 1.5 `0x3b9c8ff15abe9a0dc43341ceca2023e04698d9ca__contracts__EUROS`

重点检查内容：

- SinkResolver 与 MemorySSA 的协作；
- opaque 剪枝；
- unknown slot 处理；
- 语义模型保守恢复策略。

### 1.6 `0x43849f4035d08f3c58f70d7747a2107162930003__Token`

重点检查内容：

- sha256 precompile；
- `bytes20(msg.sender)` 输入恢复；
- slot / source tracing；
- known value 与 byte-axis MemorySSA。

## 2. 逐个重跑检查过的样例

- `0x0003e1090ec0036da0a44e65c606a281f94f357b__Token`
- `0x000505149acc7a1501c1776d18f2fd188607a5ef__Token`
- `0x13c53dfff4cee74f61a044696fe52f05a70ea4f5__Token`
- `0x15a96ebe65dc813a3f458395918c9013808b0e3a__contracts__StellarVault`
- `0x25ca3efcaf7befe9ba2fb45c6c7eab13adfae4d5__ajshhdd`
- `0x28cbe531bedb28fb5937e983289bb2159c2c9c58__opens`
- `0x32b8d41bf8b0c7290ceb26dd8a512c98d2de5d3a__sdjsjdl`
- `0x36e8a6b7f42a47d505a4be7a10d42944ace25b06__contracts__MyTokenV1`
- `0x72cf4cfa6f8043b0af5787689c8e010070644266__NEWYORKCITY`
- `0x83e3c857fc785e4487caf0e682819b2e6ab9733f__BABYTOKEN`
- `0x599a69de2267779a58eaef9a992588fe8b7047b7__contracts__QuantNeural`
- `0xdfc541c9930fb09865f9abbaa09925dcd2b02f28__contracts__ThetaNet`

## 3. 展示层 / 语义模型专项检查样例

- `0x2f024c117d531759b6f682bf8c902f3bc8ee6778__contracts__Token7`
- `0x2f68ada4795c2bbf1da2fb37e4bb22e37c51195c__sdjd`
- `0x3c2066cf79b73d8a62b75ac2b928bc930e4a9fd5__Token`
- `0x3e4e3d7d77f148da017fcc529a05b444e7adfe8e__khkjhk`
- `0x3f6c21bfcbb91f437d0a88e2c44a0bbdb637be5e__Token`
- `0x3f6f4d572939019597d7181544d2d1aaed89df97__Token`

## 4. 最近补丁后重跑样例

### 4.1 `0x3b0db14b6f0ac2a88e4b2561b7eba4462aeb21d4__contracts__Kof`

检查点：

- `not(_ROLE_SLOT_SEED)` 不再标记为 `unknown_storage_slot`；
- 当前恢复为 manual constant slot 状态读取；
- `_ROLE_SLOT_SEED` 相关 role slot 检查语义保持保守记录。

### 4.2 `0xd13d1b264334add90b9f6aaa4e19be04d1280a1b__project__contracts__BscToken`

检查点：

- packed balance slot 已恢复；
- `mstore(0x0c, or(shl(96, from), _BALANCE_SLOT_SEED))` 后接 `keccak256(0x0c, 0x20)` 可恢复为：

```solidity
storage[keccak256(abi.encodePacked(bytes20(from), low_bytes(_BALANCE_SLOT_SEED, 12)))]
```

- `transfer` / `transferFrom` 中 `fromBalanceSlot` 不再残留为 unknown。

### 4.3 `0x71e49f4941a20117dd0d6c42ef7e34e4f377f05c__TKM`

检查点：

- `_setRouterFlag` mapping slot 正常恢复；
- `_transfer` 中跨 assembly 复用 memory 的 `fromSlot` 仍保守保留为：

```solidity
storage[fromSlot]
```

原因：

该问题需要后续真正打通 function-level 多 assembly MemorySSA，不属于单个 slot 表达式折叠或 packed slot 规则能可靠解决的范围。
