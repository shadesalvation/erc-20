// SPDX-License-Identifier: MIXED
/* S-SEIR flattened branch-preprocessed source. */
/* Entry: /home/ubuntu/erc-20/TOKENS/assembly样本/0x2f024c117d531759b6f682bf8c902f3bc8ee6778/contracts/Utils.sol */

pragma solidity ^0.8.27;


/* S-SEIR flatten import: @openzeppelin/contracts/utils/ReentrancyGuard.sol -> /home/ubuntu/erc-20/TOKENS/assembly样本/0x2f024c117d531759b6f682bf8c902f3bc8ee6778/@openzeppelin/contracts/utils/ReentrancyGuard.sol */
// OpenZeppelin Contracts (last updated v5.1.0) (utils/ReentrancyGuard.sol)

pragma solidity ^0.8.20;

/**
 * @dev Contract module that helps prevent reentrant calls to a function.
 *
 * Inheriting from `ReentrancyGuard` will make the {nonReentrant} modifier
 * available, which can be applied to functions to make sure there are no nested
 * (reentrant) calls to them.
 *
 * Note that because there is a single `nonReentrant` guard, functions marked as
 * `nonReentrant` may not call one another. This can be worked around by making
 * those functions `private`, and then adding `external` `nonReentrant` entry
 * points to them.
 *
 * TIP: If EIP-1153 (transient storage) is available on the chain you're deploying at,
 * consider using {ReentrancyGuardTransient} instead.
 *
 * TIP: If you would like to learn more about reentrancy and alternative ways
 * to protect against it, check out our blog post
 * https://blog.openzeppelin.com/reentrancy-after-istanbul/[Reentrancy After Istanbul].
 */
abstract contract ReentrancyGuard {
    // Booleans are more expensive than uint256 or any type that takes up a full
    // word because each write operation emits an extra SLOAD to first read the
    // slot's contents, replace the bits taken up by the boolean, and then write
    // back. This is the compiler's defense against contract upgrades and
    // pointer aliasing, and it cannot be disabled.

    // The values being non-zero value makes deployment a bit more expensive,
    // but in exchange the refund on every call to nonReentrant will be lower in
    // amount. Since refunds are capped to a percentage of the total
    // transaction's gas, it is best to keep them low in cases like this one, to
    // increase the likelihood of the full refund coming into effect.
    uint256 private constant NOT_ENTERED = 1;
    uint256 private constant ENTERED = 2;

    uint256 private _status;

    /**
     * @dev Unauthorized reentrant call.
     */
    error ReentrancyGuardReentrantCall();

    constructor() {
        _status = NOT_ENTERED;
    }

    /**
     * @dev Prevents a contract from calling itself, directly or indirectly.
     * Calling a `nonReentrant` function from another `nonReentrant`
     * function is not supported. It is possible to prevent this from happening
     * by making the `nonReentrant` function external, and making it call a
     * `private` function that does the actual work.
     */
    modifier nonReentrant() {
        _nonReentrantBefore();
        _;
        _nonReentrantAfter();
    }

    function _nonReentrantBefore() private {
        // On the first call to nonReentrant, _status will be NOT_ENTERED
        if (_status == ENTERED) {
            revert ReentrancyGuardReentrantCall();
        }

        // Any calls to nonReentrant after this point will fail
        _status = ENTERED;
    }

    function _nonReentrantAfter() private {
        // By storing the original value once again, a refund is triggered (see
        // https://eips.ethereum.org/EIPS/eip-2200)
        _status = NOT_ENTERED;
    }

    /**
     * @dev Returns true if the reentrancy guard is currently set to "entered", which indicates there is a
     * `nonReentrant` function in the call stack.
     */
    function _reentrancyGuardEntered() internal view returns (bool) {
        return _status == ENTERED;
    }
}
/* S-SEIR end import: @openzeppelin/contracts/utils/ReentrancyGuard.sol */


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
