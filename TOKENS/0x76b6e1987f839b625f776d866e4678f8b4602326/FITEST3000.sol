// SPDX-License-Identifier: MIT
pragma solidity ^0.8.5;

/* ─────────────────────────────────────────────────────────────────────────────
 *  Interfaces & Utils
 * ────────────────────────────────────────────────────────────────────────────*/
interface IERC20 {
    function totalSupply() external view returns (uint256);
    function balanceOf(address a) external view returns (uint256);
    function allowance(address o, address s) external view returns (uint256);
    function approve(address s, uint256 a) external returns (bool);
    function transfer(address to, uint256 a) external returns (bool);
    function transferFrom(address f, address t, uint256 a) external returns (bool);
    function decimals() external view returns (uint8);
}
interface IWBNB {
    function deposit() external payable;
    function withdraw(uint256) external;
    function balanceOf(address) external view returns (uint256);
    function transfer(address to, uint256 a) external returns (bool);
    function approve(address s, uint256 a) external returns (bool);
}
interface PancakeFactory { function createPair(address tokenA, address tokenB) external returns (address pair); }
interface PancakeRouter  { function factory() external view returns (address); }

/* ─────────────────────────────────────────────────────────────────────────────
 *  Context / Ownable / Minimal ERC20
 * ────────────────────────────────────────────────────────────────────────────*/
abstract contract Context { function _msgSender() internal view virtual returns (address) { return msg.sender; } }

contract Ownable is Context {
    address private _owner; event OwnershipTransferred(address indexed prev, address indexed next);
    constructor() { _transferOwnership(_msgSender()); }
    modifier onlyOwner(){ require(owner()==_msgSender(),"Ownable: not owner"); _; }
    function owner() public view returns(address){ return _owner; }
    function transferOwnership(address n) public onlyOwner { require(n!=address(0),"owner=0"); _transferOwnership(n); }
    function _transferOwnership(address n) internal { address o=_owner; _owner=n; emit OwnershipTransferred(o,n); }
}

contract ERC20 is Context {
    mapping(address=>uint256) internal _balances;
    mapping(address=>mapping(address=>uint256)) private _allowances;
    uint256 internal _totalSupply;
    string private _name; string private _symbol;
    event Transfer(address indexed from,address indexed to,uint256 v);
    event Approval(address indexed o,address indexed s,uint256 v);
    constructor(string memory n,string memory s){_name=n;_symbol=s;}
    function name() public view returns(string memory){return _name;}
    function symbol() public view returns(string memory){return _symbol;}
    function decimals() public pure virtual returns(uint8){return 18;}
    function totalSupply() public view returns(uint256){return _totalSupply;}
    function balanceOf(address a) public view returns(uint256){return _balances[a];}
    function transfer(address to,uint256 a) public virtual returns(bool){_transfer(_msgSender(),to,a);return true;}
    function allowance(address o,address s) public view returns(uint256){return _allowances[o][s];}
    function approve(address s,uint256 a) public virtual returns(bool){_approve(_msgSender(),s,a);return true;}
    function transferFrom(address f,address t,uint256 a) public virtual returns(bool){
        uint256 cur=_allowances[f][_msgSender()]; require(cur>=a,"ERC20: allowance");
        unchecked{_approve(f,_msgSender(),cur-a);} _transfer(f,t,a); return true;
    }
    function _transfer(address s,address r,uint256 a) internal virtual {
        require(s!=address(0)&&r!=address(0),"ERC20: zero");
        uint256 b=_balances[s]; require(b>=a,"ERC20: bal");
        unchecked{_balances[s]=b-a;} _balances[r]+=a; emit Transfer(s,r,a);
    }
    function _mint(address a,uint256 v) internal virtual { require(a!=address(0),"mint0"); _totalSupply+=v; _balances[a]+=v; emit Transfer(address(0),a,v); }
    function _burn(address a,uint256 v) internal virtual { require(a!=address(0),"burn0"); uint256 b=_balances[a]; require(b>=v,"burn>bal"); unchecked{_balances[a]=b-v;} _totalSupply-=v; emit Transfer(a,address(0),v); }
    function _approve(address o,address s,uint256 a) internal virtual { require(o!=address(0)&&s!=address(0),"approve0"); _allowances[o][s]=a; emit Approval(o,s,a); }
}

/* ─────────────────────────────────────────────────────────────────────────────
 *  SwapHelper
 * ────────────────────────────────────────────────────────────────────────────*/
contract SwapHelper is Ownable {
    function safeApprove(address token, address spender, uint256 amount) external onlyOwner {
        (bool ok, bytes memory data) = token.call(abi.encodeWithSelector(0x095ea7b3, spender, amount));
        require(ok && (data.length==0 || abi.decode(data,(bool))), "approve failed");
    }
}

/* ─────────────────────────────────────────────────────────────────────────────
 *  DividendTracker (1M = 1 cota)
 * ────────────────────────────────────────────────────────────────────────────*/
contract DividendTracker {
    uint256 private constant MAG = 2**128;

    IERC20 public immutable USDT;
    address public immutable WBNB;
    address public immutable TOKEN;
    uint256 public immutable MIN_HOLD; // 1_000_000e18

    mapping(address=>uint256) private idx;
    address[] private holders;
    mapping(address=>bool) public excluded;

    mapping(address=>uint256) public shares;
    uint256 public totalShares;

    uint256 public magnifiedUSDT;
    uint256 public magnifiedBNB;
    mapping(address=>int256) public corrUSDT;
    mapping(address=>int256) public corrBNB;
    mapping(address=>uint256) public withdrawnUSDT;
    mapping(address=>uint256) public withdrawnBNB;

    uint256 public totalDistributedUSDT;
    uint256 public totalDistributedBNB;

    uint256 public lastProcessedIndex;

    modifier onlyToken(){ require(msg.sender==TOKEN,"tracker: only token"); _; }

    constructor(address usdt,address wbnb,uint256 minHold) {
        USDT = IERC20(usdt);
        WBNB = wbnb;
        TOKEN = msg.sender;
        MIN_HOLD = minHold;
    }

    function isHolder(address a) public view returns(bool){ return idx[a]!=0 || (holders.length>0 && holders[0]==a); }
    function holderCount() external view returns(uint256){ return holders.length; }

    function excludeFromDividends(address a, bool v) external onlyToken {
        excluded[a]=v;
        if(v){ _setShares(a,0); }
    }

    function setBalance(address payable a, uint256 newTokenBalance) external onlyToken {
        if(excluded[a]) return;
        uint256 newShares = newTokenBalance / MIN_HOLD;
        _setShares(a, newShares);
    }

    function _setShares(address a, uint256 newShares) internal {
        uint256 prev = shares[a];
        if(newShares==prev) return;

        shares[a]=newShares;

        if(newShares>0 && prev==0){
            if(!isHolder(a)){ idx[a]=holders.length; holders.push(a); }
        } else if(newShares==0 && prev>0){
            uint256 i=idx[a]; address last=holders[holders.length-1];
            holders[i]=last; idx[last]=i; holders.pop(); idx[a]=0;
        }

        int256 dU = int256(magnifiedUSDT) * int256(newShares) - int256(magnifiedUSDT) * int256(prev);
        int256 dB = int256(magnifiedBNB)  * int256(newShares) - int256(magnifiedBNB)  * int256(prev);
        corrUSDT[a] -= dU; corrBNB[a] -= dB;

        if(newShares>prev) totalShares += (newShares - prev);
        else if(prev>newShares) totalShares -= (prev - newShares);
    }

    function distributeTokenDividends(uint256 amountUSDT) external onlyToken {
        if(amountUSDT==0 || totalShares==0) return;
        magnifiedUSDT += (amountUSDT * MAG) / totalShares;
        totalDistributedUSDT += amountUSDT;
    }

    function distributeNativeDividends() external payable onlyToken {
        uint256 amount = msg.value;
        if(amount==0 || totalShares==0) return;
        magnifiedBNB += (amount * MAG) / totalShares;
        totalDistributedBNB += amount;
    }

    function _withdrawableUSDT(address a) internal view returns(uint256) {
        uint256 accum = uint256(int256(magnifiedUSDT * shares[a]) + corrUSDT[a]) / MAG;
        if(accum <= withdrawnUSDT[a]) return 0;
        return accum - withdrawnUSDT[a];
    }
    function _withdrawableBNB(address a) internal view returns(uint256) {
        uint256 accum = uint256(int256(magnifiedBNB * shares[a]) + corrBNB[a]) / MAG;
        if(accum <= withdrawnBNB[a]) return 0;
        return accum - withdrawnBNB[a];
    }

    function process(uint256 gasLimit) external onlyToken returns (uint256 it, uint256 claims, uint256 newIndex) {
        uint256 n = holders.length; if(n==0) return (0,0,lastProcessedIndex);
        uint256 gasUsed=0; uint256 gasLeft=gasleft(); uint256 i=lastProcessedIndex;

        while(gasUsed < gasLimit && it < n){
            i = (i + 1) % n;
            address account = holders[i];

            uint256 amtU = _withdrawableUSDT(account);
            if(amtU>0){ withdrawnUSDT[account] += amtU; require(USDT.transfer(account, amtU), "USDT xfer"); claims++; }

            uint256 amtB = _withdrawableBNB(account);
            if(amtB>0){ withdrawnBNB[account] += amtB; (bool ok,) = payable(account).call(""); require(ok,"BNB xfer"); claims++; }

            it++;
            uint256 newGasLeft=gasleft(); if(gasLeft>newGasLeft) gasUsed += (gasLeft - newGasLeft); gasLeft=newGasLeft;
        }
        lastProcessedIndex=i; return (it,claims,i);
    }

    receive() external payable {}
}

/* ─────────────────────────────────────────────────────────────────────────────
 *  FITEST3000 – Token
 * ────────────────────────────────────────────────────────────────────────────*/
contract FITEST3000 is Ownable, ERC20 {
    uint256 private constant FEE_DENOM     = 10000;
    uint256 private constant SWAP_FEE_NUM  = 9975;

    address constant DEAD = 0x000000000000000000000000000000000000dEaD;
    address constant USDT = 0x55d398326f99059fF775485246999027B3197955; // BSC
    address constant WBNB = 0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c; // BSC
    address constant PANCAKE_ROUTER = 0x10ED43C718714eb63d5aA57B78B54704E256024E;

    // Nome e símbolo
    string  constant _n="FITEST3000";
    string  constant _s="FITEST3000";
    uint8   constant _dec=18;
    uint256 constant _max = 10_000_000_000 * 1e18;

    bool public tradingEnabled;

    // taxas
    uint256 public buyF1 = 300;                // 3% buy → dev1
    uint256 public sellDev1 = 200;             // 4% dev + 2% reflexão = 6%
    uint256 public sellDev2 = 200;
    uint256 public sellDev3 = 0;
    uint256 public sellReflection = 200;       // 2% holders
    function getSellTotal() public view returns(uint256){ return sellDev1+sellDev2+sellDev3+sellReflection; }

    // payout: 0=USDT (default), 1=BNB
    uint8 public sellFeePayout = 0;

    mapping(address=>bool) public exemptFee;
    mapping(address=>bool) public feePairs; // pares LP que disparam taxa
    address public liquidityPool;
    address public developingWallet1;
    address public developingWallet2;
    address public developingWallet3;

    // batching por US$ (threshold simples)
    uint256 public minUsdToSwap = 1 * 1e18;   // US$ 1.00 (ajustável)
    bool    public autoSwapOnSell = true;

    uint256 public gasForProcessing = 250_000;

    SwapHelper private swapHelper;
    address public WBNB_USDT_PAIR = 0x16b9a82891338f9bA80E2D6970FddA79D1eb0daE; // par WBNB/USDT
    address public WBNB_TOKEN_PAIR;

    DividendTracker public dividendTracker;

    // Operadores
    mapping(address => bool) public operators;
    event OperatorSet(address indexed op, bool enabled);

    // Robustez/controle
    bool public taxEnabled = false; // taxa global desligada até lançar
    event TaxEnabledSet(bool enabled);
    uint112 public minSaneReserve = 1000; // checagem leve para evitar swap em pools rasas
    event MinSaneReserveSet(uint112 value);

    // eventos diversos
    event FeePairUpdated(address indexed pair, bool enabled);
    event ReflectionPayout(uint8 mode, uint256 amount);
    event RescueBNB(address indexed to, uint256 amount);
    event RescueERC20(address indexed token, address indexed to, uint256 amount);

    // vesting fixo
    struct VestingInfo { uint256 initialBalance; uint256 lastReleaseTime; uint256 totalReleased; uint256 startVesting; }
    mapping(address=>VestingInfo) public vestingInfo;
    mapping(address=>bool) public isVestingWallet; mapping(address=>bool) public isBeneficiaryAdded;
    uint256 public constant releaseInterval=30 days;
    uint256 public constant releasePercentage=5;
    uint256 public constant vestingCycles=20;
    uint256 public constant vestingDuration=releaseInterval*vestingCycles;

    event VestingWalletWhitelisted(address indexed wallet);

    // ── Modificadores de acesso ────────────────────────
    modifier onlyOps() {
        require(msg.sender == owner() || operators[msg.sender], "not op/owner");
        _;
    }

    function setOperator(address op, bool enabled) external onlyOwner {
        operators[op] = enabled; emit OperatorSet(op, enabled);
    }

    constructor() ERC20(_n,_s) {
        PancakeRouter router = PancakeRouter(PANCAKE_ROUTER);
        WBNB_TOKEN_PAIR = PancakeFactory(router.factory()).createPair(WBNB, address(this));
        liquidityPool = WBNB_TOKEN_PAIR;
        feePairs[WBNB_TOKEN_PAIR]=true; emit FeePairUpdated(WBNB_TOKEN_PAIR,true);

        address ownerWallet = msg.sender;
        developingWallet1 = ownerWallet;
        developingWallet2 = ownerWallet;
        developingWallet3 = ownerWallet;

        exemptFee[address(this)]=true; exemptFee[DEAD]=true; exemptFee[ownerWallet]=true;

        SwapHelper sh = new SwapHelper(); swapHelper = sh;
        sh.safeApprove(WBNB, address(this), type(uint256).max);
        sh.safeApprove(USDT, address(this), type(uint256).max);
        sh.transferOwnership(_msgSender());

        // cotas: 1_000_000 * 1e18
        dividendTracker = new DividendTracker(USDT, WBNB, 1_000_000 * 1e18);
        dividendTracker.excludeFromDividends(address(this), true);
        dividendTracker.excludeFromDividends(DEAD, true);

        // ───────── Vesting wallets oficiais (7) ─────────
        address[7] memory vWallets = [
            0x4969915ed3d17C718eF00a1e954778b92eF79B28,
            0x987D21Eb8966145DcAd88423eD77bc372f9135e8,
            0x3783509Ff942fb35C1dd1F30B4069cE9bdeAa808,
            0x4806AA4Cd12F7288677efab6C9A88FD5062D2773,
            0x8B3fe154E411FA43AD60422339e3f8fa087baA87,
            0x3bFbde440c6d6C56ac59D5F72B4396C942AD08A8,
            0x65788eAD3f06226d3EF75Da9DE6Bf56Cac54D520
        ];
        for (uint i = 0; i < vWallets.length; i++) {
            require(vWallets[i] != address(0), "vesting zero");
            isVestingWallet[vWallets[i]] = true;
            emit VestingWalletWhitelisted(vWallets[i]);
        }
        // ────────────────────────────────────────────────

        _mint(ownerWallet, _max); tradingEnabled=false; transferOwnership(ownerWallet);
    }

    /* ── Admin / Ops ─────────────────────────────────── */
    function decimals() public pure override returns(uint8){ return _dec; }
    function getOwner() external view returns(address){ return owner(); }

    function enableTrading() external onlyOps { tradingEnabled=true; }

    // Splits/fees
    function setSellFeeSplits(uint256 f1,uint256 f2,uint256 f3) external onlyOps { require(f1+f2+f3+sellReflection<=600,"sell>6%"); sellDev1=f1; sellDev2=f2; sellDev3=f3; }
    function setSellFeeReflectionBps(uint256 r) external onlyOps { require(sellDev1+sellDev2+sellDev3+r<=600,"sell>6%"); sellReflection=r; }
    function setBuyFeeSplits(uint256 f1,uint256/*f2*/,uint256/*f3*/) external onlyOps { require(f1<=300,"buy>3%"); buyF1=f1; }
    function setSellFeePayout(uint8 m) external onlyOps { require(m==0||m==1,"mode"); sellFeePayout=m; }

    // Operação
    function setGasForProcessing(uint256 g) external onlyOps { require(g>=100_000&&g<=600_000,"gas"); gasForProcessing=g; }
    function setExemptFee(address a,bool v) external onlyOps { exemptFee[a]=v; }
    function setFeePair(address p,bool e) external onlyOps { feePairs[p]=e; emit FeePairUpdated(p,e); }
    function setWBNB_TOKEN_PAIR(address p) external onlyOps { if(WBNB_TOKEN_PAIR!=address(0)){feePairs[WBNB_TOKEN_PAIR]=false; emit FeePairUpdated(WBNB_TOKEN_PAIR,false);} WBNB_TOKEN_PAIR=p; liquidityPool=p; feePairs[p]=true; emit FeePairUpdated(p,true); }
    function setWBNB_USDT_PAIR(address p) external onlyOps { WBNB_USDT_PAIR=p; }

    /* ✅ agora operadores podem setar as dev wallets */
    function setDevelopingWallet(address a) external onlyOps { developingWallet1=a; }
    function setdevelopingWallet2(address a) external onlyOps { developingWallet2=a; }
    function setdevelopingWallet3(address a) external onlyOps { developingWallet3=a; }

    function setMinUsdToSwap(uint256 usd18) external onlyOps { require(usd18>0,"usd=0"); minUsdToSwap=usd18; }
    function setAutoSwapOnSell(bool e) external onlyOps { autoSwapOnSell=e; }

    function setTaxEnabled(bool e) external onlyOps { taxEnabled = e; emit TaxEnabledSet(e); }
    function setMinSaneReserve(uint112 v) external onlyOps { minSaneReserve = v; emit MinSaneReserveSet(v); }

    function quotaSize() external pure returns (uint256) { return 1_000_000 * 1e18; }

    // Reflexão inclusões/exclusões
    function excludeFromReflection(address account, bool excluded) external onlyOps {
        dividendTracker.excludeFromDividends(account, excluded);
        try dividendTracker.setBalance(payable(account), _balances[account]) {} catch {}
    }
    function excludeManyFromReflection(address[] calldata accounts, bool excluded) external onlyOps {
        for (uint256 i=0;i<accounts.length;i++){
            dividendTracker.excludeFromDividends(accounts[i], excluded);
            try dividendTracker.setBalance(payable(accounts[i]), _balances[accounts[i]]) {} catch {}
        }
    }

    // resgates
    receive() external payable {}
    function rescueBNB(uint256 amount, address payable to) external onlyOps {
        require(to!=address(0),"zero"); require(address(this).balance>=amount,"insufficient");
        (bool ok,)=to.call{value:amount}(""); require(ok,"BNB transfer failed"); emit RescueBNB(to, amount);
    }
    function rescueERC20(address token, uint256 amount, address to) external onlyOps {
        require(to!=address(0),"zero");
        bool failed=false; assembly{
            let p:=mload(0x40)
            mstore(p,0xa9059cbb00000000000000000000000000000000000000000000000000000000)
            mstore(add(p,0x04),to)
            mstore(add(p,0x24),amount)
            failed := iszero(call(gas(), token, 0, p, 0x44, 0, 0))
        } require(!failed,"ERC20 transfer failed");
        emit RescueERC20(token, to, amount);
    }

    // vesting (whitelist fixa)
    function addVestingBeneficiary(address b,uint256 init) external onlyOps {
        require(isVestingWallet[b],"Not vesting");
        require(b!=address(0) && init>0 && !isBeneficiaryAdded[b],"args");
        VestingInfo storage info=vestingInfo[b];
        info.initialBalance=init; info.lastReleaseTime=block.timestamp; info.totalReleased=0; info.startVesting=block.timestamp;
        _transfer(_msgSender(), b, init); isBeneficiaryAdded[b]=true;
    }

    /* ── Transfer com taxas ──────────────────────────── */
    bool private _noReentrancy=false;
    function _transfer(address s,address r,uint256 a) internal override {
        require(!_noReentrancy,"reentrancy"); _noReentrancy=true;
        require(s!=address(0)&&r!=address(0),"zero");
        require(tradingEnabled || exemptFee[s], "Trading disabled");

        // vesting (se usar)
        if (vestingInfo[s].initialBalance>0) {
            VestingInfo storage info=vestingInfo[s]; uint256 t=block.timestamp;
            if (t>=info.startVesting && t<info.startVesting+vestingDuration) {
                uint256 cycles=(t - info.startVesting)/releaseInterval; if(cycles>vestingCycles) cycles=vestingCycles;
                uint256 per=(info.initialBalance*releasePercentage)/100; uint256 maxRel=per*cycles;
                require(info.totalReleased<maxRel,"cycle cap"); uint256 rem=maxRel - info.totalReleased; require(a<=rem,"exceeds cycle");
                info.totalReleased+=a; info.lastReleaseTime=t;
            }
        }

        // Se taxa global desligada → transfere sem taxas
        if (!taxEnabled || exemptFee[s] || exemptFee[r]) {
            _balances[s]-=a; _balances[r]+=a; emit Transfer(s,r,a);
            try dividendTracker.setBalance(payable(s), _balances[s]) {} catch {}
            try dividendTracker.setBalance(payable(r), _balances[r]) {} catch {}
            _noReentrancy=false; return;
        }

        uint256 feeAmt=0; uint256 tAmt=a; bool recipPair = feePairs[r]; bool sendPair  = feePairs[s];

        if (recipPair && !exemptFee[s]) {
            uint256 stot = getSellTotal(); feeAmt = (a * stot) / FEE_DENOM; tAmt = a - feeAmt;

            // taxa em tokens → contrato
            _balances[s]-=a;
            _balances[address(this)]+=feeAmt; emit Transfer(s,address(this),feeAmt);

            // tenta varrer (gatilho US$) — somente se reservas forem "sãs"
            _sellSwapAndDistribute(0, false);

            // líquido → par
            _balances[r]+=tAmt; emit Transfer(s,r,tAmt);

        } else if (sendPair && !exemptFee[r]) {
            if (buyF1>0) { feeAmt=(a*buyF1)/FEE_DENOM; tAmt=a-feeAmt; _balances[s]-=a; _balances[developingWallet1]+=feeAmt; emit Transfer(s,developingWallet1,feeAmt); _balances[r]+=tAmt; emit Transfer(s,r,tAmt); }
            else { _balances[s]-=a; _balances[r]+=a; emit Transfer(s,r,a); }
        } else {
            _balances[s]-=a; _balances[r]+=a; emit Transfer(s,r,a);
        }

        // atualiza cotas
        try dividendTracker.setBalance(payable(s), _balances[s]) {} catch {}
        try dividendTracker.setBalance(payable(r), _balances[r]) {} catch {}

        _noReentrancy=false;
    }

    /* ── Reflexão: varre quando ≈ US$ minUsdToSwap ───── */
    function _sellSwapAndDistribute(uint256 /*ignored*/, bool force) internal {
        if (!taxEnabled) return; // não varre com taxa global off
        uint256 tokenBal = _balances[address(this)];
        if (tokenBal == 0) return;

        if (!force && autoSwapOnSell) {
            // Antes de estimar, checar se o par token/WBNB tem reservas "sãs"
            if (!_hasSaneReserves(WBNB_TOKEN_PAIR)) return; // acumula em token, sem revert
            uint256 estUsd = _estimateUsdtForTokens(tokenBal);
            if (estUsd < minUsdToSwap) return;
        } else if (!force) {
            return;
        }

        // TOKEN -> WBNB
        address pairWbnbToken = WBNB_TOKEN_PAIR;
        (uint112 r0,uint112 r1) = getTokenReserves(pairWbnbToken);
        bool rev = isReversed(pairWbnbToken, WBNB); if(rev){ (r0,r1)=(r1,r0); }

        // se reservas ainda não são "sãs", não tenta swapar
        if (r0<=minSaneReserve || r1<=minSaneReserve) return;

        _balances[address(this)] -= tokenBal;
        _balances[pairWbnbToken] += tokenBal;
        emit Transfer(address(this), pairWbnbToken, tokenBal);

        uint256 wbnbOut = getAmountOut(tokenBal, r1, r0);
        swapToken(pairWbnbToken, rev?0:wbnbOut, rev?wbnbOut:0, address(swapHelper));

        uint256 stot = getSellTotal(); if (stot==0) return;

        if (sellFeePayout == 0) {
            // WBNB -> USDT (requer par setado e com reservas "sãs")
            if (WBNB_USDT_PAIR == address(0)) return;
            if (!_hasSaneReserves(WBNB_USDT_PAIR)) return;

            address pairWbnbUsdt = WBNB_USDT_PAIR;
            (r0,r1) = getTokenReserves(pairWbnbUsdt);
            bool rev2 = isReversed(pairWbnbUsdt, WBNB); if(rev2){ (r0,r1)=(r1,r0); }

            tokenTransferFrom(WBNB, address(swapHelper), pairWbnbUsdt, wbnbOut);
            uint256 usdtOut = getAmountOut(wbnbOut, r0, r1);
            swapToken(pairWbnbUsdt, rev2?usdtOut:0, rev2?0:usdtOut, address(this));

            uint256 refUSDT = (usdtOut * sellReflection) / stot;
            uint256 devUSDT = usdtOut - refUSDT;

            if (devUSDT > 0) {
                uint256 devTot = sellDev1+sellDev2+sellDev3; if (devTot==0) devTot=1;
                if (sellDev1>0) tokenTransfer(USDT, developingWallet1, (devUSDT * sellDev1)/devTot);
                if (sellDev2>0) tokenTransfer(USDT, developingWallet2, (devUSDT * sellDev2)/devTot);
                if (sellDev3>0) tokenTransfer(USDT, developingWallet3, (devUSDT * sellDev3)/devTot);
            }

            if (refUSDT>0){
                tokenTransfer(USDT, address(dividendTracker), refUSDT);
                dividendTracker.distributeTokenDividends(refUSDT);
                dividendTracker.process(gasForProcessing);
                emit ReflectionPayout(0, refUSDT);
            }
        } else {
            // Reflexão em BNB
            uint256 refWBNB = wbnbOut * sellReflection / stot;
            uint256 forDev  = wbnbOut - refWBNB;

            if (forDev>0){
                if (WBNB_USDT_PAIR == address(0)) return;
                if (!_hasSaneReserves(WBNB_USDT_PAIR)) return;
                address pairWbnbUsdt = WBNB_USDT_PAIR;
                (r0,r1)=getTokenReserves(pairWbnbUsdt);
                bool rev2=isReversed(pairWbnbUsdt, WBNB); if(rev2){(r0,r1)=(r1,r0);} 
                tokenTransferFrom(WBNB, address(swapHelper), pairWbnbUsdt, forDev);
                uint256 usdtOut = getAmountOut(forDev, r0, r1);
                swapToken(pairWbnbUsdt, rev2?usdtOut:0, rev2?0:usdtOut, address(this));

                uint256 devTot = sellDev1+sellDev2+sellDev3; if (devTot==0) devTot=1;
                if (sellDev1>0) tokenTransfer(USDT, developingWallet1, (usdtOut * sellDev1)/devTot);
                if (sellDev2>0) tokenTransfer(USDT, developingWallet2, (usdtOut * sellDev2)/devTot);
                if (sellDev3>0) tokenTransfer(USDT, developingWallet3, (usdtOut * sellDev3)/devTot);
            }

            if (refWBNB>0){
                tokenTransferFrom(WBNB, address(swapHelper), address(this), refWBNB);
                IWBNB(WBNB).withdraw(refWBNB);
                dividendTracker.distributeNativeDividends{value: refWBNB}();
                dividendTracker.process(gasForProcessing);
                emit ReflectionPayout(1, refWBNB);
            }
        }
    }

    /* ── Helpers AMM ─────────────────────────────────── */
    function getTokenReserves(address pair) internal view returns (uint112 reserve0, uint112 reserve1) {
        bool failed=false; assembly{
            let p:=mload(0x40)
            mstore(p,0x0902f1ac00000000000000000000000000000000000000000000000000000000)
            failed := iszero(staticcall(gas(), pair, p, 0x04, p, 0x40))
            reserve0 := mload(p)
            reserve1 := mload(add(p,0x20))
        } require(!failed,"getReserves fail");
    }

    function _hasSaneReserves(address pair) internal view returns (bool) {
        (uint112 r0, uint112 r1) = getTokenReserves(pair);
        return (r0 > minSaneReserve && r1 > minSaneReserve);
    }

    function _estimateUsdtForTokens(uint256 tokenAmount) internal view returns (uint256 usdtOut) {
        if (tokenAmount == 0) return 0;

        // TOKEN -> WBNB
        address pairWbnbToken = WBNB_TOKEN_PAIR;
        (uint112 r0,uint112 r1) = getTokenReserves(pairWbnbToken);
        bool rev = isReversed(pairWbnbToken, WBNB); if(rev){ (r0,r1) = (r1,r0); }
        if (r0<=minSaneReserve || r1<=minSaneReserve) return 0;
        uint256 wbnbOut = getAmountOut(tokenAmount, r1, r0);
        if (wbnbOut == 0) return 0;

        // WBNB -> USDT
        address pairWbnbUsdt = WBNB_USDT_PAIR;
        (r0,r1) = getTokenReserves(pairWbnbUsdt);
        bool rev2 = isReversed(pairWbnbUsdt, WBNB); if(rev2){ (r0,r1) = (r1,r0); }
        if (r0<=minSaneReserve || r1<=minSaneReserve) return 0;
        usdtOut = getAmountOut(wbnbOut, r0, r1);
    }

    function getAmountOut(uint256 amountIn, uint256 reserveIn, uint256 reserveOut) internal pure returns (uint256 amountOut) {
        require(amountIn>0 && reserveIn>0 && reserveOut>0, "liq");
        uint256 amountInWithFee = amountIn * SWAP_FEE_NUM;
        uint256 num = amountInWithFee * reserveOut;
        uint256 den = (reserveIn * FEE_DENOM) + amountInWithFee;
        amountOut = num / den;
    }
    function isReversed(address pair, address tokenA) internal view returns (bool) {
        address token0; bool failed=false; assembly{
            let p:=mload(0x40)
            mstore(p,0x0dfe168100000000000000000000000000000000000000000000000000000000)
            failed := iszero(staticcall(gas(), pair, p, 0x04, p, 0x20))
            token0 := mload(p)
        } require(!failed,"dir"); return token0!=tokenA;
    }
    function tokenTransfer(address token, address to, uint256 a) internal {
        bool failed=false; assembly{
            let p:=mload(0x40)
            mstore(p,0xa9059cbb00000000000000000000000000000000000000000000000000000000)
            mstore(add(p,0x04),to) mstore(add(p,0x24),a)
            failed := iszero(call(gas(), token, 0, p, 0x44, 0, 0))
        } require(!failed,"xfer");
    }
    function tokenTransferFrom(address token,address from,address to,uint256 a) internal {
        bool failed=false; assembly{
            let p:=mload(0x40)
            mstore(p,0x23b872dd00000000000000000000000000000000000000000000000000000000)
            mstore(add(p,0x04),from) mstore(add(p,0x24),to) mstore(add(p,0x44),a)
            failed := iszero(call(gas(), token, 0, p, 0x64, 0, 0))
        } require(!failed,"xferFrom");
    }
    function swapToken(address pair,uint a0,uint a1,address to) internal {
        bool failed=false; assembly{
            let p:=mload(0x40)
            mstore(p,0x022c0d9f00000000000000000000000000000000000000000000000000000000)
            mstore(add(p,0x04),a0) mstore(add(p,0x24),a1) mstore(add(p,0x44),to)
            mstore(add(p,0x64),0x80) mstore(add(p,0x84),0)
            failed := iszero(call(gas(), pair, 0, p, 0xa4, 0, 0))
        } require(!failed,"swap");
    }

    function burn(uint256 a) external { _burn(_msgSender(), a); }
}