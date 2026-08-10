// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

contract AssemblyERC20 {
    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    string public name = "Assembly Token";
    string public symbol = "ASM";
    uint8 public constant decimals = 18;
    uint256 public totalSupply;

    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    constructor(uint256 initialSupply) {
        totalSupply = initialSupply;
        balanceOf[msg.sender] = initialSupply;
        emit Transfer(address(0), msg.sender, initialSupply);
    }

    function transfer(address to, uint256 value) external returns (bool) {
        _assemblyMove(msg.sender, to, value);
        return true;
    }

    function approve(address spender, uint256 value) external returns (bool) {
        allowance[msg.sender][spender] = value;
        emit Approval(msg.sender, spender, value);
        return true;
    }

    function transferFrom(address from, address to, uint256 value) external returns (bool) {
        uint256 current = allowance[from][msg.sender];
        require(current >= value, "allowance");
        unchecked {
            allowance[from][msg.sender] = current - value;
        }
        _assemblyMove(from, to, value);
        return true;
    }

    function selectorFromCalldata() external pure returns (bytes4 selector) {
        assembly {
            selector := shr(224, calldataload(0))
        }
    }

    function hashMemorySafe(bytes memory data) external pure returns (bytes32 h) {
        assembly ("memory-safe") {
            h := keccak256(add(data, 0x20), mload(data))
        }
    }

    function sumSkipping(uint256[] memory values) external pure returns (uint256 total) {
        assembly {
            function addOrLeave(a, b) -> r {
                if gt(a, not(b)) {
                    r := 0
                    leave
                }
                r := add(a, b)
            }

            let len := mload(values)
            let ptr := add(values, 0x20)
            for { let i := 0 } lt(i, len) { i := add(i, 1) } {
                let v := mload(add(ptr, mul(i, 0x20)))
                if eq(v, 0) { continue }
                if gt(v, 1000000) { break }

                switch and(v, 1)
                case 0 {
                    total := addOrLeave(total, v)
                }
                default {
                    total := addOrLeave(total, mul(v, 2))
                }
            }
        }
    }

    function rawContext() external payable returns (address who, uint256 value, uint256 size, uint256 gasLeft) {
        assembly {
            who := caller()
            value := callvalue()
            size := calldatasize()
            gasLeft := gas()
        }
    }

    function _assemblyMove(address from, address to, uint256 value) internal {
        require(to != address(0), "zero");
        assembly {
            mstore(0x00, from)
            mstore(0x20, balanceOf.slot)
            let fromSlot := keccak256(0x00, 0x40)
            let fromBalance := sload(fromSlot)
            if lt(fromBalance, value) {
                mstore(0x00, 0x08c379a0)
                revert(0x1c, 0x04)
            }

            mstore(0x00, to)
            mstore(0x20, balanceOf.slot)
            let toSlot := keccak256(0x00, 0x40)

            sstore(fromSlot, sub(fromBalance, value))
            sstore(toSlot, add(sload(toSlot), value))

            mstore(0x00, value)
            log3(
                0x00,
                0x20,
                0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef,
                from,
                to
            )
        }
    }
}

