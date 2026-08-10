object "MiniERC20" {
    code {
        datacopy(0, dataoffset("Runtime"), datasize("Runtime"))
        return(0, datasize("Runtime"))
    }

    object "Runtime" {
        code {
            function revertZero() {
                revert(0, 0)
            }

            function balanceSlot(account) -> slot {
                mstore(0, account)
                mstore(32, 1)
                slot := keccak256(0, 64)
            }

            function allowanceSlot(owner, spender) -> slot {
                mstore(0, owner)
                mstore(32, 2)
                let inner := keccak256(0, 64)
                mstore(0, spender)
                mstore(32, inner)
                slot := keccak256(0, 64)
            }

            function emitTransfer(from, to, value) {
                mstore(0, value)
                log3(
                    0,
                    32,
                    0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef,
                    from,
                    to
                )
            }

            function emitApproval(owner, spender, value) {
                mstore(0, value)
                log3(
                    0,
                    32,
                    0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925,
                    owner,
                    spender
                )
            }

            function move(from, to, value) {
                if iszero(to) { revertZero() }

                let fromSlot := balanceSlot(from)
                let fromBalance := sload(fromSlot)
                if lt(fromBalance, value) { revertZero() }

                let toSlot := balanceSlot(to)
                sstore(fromSlot, sub(fromBalance, value))
                sstore(toSlot, add(sload(toSlot), value))
                emitTransfer(from, to, value)
            }

            let selector := shr(224, calldataload(0))

            switch selector
            case 0x18160ddd {
                // totalSupply()
                mstore(0, sload(0))
                return(0, 32)
            }
            case 0x70a08231 {
                // balanceOf(address)
                let account := shr(96, calldataload(4))
                mstore(0, sload(balanceSlot(account)))
                return(0, 32)
            }
            case 0xdd62ed3e {
                // allowance(address,address)
                let owner := shr(96, calldataload(4))
                let spender := shr(96, calldataload(36))
                mstore(0, sload(allowanceSlot(owner, spender)))
                return(0, 32)
            }
            case 0xa9059cbb {
                // transfer(address,uint256)
                let to := shr(96, calldataload(4))
                let value := calldataload(36)
                move(caller(), to, value)
                mstore(0, 1)
                return(0, 32)
            }
            case 0x095ea7b3 {
                // approve(address,uint256)
                let spender := shr(96, calldataload(4))
                let value := calldataload(36)
                sstore(allowanceSlot(caller(), spender), value)
                emitApproval(caller(), spender, value)
                mstore(0, 1)
                return(0, 32)
            }
            case 0x23b872dd {
                // transferFrom(address,address,uint256)
                let from := shr(96, calldataload(4))
                let to := shr(96, calldataload(36))
                let value := calldataload(68)
                let slot := allowanceSlot(from, caller())
                let allowed := sload(slot)
                if lt(allowed, value) { revertZero() }
                sstore(slot, sub(allowed, value))
                move(from, to, value)
                mstore(0, 1)
                return(0, 32)
            }
            default {
                revertZero()
            }
        }
    }

    data "Note" "Handcrafted Yul ERC20 runtime sample"
}

