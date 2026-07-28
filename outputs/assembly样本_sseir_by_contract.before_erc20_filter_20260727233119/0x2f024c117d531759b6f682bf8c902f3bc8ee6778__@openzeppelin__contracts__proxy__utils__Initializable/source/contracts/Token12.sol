// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.27;

interface PancakeSwapFactory {
    function createPair(address tokenA, address tokenB) external returns (address pair);
    function getPair(address tokenA, address tokenB) external view returns (address pair);
    function feeTo() external view returns (address);
}

interface PancakeSwapRouter {
    function factory() external pure returns (address);
    function WETH() external pure returns (address);

    function swapExactTokensForETHSupportingFeeOnTransferTokens(
        uint amountIn,uint amountOutMin,address[] calldata path,address to,uint deadline
    ) external;

    function swapExactTokensForTokensSupportingFeeOnTransferTokens(
        uint amountIn,uint amountOutMin,address[] calldata path,address to,uint deadline
    ) external;
}

interface PancakeSwapPair {
    function getReserves() external view returns (uint112 reserve0,uint112 reserve1,uint32 blockTimestampLast);
    function token0() external view returns (address);
    function balanceOf(address account) external view returns (uint256);
    function kLast() external view returns (uint256);
    function totalSupply() external view returns (uint256);
}

abstract contract Context {
    function _msgSender() internal view virtual returns (address) { return msg.sender; }
    function _msgData() internal view virtual returns (bytes calldata) { return msg.data; }
    function _contextSuffixLength() internal view virtual returns (uint256) { return 0; }
}

abstract contract Ownable is Context {
    address private _owner;

    error OwnableUnauthorizedAccount(address account);
    error OwnableInvalidOwner(address owner);

    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    constructor(address initialOwner) {
        if (initialOwner == address(0)) { revert OwnableInvalidOwner(address(0)); }
        _transferOwnership(initialOwner);
    }

    modifier onlyOwner() { _checkOwner(); _; }

    function owner() public view virtual returns (address) { return _owner; }

    function _checkOwner() internal view virtual {
        if (owner() != _msgSender()) { revert OwnableUnauthorizedAccount(_msgSender()); }
    }

    function renounceOwnership() public virtual onlyOwner { _transferOwnership(address(0)); }

    function transferOwnership(address newOwner) public virtual onlyOwner {
        if (newOwner == address(0)) { revert OwnableInvalidOwner(address(0)); }
        _transferOwnership(newOwner);
    }

    function _transferOwnership(address newOwner) internal virtual {
        address oldOwner = _owner; _owner = newOwner; emit OwnershipTransferred(oldOwner, newOwner);
    }
}

interface IERC20 {
    event Transfer(address indexed from,address indexed to,uint256 value);
    event Approval(address indexed owner,address indexed spender,uint256 value);
    function totalSupply() external view returns (uint256);
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to,uint256 value) external returns (bool);
    function allowance(address owner,address spender) external view returns (uint256);
    function approve(address spender,uint256 value) external returns (bool);
    function transferFrom(address from,address to,uint256 value) external returns (bool);
}

interface IERC20Metadata is IERC20 {
    function name() external view returns (string memory);
    function symbol() external view returns (string memory);
    function decimals() external view returns (uint8);
}

interface IERC20Errors {
    error ERC20InsufficientBalance(address sender,uint256 balance,uint256 needed);
    error ERC20InvalidSender(address sender);
    error ERC20InvalidReceiver(address receiver);
    error ERC20InsufficientAllowance(address spender,uint256 allowance,uint256 needed);
    error ERC20InvalidApprover(address approver);
    error ERC20InvalidSpender(address spender);
}

contract ERC20 is Context, IERC20, IERC20Metadata {
    using SafeMath for uint256;

    mapping(address => uint256) internal _balances;
    mapping(address => mapping(address => uint256)) private _allowances;

    uint256 internal _totalSupply;

    string private _name;
    string private _symbol;

    constructor(string memory name_, string memory symbol_) { _name = name_; _symbol = symbol_; }

    function name() public view virtual override returns (string memory) { return _name; }
    function symbol() public view virtual override returns (string memory) { return _symbol; }
    function decimals() external view virtual override returns (uint8) { return 18; }
    function totalSupply() public view virtual override returns (uint256) { return _totalSupply; }
    function balanceOf(address account) public view virtual override returns (uint256) { return _balances[account]; }

    function transfer(address recipient,uint256 amount) public virtual override returns (bool) {
        _transfer(msg.sender, recipient, amount); return true;
    }

    function allowance(address owner,address spender) public view virtual override returns (uint256) {
        return _allowances[owner][spender];
    }

    function approve(address spender,uint256 amount) public virtual override returns (bool) {
        _approve(msg.sender, spender, amount); return true;
    }

    function transferFrom(address sender,address recipient,uint256 amount) public virtual override returns (bool) {
        _transfer(sender, recipient, amount);
        _approve(sender, msg.sender, _allowances[sender][msg.sender].sub(amount,"ERC20: transfer amount exceeds allowance"));
        return true;
    }

    function increaseAllowance(address spender,uint256 addedValue) public virtual returns (bool) {
        _approve(msg.sender, spender, _allowances[msg.sender][spender].add(addedValue)); return true;
    }

    function decreaseAllowance(address spender,uint256 subtractedValue) public virtual returns (bool) {
        _approve(msg.sender, spender, _allowances[msg.sender][spender].sub(subtractedValue,"ERC20: decreased allowance below zero"));
        return true;
    }

    function _transfer(address sender,address recipient,uint256 amount) internal virtual {
        require(sender != address(0), "ERC20: transfer from the zero address");
        require(recipient != address(0), "ERC20: transfer to the zero address");

        _beforeTokenTransfer(sender, recipient, amount);

        _balances[sender] = _balances[sender].sub(amount,"ERC20: transfer amount exceeds balance");
        _balances[recipient] = _balances[recipient].add(amount);
        emit Transfer(sender, recipient, amount);
    }

    function _mint(address account,uint256 amount) internal virtual {
        require(account != address(0), "ERC20: mint to the zero address");
        _beforeTokenTransfer(address(0), account, amount);
        _totalSupply = _totalSupply.add(amount);
        _balances[account] = _balances[account].add(amount);
        emit Transfer(address(0), account, amount);
    }

    function _burn(address account,uint256 amount) internal virtual {
        require(account != address(0), "ERC20: burn from the zero address");
        _beforeTokenTransfer(account, address(0), amount);
        _balances[account] = _balances[account].sub(amount,"ERC20: burn amount exceeds balance");
        _totalSupply = _totalSupply.sub(amount);
        emit Transfer(account, address(0), amount);
    }

    function _approve(address owner,address spender,uint256 amount) internal virtual {
        require(owner != address(0), "ERC20: approve from the zero address");
        require(spender != address(0), "ERC20: approve to the zero address");
        _allowances[owner][spender] = amount; emit Approval(owner, spender, amount);
    }

    function _beforeTokenTransfer(address from,address to,uint256 amount) internal virtual {}
}

contract TOKEN is Ownable, ERC20 {
    ETHBackDividendTracker public dividendTracker;

    address private _mainPair;

    uint8 private _inviterTax = 10;
    uint8 private _marketTax = 1;
    uint8 private _currencyDividendTax = 1;

    mapping(address => bool) public whiteList;
    mapping(address => mapping(address => bool)) public gx;
    mapping(address => address) public inviter;

    address public marketAddress = 0xa8d725B85292cd520cEF7d25a78Ab0145cfeF212;
    address private constant WETH  = 0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c;
    address private constant USDT  = 0x55d398326f99059fF775485246999027B3197955;
    address private constant ROUTER= 0x10ED43C718714eb63d5aA57B78B54704E256024E;

    mapping(address => bool) public ammPairs;

    PancakeSwapRouter public swapRouter;

    uint256 public minBindAmount;
    uint256 public swapAtAmount;
    uint256 public marketAmount;
    uint256 public currencyDividendAmount;

    uint256 public gasForProcessing = 300000;

    bool private swapping;
    modifier swapLock() { require(!swapping, "Swap in progress"); swapping = true; _; swapping = false; }

    event ProcessedDividendTracker(uint256 iterations,uint256 claims,uint256 lastProcessedIndex,bool indexed automatic,uint256 gas,address indexed processor);

    constructor(string memory name_,string memory symbol_,uint256 amount,address to)
        ERC20(name_,symbol_) Ownable(to)
    {
        swapRouter = PancakeSwapRouter(ROUTER);
        PancakeSwapFactory swapFactory = PancakeSwapFactory(swapRouter.factory());
        address swapPair = swapFactory.createPair(address(this), WETH);
        _mainPair = swapPair;

        whiteList[to] = true;
        whiteList[marketAddress] = true;
        whiteList[address(this)] = true;

        minBindAmount = 1 * 10 ** 18;
        swapAtAmount = 50000 * 10 ** 18;

        ammPairs[_mainPair] = true;

        dividendTracker = new ETHBackDividendTracker(10000 * 10 ** 18, USDT, address(this));

        dividendTracker.excludeFromDividends(address(dividendTracker));
        dividendTracker.excludeFromDividends(address(this));
        dividendTracker.excludeFromDividends(address(swapRouter));
        dividendTracker.excludeFromDividends(_mainPair);
        dividendTracker.excludeFromDividends(0x000000000000000000000000000000000000dEaD);
        
        _approve(address(this), ROUTER, type(uint256).max);

        _mint(to, amount * 10 ** 18);
    }

    function _getReserves() private view returns (uint256 rOther,uint256 rThis,uint256 balanceOther) {
        PancakeSwapPair mainPair = PancakeSwapPair(_mainPair);
        (uint256 r0, uint256 r1, ) = mainPair.getReserves();
        address tokenOther = WETH;
        if (tokenOther < address(this)) { rOther = r0; rThis = r1; } else { rOther = r1; rThis = r0; }
        balanceOther = IERC20(tokenOther).balanceOf(_mainPair);
    }

    function _isRemoveLiquidity(uint256 amount) internal view returns (uint256 liquidity) {
        (uint256 rOther, , uint256 balanceOther) = _getReserves();
        if (balanceOther <= rOther) {
            liquidity = (amount * PancakeSwapPair(_mainPair).totalSupply()) / (balanceOf(_mainPair) - amount);
        }
    }

    function _transfer(address from,address to,uint256 value) internal override {
        require(from != address(0), "ERC20: transfer from the zero address");
        require(to != address(0), "ERC20: transfer to the zero address");

        _bindInviter(from, to, value);

        if (from == _mainPair && _isRemoveLiquidity(value) > 0) {
            if (!whiteList[to] && to != ROUTER) { revert("remove liquidity not allowed"); }
        }

        uint256 finalAmount = value;
        uint256 inviterFee;
        uint256 marketFee;
        uint256 currencyDividendFee;

        // bool isBuy = from == _mainPair;
        // bool isSell = to == _mainPair;

        bool isBuy  = ammPairs[from];
        bool isSell = ammPairs[to];
        bool isTrade = isBuy || isSell;

        if ((isBuy || isSell) && !whiteList[from] && !whiteList[to]) {
            inviterFee = (value * _inviterTax) / 100;
            marketFee = (value * _marketTax) / 100;
            currencyDividendFee = (value * _currencyDividendTax) / 100;
        }

        if (marketFee > 0) {
            finalAmount = value - inviterFee - marketFee - currencyDividendFee;


            //address upperAddress = isBuy ? to : from;

            // address traderAddress;
            // if (isBuy) {
            //     traderAddress = _isContract(to) ? tx.origin : to;
            // } else if (isSell) {
            //     traderAddress = _isContract(from) ? tx.origin : from;
            // } else {
            //     traderAddress = from;
            // }
            // if (_isContract(traderAddress)) {
            //     traderAddress = tx.origin;
            // }

            address upper = inviter[tx.origin];

            uint256 feeAmount;
            if (upper != address(0)) {
                feeAmount = marketFee + currencyDividendFee;
                super._transfer(from, upper, inviterFee);
                marketAmount += marketFee;
            } else {
                feeAmount = inviterFee + marketFee + currencyDividendFee;
                marketAmount += inviterFee + marketFee;
            }

            currencyDividendAmount += currencyDividendFee;

            super._transfer(from, address(this), feeAmount);

            uint256 contractTokenBalance = balanceOf(address(this));
            bool canSwap = contractTokenBalance >= swapAtAmount;

            if (canSwap && !swapping && isSell) {
                uint256 numTokensSellToFund = marketAmount + currencyDividendAmount;
                if (numTokensSellToFund > contractTokenBalance) {
                    numTokensSellToFund = contractTokenBalance;
                }

                uint256 marketRatio   = (marketAmount * 1e18) / numTokensSellToFund;
                uint256 dividendRatio = 1e18 - marketRatio;

                marketAmount = 0;
                currencyDividendAmount = 0;

                _swapAndReward(numTokensSellToFund, marketRatio, dividendRatio);
            }
        }

        super._transfer(from, to, finalAmount);

        try dividendTracker.setBalance(payable(from), balanceOf(from)) {} catch {}
        try dividendTracker.setBalance(payable(to),   balanceOf(to))   {} catch {}
        // bool isTrade = ammPairs[from] || ammPairs[to];
        //        if (!swapping && (from == _mainPair || to == _mainPair)) {

        if (!swapping && isTrade) {
            uint256 gas = gasForProcessing;
            try dividendTracker.process(gas) returns (uint256 it, uint256 cl, uint256 idx) {
                emit ProcessedDividendTracker(it, cl, idx, true, gas, tx.origin);
            } catch {}
        }
    }

    function setWhiteList(address[] memory addressList,bool w) external onlyOwner {
        for (uint256 i = 0; i < addressList.length; i++) { whiteList[addressList[i]] = w; }
    }

    function _bindInviter(address from,address to,uint256 amount) internal {
        if (from == to) return;
        if (amount < minBindAmount) return;
        if (whiteList[from] || whiteList[to]) return;
        // if (from == _mainPair || to == _mainPair) return;
        if (ammPairs[from] || ammPairs[to]) return;
        // if (_isContract(from) || _isContract(to)) return;
      
        if (!gx[to][from]) { 
            gx[to][from] = true; 
        }

        if (
            inviter[from] == address(0) &&
            gx[from][to]
        ) {
            inviter[from] = to;
        }
    }

    function _swapAndReward(uint256 balance,uint256 marketRatio,uint256 dividendRatio) internal swapLock {
        
        address[] memory path = new address[](3);

        path[0] = address(this);
        path[1] = WETH;
        path[2] = USDT;    


        PancakeSwapRouter(ROUTER).swapExactTokensForTokensSupportingFeeOnTransferTokens(
            balance, 0, path, address(this), block.timestamp
        );

        uint256 usdtBal = IERC20(USDT).balanceOf(address(this));
        if (usdtBal == 0) return;

        uint256 fundShare     = (usdtBal * marketRatio) / 1e18;
        uint256 dividendShare = usdtBal - fundShare;

        if (fundShare > 0) { IERC20(USDT).transfer(marketAddress, fundShare); }
        if (dividendShare > 0) {
            IERC20(USDT).transfer(address(dividendTracker), dividendShare);
            dividendTracker.distributeETHDividends(dividendShare);
        }
    }

    function setSwapAtAmount(uint256 newValue) public onlyOwner { swapAtAmount = newValue; }
    
    function _isContract(address _addr) public view returns (bool) {
        uint32 size;
        assembly {
            size := extcodesize(_addr)
        }
        return (size > 0);
    }

    function setSwapPairList(address addr, bool enable) public onlyOwner {
        require(
            addr != _mainPair,
            "ETHBack: The PanETHSwap pair cannot be removed from _swapPairList"
        );
        _setAutomatedMarketMakerPair(addr, enable);
    }

    function _setAutomatedMarketMakerPair(address pair, bool value) private {
        require(
            ammPairs[pair] != value,
            "ETHBack: Automated market maker pair is already set to that value"
        );
        ammPairs[pair] = value;

        if (value) {
            dividendTracker.excludeFromDividends(pair);
        }
    }

    receive() external payable {}
}

library SafeMath {
    function add(uint256 a,uint256 b) internal pure returns (uint256) { uint256 c=a+b; require(c>=a,"SafeMath: addition overflow"); return c; }
    function sub(uint256 a,uint256 b) internal pure returns (uint256) { return sub(a,b,"SafeMath: subtraction overflow"); }
    function sub(uint256 a,uint256 b,string memory errorMessage) internal pure returns (uint256) { require(b<=a,errorMessage); return a-b; }
    function mul(uint256 a,uint256 b) internal pure returns (uint256) { if (a==0) return 0; uint256 c=a*b; require(c/a==b,"SafeMath: multiplication overflow"); return c; }
    function div(uint256 a,uint256 b) internal pure returns (uint256) { return div(a,b,"SafeMath: division by zero"); }
    function div(uint256 a,uint256 b,string memory errorMessage) internal pure returns (uint256) { require(b>0,errorMessage); return a/b; }
    function mod(uint256 a,uint256 b) internal pure returns (uint256) { return mod(a,b,"SafeMath: modulo by zero"); }
    function mod(uint256 a,uint256 b,string memory errorMessage) internal pure returns (uint256) { require(b!=0,errorMessage); return a%b; }
}

library SafeMathInt {
    int256 private constant MIN_INT256 = int256(1) << 255;
    function mul(int256 a,int256 b) internal pure returns (int256) { int256 c=a*b; require(c!=MIN_INT256 || (a & MIN_INT256)!=(b & MIN_INT256)); require((b==0)||(c/b==a)); return c; }
    function div(int256 a,int256 b) internal pure returns (int256) { require(b!=-1 || a!=MIN_INT256); return a/b; }
    function sub(int256 a,int256 b) internal pure returns (int256) { int256 c=a-b; require((b>=0 && c<=a)||(b<0 && c>a)); return c; }
    function add(int256 a,int256 b) internal pure returns (int256) { int256 c=a+b; require((b>=0 && c>=a)||(b<0 && c<a)); return c; }
    function abs(int256 a) internal pure returns (int256) { require(a!=MIN_INT256); return a<0 ? -a : a; }
    function toUint256Safe(int256 a) internal pure returns (uint256) { require(a>=0); return uint256(a); }
}

library SafeMathUint {
    function toInt256Safe(uint256 a) internal pure returns (int256) { int256 b=int256(a); require(b>=0); return b; }
}

interface DividendPayingTokenOptionalInterface {
    function withdrawableDividendOf(address _owner) external view returns (uint256);
    function withdrawnDividendOf(address _owner) external view returns (uint256);
    function accumulativeDividendOf(address _owner) external view returns (uint256);
}

interface DividendPayingTokenInterface {
    function dividendOf(address _owner) external view returns (uint256);
    function withdrawDividend() external;
    event DividendsDistributed(address indexed from,uint256 weiAmount);
    event DividendWithdrawn(address indexed to,uint256 weiAmount);
}

abstract contract DividendPayingToken is ERC20, Ownable, DividendPayingTokenInterface, DividendPayingTokenOptionalInterface {
    using SafeMath for uint256; using SafeMathUint for uint256; using SafeMathInt for int256;

    address public ETH; 
    uint256 internal constant magnitude = 2 ** 128;
    uint256 internal magnifiedDividendPerShare;

    mapping(address => int256) internal magnifiedDividendCorrections;
    mapping(address => uint256) internal withdrawnDividends;

    uint256 public totalDividendsDistributed;

    constructor(string memory _name,string memory _symbol,address RewardToken,address to)
        ERC20(_name, _symbol) Ownable(to)
    { ETH = RewardToken; }

    function distributeETHDividends(uint256 amount) public onlyOwner {
        require(totalSupply() > 0);
        if (amount > 0) {
            magnifiedDividendPerShare = magnifiedDividendPerShare.add((amount).mul(magnitude) / totalSupply());
            emit DividendsDistributed(msg.sender, amount);
            totalDividendsDistributed = totalDividendsDistributed.add(amount);
        }
    }

    function withdrawDividend() public virtual override { _withdrawDividendOfUser(payable(msg.sender)); }

    function _withdrawDividendOfUser(address payable user) internal returns (uint256) {
        uint256 _withdrawableDividend = withdrawableDividendOf(user);
        if (_withdrawableDividend > 0) {
            withdrawnDividends[user] = withdrawnDividends[user].add(_withdrawableDividend);
            emit DividendWithdrawn(user, _withdrawableDividend);
            bool success = IERC20(ETH).transfer(user, _withdrawableDividend);
            if (!success) {
                withdrawnDividends[user] = withdrawnDividends[user].sub(_withdrawableDividend);
                return 0;
            }
            return _withdrawableDividend;
        }
        return 0;
    }

    function dividendOf(address _owner) public view override returns (uint256) { return withdrawableDividendOf(_owner); }

    function withdrawableDividendOf(address _owner) public view override returns (uint256) {
        return accumulativeDividendOf(_owner).sub(withdrawnDividends[_owner]);
    }

    function withdrawnDividendOf(address _owner) public view override returns (uint256) {
        return withdrawnDividends[_owner];
    }

    function accumulativeDividendOf(address _owner) public view override returns (uint256) {
        return magnifiedDividendPerShare.mul(balanceOf(_owner)).toInt256Safe().add(magnifiedDividendCorrections[_owner]).toUint256Safe() / magnitude;
    }

    function _transfer(address from,address to,uint256 value) internal virtual override {
        require(false);
        int256 _magCorrection = magnifiedDividendPerShare.mul(value).toInt256Safe();
        magnifiedDividendCorrections[from] = magnifiedDividendCorrections[from].add(_magCorrection);
        magnifiedDividendCorrections[to]   = magnifiedDividendCorrections[to].sub(_magCorrection);
    }

    function _mint(address account,uint256 value) internal override {
        super._mint(account, value);
        magnifiedDividendCorrections[account] = magnifiedDividendCorrections[account].sub((magnifiedDividendPerShare.mul(value)).toInt256Safe());
    }

    function _burn(address account,uint256 value) internal override {
        super._burn(account, value);
        magnifiedDividendCorrections[account] = magnifiedDividendCorrections[account].add((magnifiedDividendPerShare.mul(value)).toInt256Safe());
    }

    function _setBalance(address account,uint256 newBalance) internal {
        uint256 currentBalance = balanceOf(account);
        if (newBalance > currentBalance) {
            uint256 mintAmount = newBalance.sub(currentBalance); _mint(account, mintAmount);
        } else if (newBalance < currentBalance) {
            uint256 burnAmount = currentBalance.sub(newBalance); _burn(account, burnAmount);
        }
    }
}

contract ETHBackDividendTracker is DividendPayingToken {
    using SafeMath for uint256;

    address[] public holders;
    mapping(address => uint256) public holderIndex; 
    mapping(address => bool)    public excludedFromDividends;

    uint256 public minimumTokenBalanceForDividends;
    uint256 public lastProcessedIndex;

    event ExcludeFromDividends(address indexed account);
    event IncludeInDividends(address indexed account);
    event AutoPaid(address indexed account, uint256 amount);

    constructor(uint256 mushHoldTokenAmount,address RewardToken,address to)
        DividendPayingToken("ETHBack_Dividen_Tracker","ETHBack_Dividend_Tracker",RewardToken,to)
    { minimumTokenBalanceForDividends = mushHoldTokenAmount; }

    function _transfer(address, address, uint256) internal pure override {
        require(false, "ETHBack_Dividend_Tracker: No transfers allowed");
    }

    function withdrawDividend() public pure override {
        require(false, "ETHBack_Dividend_Tracker: withdraw disabled; auto-distribution only");
    }

    function excludeFromDividends(address account) external onlyOwner {
        if (excludedFromDividends[account]) return;
        _autoPay(payable(account));
        excludedFromDividends[account] = true;
        _setBalance(account, 0);
        _removeHolder(account);
        emit ExcludeFromDividends(account);
    }

    function setMinBalance(uint256 v) external onlyOwner { minimumTokenBalanceForDividends = v; }

    function setBalance(address payable account,uint256 newBalance) external onlyOwner {
        if (excludedFromDividends[account]) return;

        if (newBalance >= minimumTokenBalanceForDividends) {
            _setBalance(account, newBalance);
            _addHolder(account);
            _autoPay(account);
        } else {
            _autoPay(account);
            _setBalance(account, 0);
            _removeHolder(account);
        }
    }

    function process(uint256 gas) external onlyOwner returns (uint256 iterations,uint256 claims,uint256 newIndex) {
        uint256 n = holders.length;
        if (n == 0) return (0,0,lastProcessedIndex);

        uint256 gasUsed = 0;
        uint256 gasLeft = gasleft();
        uint256 _index = lastProcessedIndex;

        while (gasUsed < gas && iterations < n) {
            _index++;
            if (_index >= n) _index = 0;

            address account = holders[_index];
            if (_autoPay(payable(account))) { claims++; }

            iterations++;
            uint256 newGasLeft = gasleft();
            if (gasLeft > newGasLeft) { unchecked { gasUsed += (gasLeft - newGasLeft); } }
            gasLeft = newGasLeft;
        }
        lastProcessedIndex = _index;
        return (iterations, claims, _index);
    }

    function _autoPay(address payable account) internal returns (bool) {
        uint256 amount = _withdrawDividendOfUser(account);
        if (amount > 0) { emit AutoPaid(account, amount); return true; }
        return false;
    }

    function _addHolder(address a) internal {
        if (holderIndex[a] == 0) {
            holders.push(a);
            holderIndex[a] = holders.length; // index + 1
            emit IncludeInDividends(a);
        }
    }

    function _removeHolder(address a) internal {
        uint256 idx1 = holderIndex[a];
        if (idx1 == 0) return;

        uint256 idx  = idx1 - 1;
        uint256 last = holders.length - 1;

        if (idx != last) {
            address lastAddr = holders[last];
            holders[idx] = lastAddr;
            holderIndex[lastAddr] = idx + 1;
        }
        holders.pop();
        holderIndex[a] = 0;
    }
}
