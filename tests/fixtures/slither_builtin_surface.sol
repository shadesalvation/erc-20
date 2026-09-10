// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

error BuiltinFailure(uint256 value);

contract SlitherBuiltinSurface {
    mapping(address => uint256) private credits;
    function codecs(bytes calldata payload, uint256 value)
        external
        pure
        returns (bytes memory packed, uint256 decoded, bytes32 digest, uint256 modular)
    {
        packed = abi.encodePacked(value, uint256(7));
        decoded = abi.decode(payload, (uint256));
        digest = keccak256(packed);
        modular = addmod(value, 3, 11);
    }

    function guards(uint256 value) external pure {
        assert(value < 100);
        if (value == 0) revert BuiltinFailure(value);
    }

    function cryptography(
        bytes calldata payload,
        bytes32 hash,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) external view returns (bytes32 sha, bytes20 ripe, address signer, uint256 remaining, bytes32 priorBlock) {
        sha = sha256(payload);
        ripe = ripemd160(payload);
        signer = ecrecover(hash, v, r, s);
        remaining = gasleft();
        priorBlock = blockhash(block.number - 1);
    }

    function destroy(address payable beneficiary) external {
        selfdestruct(beneficiary);
    }

    function codeMetrics(address target) external view returns (uint256) {
        return target.code.length;
    }

    function accountMetrics(address target) external view returns (uint256 balance, bytes32 hash) {
        balance = target.balance;
        hash = target.codehash;
    }

    function payments(address payable recipient) external returns (bool sent) {
        sent = recipient.send(1);
        recipient.transfer(2);
    }

    function concatenate(bytes calldata left, bytes calldata right, string calldata prefix, string calldata suffix)
        external
        pure
        returns (bytes memory joinedBytes, string memory joinedString)
    {
        joinedBytes = bytes.concat(left, right);
        joinedString = string.concat(prefix, suffix);
    }

    function clearCredit(address account) external returns (uint256 previous) {
        previous = credits[account];
        delete credits[account];
    }

    function invert(bool value) external pure returns (bool) {
        return !value;
    }
}
