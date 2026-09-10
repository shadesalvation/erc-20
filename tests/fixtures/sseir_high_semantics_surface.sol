// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

/// Surface fixture for completed S-SEIR Yul overlays.  Individual unit tests
/// verify the stricter MemorySSA/CFG preconditions; this source keeps their
/// high-level forms together for SFIR regression runs.
contract SSeirHighSemanticSurface {
    struct Pair {
        uint256 left;
        uint256 right;
    }

    function balanceOf(address) external pure returns (uint256) {
        return 0;
    }

    function abiProbe(address token, address account) external view returns (bool ok) {
        assembly {
            let payload := mload(0x40)
            mstore(payload, 0x70a0823100000000000000000000000000000000000000000000000000000000)
            mstore(add(payload, 0x04), account)
            ok := staticcall(gas(), token, payload, 0x24, 0, 0)
        }
    }

    function hashAndProbe(bytes memory data, address account)
        external
        view
        returns (bytes32 digest, bool ok)
    {
        Pair memory pair;
        assembly {
            // dynamic bytes data pointer + length -> BytesContentHash
            digest := keccak256(add(data, 0x20), mload(data))

            // named memory struct field reads/writes
            mstore(pair, digest)
            let left := mload(pair)
            mstore(add(pair, 0x20), left)

            // address zero guard plus a selector/argument ABI buffer
            if iszero(iszero(account)) {
                let payload := mload(0x40)
                mstore(payload, shl(224, 0x70a08231))
                mstore(add(payload, 0x04), account)
                ok := staticcall(gas(), address(), payload, 0x24, 0, 0)
            }
        }
    }

    function rawWord(uint256 value) external pure {
        assembly {
            mstore(0, value)
            return(0, 0x20)
        }
    }
}
