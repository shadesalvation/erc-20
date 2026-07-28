// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.27;

import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

interface IERC20 {    
    function symbol() external view returns (string memory);
    function decimals() external view returns (uint8);
    function balanceOf(address owner) external view returns (uint);
    function allowance(address owner, address spender) external view returns (uint256);
    function transfer(address to, uint256 value) external returns (bool);
    function transferFrom(address from, address to, uint256 value) external returns (bool);
}

contract Utils is ReentrancyGuard {
    address public owner;

    modifier onlyOwner() {
        require(msg.sender == owner, "Not authorized");
        _;
    }

    constructor() {
        owner = msg.sender;
    }

    event TransferETH(address indexed from, address indexed to, uint256 value);
    event TransferToken(address indexed from, address indexed to, uint256 value, address indexed token);
    event WithdrawETH(address indexed to, uint256 amount);
    event WithdrawToken(address indexed token, address indexed to, uint256 amount);

    // 检查一个地址是否为合约地址
    function isContract(address account) internal view returns (bool) {
        uint256 size;
        assembly {
            size := extcodesize(account)
        }
        return size > 0;
    }

    function getTokenInfo(address token) public view returns (string memory symbol, uint8 decimals) {
        if (!isContract(token)) {
            return ("UNKNOWN", 0);
        }

        try IERC20(token).symbol() returns (string memory _symbol) {
            symbol = _symbol;
        } catch {
            symbol = "UNKNOWN";
        }

        try IERC20(token).decimals() returns (uint8 _decimals) {
            decimals = _decimals;
        } catch {
            decimals = 0;
        }
    }

    // 查询多个地址余额
    function getBalances(address[] memory users, address token) public view returns (uint256[] memory) {
        uint256[] memory balances = new uint256[](users.length);

        for (uint256 i = 0; i < users.length; i++) {
            address user = users[i];
            if (token == address(0)) {
                balances[i] = user.balance;
            } else {
                if (isContract(token)) {
                    try IERC20(token).balanceOf(user) returns (uint256 balance) {
                        balances[i] = balance;
                    } catch {
                        balances[i] = 0;
                    }
                } else {
                    balances[i] = 0;
                }
            }
        }
        return balances;
    }

    // 批量转账 ETH
    function batchTransferEth(address[] memory toList, uint256[] memory values) external payable {
        require(toList.length == values.length, "Input arrays must have the same length");

        uint256 totalAmount = 0;
        for (uint256 i = 0; i < values.length; i++) {
            totalAmount += values[i];
        }

        require(msg.value >= totalAmount, "Insufficient ETH sent with the transaction");

        for (uint256 i = 0; i < toList.length; i++) {
            address to = toList[i];
            uint256 value = values[i];

            require(to != address(0), "Recipient address cannot be zero");
            require(value != 0, "value cannot be zero");

            payable(to).transfer(value);
            emit TransferETH(msg.sender, to, value);
        }

        if (msg.value > totalAmount) {
            payable(msg.sender).transfer(msg.value - totalAmount);
        }
    }

    // 批量转账 Token
    function batchTransferToken(address[] memory toList, uint256[] memory values, address token) external {
        require(toList.length == values.length, "Input arrays must have the same length");

        uint256 totalAmount = 0;
        for (uint256 i = 0; i < values.length; i++) {
            totalAmount += values[i];
        }

        require(IERC20(token).allowance(msg.sender, address(this)) >= totalAmount, "Insufficient allowance");

        for (uint256 i = 0; i < toList.length; i++) {
            address to = toList[i];
            uint256 value = values[i];

            require(to != address(0), "Recipient address cannot be zero");
            require(value != 0, "value cannot be zero");

            bool success = IERC20(token).transferFrom(msg.sender, to, value);
            require(success, "Token transfer failed");

            emit TransferToken(msg.sender, to, value, token);
        }
    }

    // 修改合约所有者
    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "New owner cannot be zero address");
        owner = newOwner;
    }

    // 提取合约中的 ETH
    function withdrawETH(address payable to, uint256 amount) external onlyOwner nonReentrant {
        require(address(this).balance >= amount, "Insufficient ETH balance");
        require(to != address(0), "Recipient address cannot be zero");
        payable(to).transfer(amount);
        emit WithdrawETH(to, amount);
    }

    // 提取任意 ERC20 代币
    function withdrawToken(address token, address to, uint256 amount) external onlyOwner nonReentrant {
        require(token != address(0), "Token address cannot be zero");
        require(to != address(0), "Recipient address cannot be zero");

        uint256 balance = IERC20(token).balanceOf(address(this));
        require(balance >= amount, "Insufficient token balance");

        bool success = IERC20(token).transfer(to, amount);
        require(success, "Token transfer failed");

        emit WithdrawToken(token, to, amount);
    }

    // 接受 ETH 转账
    receive() external payable {}
}
