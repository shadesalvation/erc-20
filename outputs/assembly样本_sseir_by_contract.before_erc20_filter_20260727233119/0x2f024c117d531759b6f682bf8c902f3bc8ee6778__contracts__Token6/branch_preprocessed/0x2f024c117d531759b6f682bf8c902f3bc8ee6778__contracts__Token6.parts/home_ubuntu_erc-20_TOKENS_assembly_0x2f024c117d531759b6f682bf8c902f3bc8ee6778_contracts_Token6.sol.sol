// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.27;

import "@openzeppelin/contracts/utils/Strings.sol";


interface PancakeSwapFactory {
    function createPair(
        address tokenA,
        address tokenB
    ) external returns (address pair);

    function getPair(
        address tokenA,
        address tokenB
    ) external view returns (address pair);

    function feeTo() external view returns (address);
}

interface PancakeSwapRouter {
    function factory() external pure returns (address);

    function WETH() external pure returns (address);
    function swapExactTokensForETHSupportingFeeOnTransferTokens(
        uint amountIn,
        uint amountOutMin,
        address[] calldata path,
        address to,
        uint deadline
    ) external;
}

interface PancakeSwapPair {
    function getReserves()
        external
        view
        returns (uint112 reserve0, uint112 reserve1, uint32 blockTimestampLast);

    function token0() external view returns (address);
    function token1() external view returns (address);

    function balanceOf(address account) external view returns (uint256);

    function kLast() external view returns (uint256);

    function totalSupply() external view returns (uint256);
}

abstract contract Context {
    function _msgSender() internal view virtual returns (address) {
        return msg.sender;
    }

    function _msgData() internal view virtual returns (bytes calldata) {
        return msg.data;
    }

    function _contextSuffixLength() internal view virtual returns (uint256) {
        return 0;
    }
}

abstract contract Ownable is Context {
    address private _owner;

    /**
     * @dev The caller account is not authorized to perform an operation.
     */
    error OwnableUnauthorizedAccount(address account);

    /**
     * @dev The owner is not a valid owner account. (eg. `address(0)`)
     */
    error OwnableInvalidOwner(address owner);

    event OwnershipTransferred(
        address indexed previousOwner,
        address indexed newOwner
    );

    /**
     * @dev Initializes the contract setting the address provided by the deployer as the initial owner.
     */
    constructor(address initialOwner) {
        if (initialOwner == address(0)) {
            revert OwnableInvalidOwner(address(0));
        }
        _transferOwnership(initialOwner);
    }

    /**
     * @dev Throws if called by any account other than the owner.
     */
    modifier onlyOwner() {
        _checkOwner();
        _;
    }

    /**
     * @dev Returns the address of the current owner.
     */
    function owner() public view virtual returns (address) {
        return _owner;
    }

    /**
     * @dev Throws if the sender is not the owner.
     */
    function _checkOwner() internal view virtual {
        if (owner() != _msgSender()) {
            revert OwnableUnauthorizedAccount(_msgSender());
        }
    }

    /**
     * @dev Leaves the contract without owner. It will not be possible to call
     * `onlyOwner` functions. Can only be called by the current owner.
     *
     * NOTE: Renouncing ownership will leave the contract without an owner,
     * thereby disabling any functionality that is only available to the owner.
     */
    function renounceOwnership() public virtual onlyOwner {
        _transferOwnership(address(0));
    }

    /**
     * @dev Transfers ownership of the contract to a new account (`newOwner`).
     * Can only be called by the current owner.
     */
    function transferOwnership(address newOwner) public virtual onlyOwner {
        if (newOwner == address(0)) {
            revert OwnableInvalidOwner(address(0));
        }
        _transferOwnership(newOwner);
    }

    /**
     * @dev Transfers ownership of the contract to a new account (`newOwner`).
     * Internal function without access restriction.
     */
    function _transferOwnership(address newOwner) internal virtual {
        address oldOwner = _owner;
        _owner = newOwner;
        emit OwnershipTransferred(oldOwner, newOwner);
    }
}

interface IERC20 {
    /**
     * @dev Emitted when `value` tokens are moved from one account (`from`) to
     * another (`to`).
     *
     * Note that `value` may be zero.
     */
    event Transfer(address indexed from, address indexed to, uint256 value);

    /**
     * @dev Emitted when the allowance of a `spender` for an `owner` is set by
     * a call to {approve}. `value` is the new allowance.
     */
    event Approval(
        address indexed owner,
        address indexed spender,
        uint256 value
    );

    /**
     * @dev Returns the value of tokens in existence.
     */
    function totalSupply() external view returns (uint256);

    /**
     * @dev Returns the value of tokens owned by `account`.
     */
    function balanceOf(address account) external view returns (uint256);

    /**
     * @dev Moves a `value` amount of tokens from the caller's account to `to`.
     *
     * Returns a boolean value indicating whether the operation succeeded.
     *
     * Emits a {Transfer} event.
     */
    function transfer(address to, uint256 value) external returns (bool);

    /**
     * @dev Returns the remaining number of tokens that `spender` will be
     * allowed to spend on behalf of `owner` through {transferFrom}. This is
     * zero by default.
     *
     * This value changes when {approve} or {transferFrom} are called.
     */
    function allowance(
        address owner,
        address spender
    ) external view returns (uint256);

    /**
     * @dev Sets a `value` amount of tokens as the allowance of `spender` over the
     * caller's tokens.
     *
     * Returns a boolean value indicating whether the operation succeeded.
     *
     * IMPORTANT: Beware that changing an allowance with this method brings the risk
     * that someone may use both the old and the new allowance by unfortunate
     * transaction ordering. One possible solution to mitigate this race
     * condition is to first reduce the spender's allowance to 0 and set the
     * desired value afterwards:
     * https://github.com/ethereum/EIPs/issues/20#issuecomment-263524729
     *
     * Emits an {Approval} event.
     */
    function approve(address spender, uint256 value) external returns (bool);

    /**
     * @dev Moves a `value` amount of tokens from `from` to `to` using the
     * allowance mechanism. `value` is then deducted from the caller's
     * allowance.
     *
     * Returns a boolean value indicating whether the operation succeeded.
     *
     * Emits a {Transfer} event.
     */
    function transferFrom(
        address from,
        address to,
        uint256 value
    ) external returns (bool);
}

interface IERC20Metadata is IERC20 {
    /**
     * @dev Returns the name of the token.
     */
    function name() external view returns (string memory);

    /**
     * @dev Returns the symbol of the token.
     */
    function symbol() external view returns (string memory);

    /**
     * @dev Returns the decimals places of the token.
     */
    function decimals() external view returns (uint8);
}

interface IERC20Errors {
    /**
     * @dev Indicates an error related to the current `balance` of a `sender`. Used in transfers.
     * @param sender Address whose tokens are being transferred.
     * @param balance Current balance for the interacting account.
     * @param needed Minimum amount required to perform a transfer.
     */
    error ERC20InsufficientBalance(
        address sender,
        uint256 balance,
        uint256 needed
    );

    /**
     * @dev Indicates a failure with the token `sender`. Used in transfers.
     * @param sender Address whose tokens are being transferred.
     */
    error ERC20InvalidSender(address sender);

    /**
     * @dev Indicates a failure with the token `receiver`. Used in transfers.
     * @param receiver Address to which tokens are being transferred.
     */
    error ERC20InvalidReceiver(address receiver);

    /**
     * @dev Indicates a failure with the `spender`’s `allowance`. Used in transfers.
     * @param spender Address that may be allowed to operate on tokens without being their owner.
     * @param allowance Amount of tokens a `spender` is allowed to operate with.
     * @param needed Minimum amount required to perform a transfer.
     */
    error ERC20InsufficientAllowance(
        address spender,
        uint256 allowance,
        uint256 needed
    );

    /**
     * @dev Indicates a failure with the `approver` of a token to be approved. Used in approvals.
     * @param approver Address initiating an approval operation.
     */
    error ERC20InvalidApprover(address approver);

    /**
     * @dev Indicates a failure with the `spender` to be approved. Used in approvals.
     * @param spender Address that may be allowed to operate on tokens without being their owner.
     */
    error ERC20InvalidSpender(address spender);
}

contract TOKEN is Ownable, IERC20, IERC20Metadata, IERC20Errors {
    mapping(address => uint256) private _balances;

    mapping(address => mapping(address => uint256)) private _allowances;

    uint256 private _totalSupply;
    string private _name;
    string private _symbol;

    PancakeSwapRouter private _swapRouter;
    address public _mainPair;

    uint256 public _endHighTaxTime;

    uint256 public _highTaxDuration = 24 hours;

    uint256 public _buyFeeTax = 2;
    uint256 public _sellFeeTax = 4;
    uint256 public _sellHighFeeTax = 35;

    bool public _tradingEnabled;

    mapping(address => bool) public whiteList;

    address public marketAddress = 0xE5CaE46155E05Ed3239F1CCDaFA3Dc3aF87280Ba;

    address private constant WETH = 0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c;
    address private constant ROUTER =
        0x10ED43C718714eb63d5aA57B78B54704E256024E;
    PancakeSwapRouter public swapRouter;

    bool private swapping;

    modifier swapLock() {
        require(!swapping, "Swap in progress");
        swapping = true;
        _;
        swapping = false;
    }

    constructor(
        string memory name_,
        string memory symbol_,
        uint256 amount,
        address to
    ) Ownable(to) {
        _name = name_;
        _symbol = symbol_;
        _update(address(0), to, amount * 10 ** 18);

        swapRouter = PancakeSwapRouter(ROUTER);
        PancakeSwapFactory swapFactory = PancakeSwapFactory(
            swapRouter.factory()
        );
        address swapPair = swapFactory.createPair(address(this), WETH);
        _mainPair = swapPair;
        whiteList[to] = true;
        whiteList[marketAddress] = true;
        whiteList[address(this)] = true;
    }

    function name() public view virtual returns (string memory) {
        return _name;
    }

    function symbol() public view virtual returns (string memory) {
        return _symbol;
    }

    function decimals() public view virtual returns (uint8) {
        return 18;
    }

    function _getTotal() internal view returns (uint256) {
        return _totalSupply;
    }

    function totalSupply() public view virtual returns (uint256) {
        return _getTotal();
    }

    function _getBalance(address account) internal view returns (uint256) {
        return _balances[account];
    }

    function balanceOf(address account) public view virtual returns (uint256) {
        return _getBalance(account);
    }

    function transfer(address to, uint256 value) public virtual returns (bool) {
        address owner = _msgSender();
        _transfer(owner, to, value);
        return true;
    }

    function allowance(
        address owner,
        address spender
    ) public view virtual returns (uint256) {
        return _allowances[owner][spender];
    }

    function approve(
        address spender,
        uint256 value
    ) public virtual returns (bool) {
        address owner = _msgSender();
        _approve(owner, spender, value);
        return true;
    }

    function transferFrom(
        address from,
        address to,
        uint256 value
    ) public virtual returns (bool) {
        address spender = _msgSender();
        _spendAllowance(from, spender, value);
        _transfer(from, to, value);
        return true;
    }

    // function _isRemoveLiquidity(
    //     address from
    // ) internal view returns (bool) {
    //     if (from != _mainPair) return false;

    //     PancakeSwapPair mainPair = PancakeSwapPair(_mainPair);

    //     (uint112 reserve0, uint112 reserve1,) = mainPair.getReserves();

    //     address token0 = mainPair.token0();

    //     uint reserve;
    //     uint balance = IERC20(WETH).balanceOf(_mainPair);

    //     if (token0 == address(this)) {
    //         reserve = reserve1;
    //     } else {
    //         reserve = reserve0;
    //     }

    //     return balance < reserve;
    // }


    // function _isAddLiquidity(address to) internal view returns (bool) {
    //     if (to != _mainPair) return false;

    //     PancakeSwapPair mainPair = PancakeSwapPair(_mainPair);

    //     (uint112 reserve0, uint112 reserve1,) = mainPair.getReserves();

    //     address token0 = mainPair.token0();

    //     uint reserve;
    //     uint balance = IERC20(WETH).balanceOf(_mainPair);

    //     if (token0 == address(this)) {
    //         reserve = reserve1;
    //     } else {
    //         reserve = reserve0;
    //     }

    //     return balance > reserve;
    // }


    // function _isAddLiquidity() internal view returns (bool isAdd) {
    //     PancakeSwapPair mainPair = PancakeSwapPair(_mainPair);
    //     (uint r0, uint256 r1, ) = mainPair.getReserves();

    //     address tokenOther = WETH;
    //     uint256 r;
    //     if (tokenOther < address(this)) {
    //         r = r0;
    //     } else {
    //         r = r1;
    //     }

    //     uint bal = IERC20(tokenOther).balanceOf(address(mainPair));
    //     isAdd = bal > r;
    // }

    // function _isRemoveLiquidity() internal view returns (bool isRemove) {
    //     PancakeSwapPair mainPair = PancakeSwapPair(_mainPair);
    //     (uint r0, uint256 r1, ) = mainPair.getReserves();

    //     address tokenOther = WETH;
    //     uint256 r;
    //     if (tokenOther < address(this)) {
    //         r = r0;
    //     } else {
    //         r = r1;
    //     }

    //     uint bal = IERC20(tokenOther).balanceOf(address(mainPair));
    //     isRemove = r >= bal;
    // }

    // event ccc(
    //     address token0,
    //     address token1,
    //     uint256 balance0,
    //     uint256 balance1,
    //     uint112 reserve0,
    //     uint112 reserve1,
    //     bool isAdd
    // );

    // function _isAddLiquidity() internal view returns (bool) {
    //     PancakeSwapPair mainPair = PancakeSwapPair(_mainPair);
    //     (uint112 reserve0, uint112 reserve1,) = mainPair.getReserves();
    //     address token0 = mainPair.token0();
    //     address token1 = mainPair.token1();

    //     uint balance0 = IERC20(token0).balanceOf(_mainPair);
    //     uint balance1 = IERC20(token1).balanceOf(_mainPair);

    //     bool increased0 = balance0 > reserve0;
    //     bool increased1 = balance1 > reserve1;
    //     // 加流动性要求两个余额都相较于储备增加
    //     return increased0 && increased1;
    // }

    // function debugAddLiquidity() internal {

    //     PancakeSwapPair mainPair = PancakeSwapPair(_mainPair);
    //     (uint112 reserve0, uint112 reserve1,) = mainPair.getReserves();
    //     address token0 = mainPair.token0();
    //     address token1 = mainPair.token1();

    //     uint balance0 = IERC20(token0).balanceOf(_mainPair);
    //     uint balance1 = IERC20(token1).balanceOf(_mainPair);

    //     emit ccc(token0, token1, balance0, balance1, reserve0, reserve1, true);
    // }

    // function _isRemoveLiquidity() internal view returns (bool) {
    //     PancakeSwapPair mainPair = PancakeSwapPair(_mainPair);
    //     (uint112 reserve0, uint112 reserve1,) = mainPair.getReserves();
    //     address token0 = mainPair.token0();
    //     address token1 = mainPair.token1();

    //     uint balance0 = IERC20(token0).balanceOf(_mainPair);
    //     uint balance1 = IERC20(token1).balanceOf(_mainPair);

    //     bool decreased0 = balance0 < reserve0;
    //     bool decreased1 = balance1 < reserve1;

    //     // 减流动性要求两个余额都相较于储备减少
    //     return decreased0 && decreased1;
    // }

    // function debugRemoveLiquidity() internal {
    //     PancakeSwapPair mainPair = PancakeSwapPair(_mainPair);
    //     (uint112 reserve0, uint112 reserve1,) = mainPair.getReserves();
    //     address token0 = mainPair.token0();
    //     address token1 = mainPair.token1();

    //     uint balance0 = IERC20(token0).balanceOf(_mainPair);
    //     uint balance1 = IERC20(token1).balanceOf(_mainPair);

    //     bool decreased0 = balance0 < reserve0;
    //     bool decreased1 = balance1 < reserve1;

       
    //     emit ccc(token0, token1, balance0, balance1, reserve0, reserve1, false);

    // }


    function _isAddLiquidity(address from, address to,uint256 value) internal view returns (bool) {
        // 只有 Router 调用，并且是用户向主池子转账
        if (msg.sender != ROUTER) return false;
        if (to != _mainPair) return false;
        if (from == _mainPair) return false;
        if (value == 0) return false;
        return true;
    }

    function _isRemoveLiquidity(address from, address to,uint256 value) internal view returns (bool) {
        // 只有 Router 调用，并且是主池子向用户转账
        if (msg.sender != ROUTER) return false;
        if (from != _mainPair) return false;
        if (to == _mainPair) return false;
        if (value == 0) return false;
        return true;
    }

    event DebugTransfer(
        address from,
        address to,
        uint256 value,
        bool isAddLiq,
        bool isRemoveLiq,
        bool isBuy,
        bool isSell
    );
 
    function _transfer(address from, address to, uint256 value) internal {
        if (from == address(0)) {
            revert ERC20InvalidSender(address(0));
        }
        if (to == address(0)) {
            revert ERC20InvalidReceiver(address(0));
        }

        bool a = _isAddLiquidity(from,to,value);
        bool b = _isRemoveLiquidity(from,to,value);
        bool isAddLiq = true;
        bool isRemoveLiq = true;
        bool isBuy = (from == _mainPair) && !a && !b;
        bool isSell = (to == _mainPair) && !a && !b;

        // debugAddLiquidity();
        // debugRemoveLiquidity();

        emit DebugTransfer(
            from,
            to,
            value,
            a,
            b,
            isBuy,
            isSell
        );
        
        if (!_tradingEnabled) {
            if (!isAddLiq && !isRemoveLiq) {
                revert("Trading is disabled");
            }
        }

        uint256 finalAmount = value;
        uint256 feeAmount;

        if ((isBuy || isSell) && !whiteList[from] && !whiteList[to]) {
            if(isBuy){
                feeAmount = (value * _buyFeeTax) / 100;
            }else{
               if (_endHighTaxTime == 0 || block.timestamp <= _endHighTaxTime) {
                    feeAmount = (value * _sellHighFeeTax) / 100;
                } else {
                    feeAmount = (value * _sellFeeTax) / 100;
                }
            }
        }

        if (feeAmount > 0) {
            finalAmount = value - feeAmount;
            _update(from, address(this), feeAmount);

            if (!swapping && isSell) {
                _swapAndReward();
            }
        }

        _update(from, to, finalAmount);
    }

    function _update(address from, address to, uint256 value) internal virtual {
        if (from == address(0)) {
            // Overflow check required: The rest of the code assumes that totalSupply never overflows
            _totalSupply += value;
        } else {
            uint256 fromBalance = _balances[from];
            if (fromBalance < value) {
                revert ERC20InsufficientBalance(from, fromBalance, value);
            }
            unchecked {
                // Overflow not possible: value <= fromBalance <= totalSupply.
                _balances[from] = fromBalance - value;
            }
        }

        if (to == address(0)) {
            unchecked {
                // Overflow not possible: value <= totalSupply or value <= fromBalance <= totalSupply.
                _totalSupply -= value;
            }
        } else {
            unchecked {
                // Overflow not possible: balance + value is at most totalSupply, which we know fits into a uint256.
                _balances[to] += value;
            }
        }

        emit Transfer(from, to, value);
    }

    function _approve(address owner, address spender, uint256 value) internal {
        _approve(owner, spender, value, true);
    }

    function _approve(
        address owner,
        address spender,
        uint256 value,
        bool emitEvent
    ) internal virtual {
        if (owner == address(0)) {
            revert ERC20InvalidApprover(address(0));
        }
        if (spender == address(0)) {
            revert ERC20InvalidSpender(address(0));
        }
        _allowances[owner][spender] = value;
        if (emitEvent) {
            emit Approval(owner, spender, value);
        }
    }

    function _spendAllowance(
        address owner,
        address spender,
        uint256 value
    ) internal virtual {
        uint256 currentAllowance = allowance(owner, spender);
        if (currentAllowance != type(uint256).max) {
            if (currentAllowance < value) {
                revert ERC20InsufficientAllowance(
                    spender,
                    currentAllowance,
                    value
                );
            }
            unchecked {
                _approve(owner, spender, currentAllowance - value, false);
            }
        }
    }

    function setWhiteList(
        address[] memory addressList,
        bool w
    ) external onlyOwner {
        for (uint256 i = 0; i < addressList.length; i++) {
            whiteList[addressList[i]] = w;
        }
    }

    function startTrading() external onlyOwner {
        require(_endHighTaxTime == 0, "Trading already started");
        _endHighTaxTime = block.timestamp + _highTaxDuration;
        _tradingEnabled = true;
    }


    function _swapAndReward() internal swapLock {
        address[] memory path = new address[](2);

        path[0] = address(this);
        path[1] = WETH;
        uint256 balance = balanceOf(address(this));
        require(balance > 0, "No tokens to swap");

        _approve(address(this), ROUTER, balance);

        swapRouter.swapExactTokensForETHSupportingFeeOnTransferTokens(
            balance,
            0,
            path,
            marketAddress,
            block.timestamp
        );

    }

    receive() external payable {}

}
