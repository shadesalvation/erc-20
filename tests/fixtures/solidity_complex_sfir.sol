// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IComplexQuoteOracle {
    function quote(address recipient, uint256 amount) external returns (uint256);
}

contract ComplexSoliditySFIR {
    struct Account {
        uint256 balance;
        uint256 quota;
        bool enabled;
    }

    mapping(address => Account) internal accounts;
    mapping(address => mapping(address => uint256)) internal allowances;

    event Settled(address indexed sender, address indexed recipient, uint256 amount, uint256 quote);
    event SettlementSkipped(address indexed sender, uint256 amount);

    function _discount(uint256 quote, uint256 quota) internal pure returns (uint256) {
        return quote > quota ? quota : quote;
    }

    function settle(
        IComplexQuoteOracle oracle,
        address recipient,
        address spender,
        uint256 amount,
        uint256[] calldata multipliers
    ) external returns (uint256) {
        require(recipient != address(0) && amount != 0, "invalid settlement");

        Account storage sender = accounts[msg.sender];
        require(sender.enabled, "sender disabled");

        uint256 weight = 1;
        for (uint256 i = 0; i < multipliers.length; ++i) {
            uint256 factor = multipliers[i];
            if (factor != 0) {
                weight += factor;
            }
        }

        uint256 requested = amount * weight;
        uint256 actual = requested > sender.quota ? sender.quota : requested;
        require(actual <= sender.balance, "insufficient balance");

        sender.balance -= actual;
        accounts[recipient].balance += actual;
        allowances[msg.sender][spender] = actual;

        uint256 quote = oracle.quote(recipient, actual);
        uint256 discounted = _discount(quote, sender.quota);

        if (discounted > 0) {
            accounts[recipient].quota += discounted;
            emit Settled(msg.sender, recipient, actual, discounted);
        } else {
            emit SettlementSkipped(msg.sender, actual);
        }

        return discounted;
    }
}
