# 06 ABI、低级调用、try/catch、new、合约类型

本样例覆盖：

- 合约类型转换与接口调用
- `abi.encode`、`abi.encodePacked`、`abi.encodeWithSelector`、`abi.encodeWithSignature`、`abi.encodeCall`、`abi.decode`
- `.selector`
- 低级 `call`、`staticcall`
- 外部调用 options：`{value: ..., gas: ...}`
- `try / catch Error / catch Panic / catch (bytes)`
- `new` 合约创建与 CREATE2 `salt`
- 多返回值、解构、省略解构
- ERC-20 工厂、探针和托管调用

入口合约：`contracts/AbiFactoryAndProbe.sol`

