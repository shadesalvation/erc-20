// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

type TokenAmount is uint256;

struct MintPlan {
    address receiver;
    TokenAmount amount;
}

enum MintState {
    NotStarted,
    Minted,
    Cancelled
}

error ZeroAddress();
error ZeroTokenAmount();

uint8 constant ERC20_DECIMALS = 18;
uint256 constant ONE_TOKEN = 1 ether;

function unwrapAmount(TokenAmount amount) pure returns (uint256) {
    return TokenAmount.unwrap(amount);
}

function tokenAmountAdd(TokenAmount a, TokenAmount b) pure returns (TokenAmount) {
    return TokenAmount.wrap(TokenAmount.unwrap(a) + TokenAmount.unwrap(b));
}

using {tokenAmountAdd as +} for TokenAmount global;

library TokenAmountLib {
    function wrap(uint256 value) internal pure returns (TokenAmount) {
        if (value == 0) revert ZeroTokenAmount();
        return TokenAmount.wrap(value);
    }

    function add(TokenAmount a, TokenAmount b) internal pure returns (TokenAmount) {
        return a + b;
    }
}
