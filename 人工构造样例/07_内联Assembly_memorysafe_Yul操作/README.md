# 07 内联 Assembly、memory-safe、Yul 操作

本样例覆盖：

- Solidity `assembly { ... }`
- `assembly ("memory-safe")`
- Yul `let`、`:=`、`if`、`switch`、`for`、`break`、`continue`、`function`、`leave`
- 常见 EVM Yul 内建：`mload`、`mstore`、`sload`、`sstore`、`keccak256`、`calldataload`、`calldatasize`、`caller`、`callvalue`、`gas`、`log3`
- ERC-20 存储槽计算、selector 读取和事件 topic

入口合约：`contracts/AssemblyERC20.sol`

