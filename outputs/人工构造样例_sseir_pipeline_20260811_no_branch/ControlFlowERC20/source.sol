// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

contract ControlFlowERC20 {
    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    string public name = "Control Flow Token";
    string public symbol = "CFT";
    uint8 public constant decimals = 18;
    uint256 public totalSupply;
    uint256 public transferBitmap;

    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    constructor(uint256 initialSupply) {
        _mint(msg.sender, initialSupply);
    }

    function transfer(address to, uint256 value) external returns (bool) {
        uint256 fee = value > 1000 ether ? value / 100 : 0;
        _move(msg.sender, to, value, fee);
        return true;
    }

    function approve(address spender, uint256 value) external returns (bool) {
        allowance[msg.sender][spender] = value;
        emit Approval(msg.sender, spender, value);
        return true;
    }

    function transferFrom(address from, address to, uint256 value) external returns (bool) {
        uint256 current = allowance[from][msg.sender];
        require(current >= value && to != address(0), "bad transferFrom");
        allowance[from][msg.sender] -= value;
        _move(from, to, value, 0);
        return true;
    }

    function distribute(address[] calldata receivers, uint256 amount) external returns (uint256 sent) {
        for (uint256 i = 0; i < receivers.length; ++i) {
            if (receivers[i] == address(0)) continue;
            if (sent >= 10) break;
            _move(msg.sender, receivers[i], amount, 0);
            sent++;
        }
    }

    function burnUntilBelow(uint256 targetSupply, uint256 step) external returns (uint256 burned) {
        require(step != 0, "step");
        while (totalSupply > targetSupply) {
            uint256 burnAmount = totalSupply - targetSupply > step ? step : totalSupply - targetSupply;
            _burn(msg.sender, burnAmount);
            burned += burnAmount;
        }
    }

    function fillPreview(uint256 length) external pure returns (uint256[] memory preview) {
        preview = new uint256[](length);
        uint256 i;
        do {
            if (length == 0) break;
            preview[i] = 2 ** i;
            ++i;
        } while (i < length);
    }

    function resetAllowance(address spender) external {
        delete allowance[msg.sender][spender];
        emit Approval(msg.sender, spender, 0);
    }

    function bitOps(uint256 x, uint256 y) external pure returns (uint256) {
        uint256 z = (x & y) | (x ^ y);
        z <<= 1;
        z >>= 1;
        z %= type(uint64).max;
        return ~z;
    }

    function _move(address from, address to, uint256 value, uint256 fee) internal {
        require(to != address(0) && (value != 0 || fee == 0), "bad");
        uint256 debit = value + fee;
        uint256 bal = balanceOf[from];
        require(bal >= debit, "balance");
        unchecked {
            balanceOf[from] = bal - debit;
        }
        balanceOf[to] += value;
        if (fee > 0) {
            totalSupply -= fee;
            emit Transfer(from, address(0), fee);
        }
        transferBitmap ^= uint160(to);
        emit Transfer(from, to, value);
    }

    function _mint(address to, uint256 value) internal {
        totalSupply += value;
        balanceOf[to] += value;
        emit Transfer(address(0), to, value);
    }

    function _burn(address from, uint256 value) internal {
        uint256 bal = balanceOf[from];
        require(bal >= value, "balance");
        unchecked {
            balanceOf[from] = bal - value;
            totalSupply -= value;
        }
        emit Transfer(from, address(0), value);
    }
}

