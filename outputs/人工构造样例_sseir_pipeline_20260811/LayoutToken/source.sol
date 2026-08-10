// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20MetadataLike as IERC20Meta} from "./IERC20Like.sol";
import {TokenAmount, TokenAmountLib, MintPlan, MintState, ERC20_DECIMALS, ONE_TOKEN, unwrapAmount, ZeroAddress} from "./TokenUnits.sol";

/// @title LayoutToken
/// @notice ERC-20 sample focused on source layout, imports, file-level declarations and libraries.
contract LayoutToken is IERC20Meta {
    using TokenAmountLib for uint256;
    using TokenAmountLib for TokenAmount;

    string public name;
    string public symbol;
    uint8 public constant decimals = ERC20_DECIMALS;

    uint256 public totalSupply;
    MintState public mintState;
    MintPlan public genesisPlan;

    mapping(address account => uint256 balance) public balanceOf;
    mapping(address owner => mapping(address spender => uint256 value)) public allowance;

    constructor(string memory tokenName, string memory tokenSymbol, address receiver) {
        if (receiver == address(0)) revert ZeroAddress();
        name = tokenName;
        symbol = tokenSymbol;
        TokenAmount plannedAmount = ONE_TOKEN.wrap() + TokenAmount.wrap(0);
        genesisPlan = MintPlan({receiver: receiver, amount: plannedAmount});
        _mint(genesisPlan.receiver, unwrapAmount(genesisPlan.amount));
        mintState = MintState.Minted;
    }

    function transfer(address to, uint256 value) external returns (bool) {
        _transfer(msg.sender, to, value);
        return true;
    }

    function approve(address spender, uint256 value) external returns (bool) {
        allowance[msg.sender][spender] = value;
        emit Approval(msg.sender, spender, value);
        return true;
    }

    function transferFrom(address from, address to, uint256 value) external returns (bool) {
        uint256 allowed = allowance[from][msg.sender];
        require(allowed >= value, "allowance");
        allowance[from][msg.sender] = allowed - value;
        _transfer(from, to, value);
        return true;
    }

    function _mint(address to, uint256 value) internal {
        totalSupply += value;
        balanceOf[to] += value;
        emit Transfer(address(0), to, value);
    }

    function _transfer(address from, address to, uint256 value) internal {
        if (to == address(0)) revert ZeroAddress();
        uint256 bal = balanceOf[from];
        require(bal >= value, "balance");
        balanceOf[from] = bal - value;
        balanceOf[to] += value;
        emit Transfer(from, to, value);
    }
}
