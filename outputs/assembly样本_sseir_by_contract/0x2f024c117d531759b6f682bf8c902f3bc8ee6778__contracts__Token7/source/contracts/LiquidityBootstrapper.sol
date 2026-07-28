// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

/// @notice 标准 ERC20 接口（精简版）
interface IERC20 {
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 value) external returns (bool);
    function approve(address spender, uint256 value) external returns (bool);
    function transferFrom(address from, address to, uint256 value) external returns (bool);
}

/// @notice WETH/WBNB 接口
interface IWETH {
    function deposit() external payable;
    function transfer(address to, uint256 value) external returns (bool);
    function balanceOf(address) external view returns (uint256);
}

/// @notice Pair 接口（UniswapV2/PancakeV2）
interface IUniswapV2Pair {
    function mint(address to) external returns (uint256 liquidity);
    function getReserves()
        external
        view
        returns (uint112 reserve0, uint112 reserve1, uint32 blockTimestampLast);
}

/// @notice Router 接口
interface IUniswapV2Router02 {
    function factory() external view returns (address);
    function WETH() external view returns (address);

    function addLiquidityETH(
        address token,
        uint amountTokenDesired,
        uint amountTokenMin,
        uint amountETHMin,
        address to,
        uint deadline
    )
        external
        payable
        returns (
            uint amountToken,
            uint amountETH,
            uint liquidity
        );
}

/// @notice Factory 接口
interface IUniswapV2Factory {
    function getPair(address tokenA, address tokenB) external view returns (address pair);
    function createPair(address tokenA, address tokenB) external returns (address pair);
}

/// @title LiquidityBootstrapper
/// @notice 复用型：
///  - 构造时固定 router / weth / factory
///  - 每次调用传入不同 token，自动 get/create pair，然后：
///    1）先用一笔小 LP 手动 mint，定价；
///    2）再用 addLiquidityETH 加大 LP。
contract LiquidityBootstrapper {
    address public immutable weth;
    address public immutable router;
    address public immutable factory;

    address public owner;

    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event InitLiquidity(
        address indexed token,
        address indexed pair,
        uint256 tokenInit,
        uint256 bnbInit,
        uint256 liquidityInit
    );
    event AddLiquidity(
        address indexed token,
        address indexed pair,
        uint256 tokenAdded,
        uint256 bnbAdded,
        uint256 liquidityAdded
    );

    modifier onlyOwner() {
        require(msg.sender == owner, "NOT_OWNER");
        _;
    }

    constructor(address _router, address _weth) {
        require(_router != address(0), "ROUTER_ZERO");
        require(_weth != address(0), "WETH_ZERO");

        router = _router;
        weth   = _weth;

        owner = msg.sender;
        emit OwnershipTransferred(address(0), msg.sender);

        address f = IUniswapV2Router02(_router).factory();
        require(f != address(0), "NO_FACTORY");
        factory = f;
    }

    /// @notice 一笔 tx 内完成：首笔 mint + addLiquidityETH
    /// @param token               本次要建池的代币地址（每次可以不同）
    /// @param tokenAmountForInit  第一笔小额 token（定价用）
    /// @param bnbAmountForInit    第一笔小额 BNB（定价用，包含在 msg.value 里）
    /// @param tokenAmountForBigLp 第二笔 addLiquidityETH 用的大额 token 数
    /// @param amountTokenMin      第二笔 addLiquidityETH 的最小 token（防滑点）
    /// @param amountETHMin        第二笔 addLiquidityETH 的最小 BNB（防滑点）
    /// @param deadline            router 的 deadline
    ///
    /// @dev 要求：
    ///  - 调用前，token.approve(本合约, tokenAmountForInit + tokenAmountForBigLp)
    ///  - msg.value = bnbAmountForInit + bnbAmountForBigLp
    function initAndAddLiquidity(
        address token,
        uint256 tokenAmountForInit,
        uint256 bnbAmountForInit,
        uint256 tokenAmountForBigLp,
        uint256 amountTokenMin,
        uint256 amountETHMin,
        uint256 deadline
    ) external payable {
        require(token != address(0), "TOKEN_ZERO");
        require(deadline >= block.timestamp, "DEADLINE");
        require(tokenAmountForInit > 0, "INIT_TOKEN_ZERO");
        require(bnbAmountForInit > 0, "INIT_BNB_ZERO");
        require(tokenAmountForBigLp > 0, "BIG_TOKEN_ZERO");
        require(msg.value > bnbAmountForInit, "BNB_TOO_SMALL");

        // 计算第二部分 BNB
        uint256 bnbAmountForBigLp = msg.value - bnbAmountForInit;

        // --------- 找到或者创建 pair（针对当前 token） ---------
        address pair = IUniswapV2Factory(factory).getPair(token, weth);
        if (pair == address(0)) {
            pair = IUniswapV2Factory(factory).createPair(token, weth);
        }

        // --------- Step 0: 拉 token 进来（两部分总量） ---------
        {
            uint256 totalTokenNeeded = tokenAmountForInit + tokenAmountForBigLp;
            bool ok = IERC20(token).transferFrom(msg.sender, address(this), totalTokenNeeded);
            require(ok, "TRANSFER_FROM_FAIL");
        }

        // --------- Step 1: 小 LP -> 手动 mint 首笔，给池子定价 ---------
        // 1.1 wrap 第一部分 BNB
        IWETH(weth).deposit{value: bnbAmountForInit}();

        // 1.2 token + WBNB 转到 pair
        require(IERC20(token).transfer(pair, tokenAmountForInit), "T_INIT_TO_PAIR_FAIL");
        require(IWETH(weth).transfer(pair, bnbAmountForInit), "W_INIT_TO_PAIR_FAIL");

        // 1.3 mint 首笔 LP（dust 会在这里抵消）
        uint256 liquidityInit = IUniswapV2Pair(pair).mint(msg.sender);
        emit InitLiquidity(token, pair, tokenAmountForInit, bnbAmountForInit, liquidityInit);

        // --------- Step 2: 用 router.addLiquidityETH 加大 LP ---------
        // 2.1 授权 router 使用第二部分 token
        require(
            IERC20(token).approve(router, tokenAmountForBigLp),
            "APPROVE_ROUTER_FAIL"
        );

        // 2.2 真正大额加池
        (
            uint256 usedToken,
            uint256 usedETH,
            uint256 liquidityAdded
        ) = IUniswapV2Router02(router).addLiquidityETH{value: bnbAmountForBigLp}(
            token,
            tokenAmountForBigLp,
            amountTokenMin,
            amountETHMin,
            msg.sender,
            deadline
        );

        emit AddLiquidity(token, pair, usedToken, usedETH, liquidityAdded);

        // --------- Step 3: 把多余的 token / BNB 退回 owner ---------
        uint256 remainingToken = IERC20(token).balanceOf(address(this));
        if (remainingToken > 0) {
            IERC20(token).transfer(msg.sender, remainingToken);
        }

        uint256 remainingETH = address(this).balance;
        if (remainingETH > 0) {
            (bool sent, ) = msg.sender.call{value: remainingETH}("");
            require(sent, "REFUND_ETH_FAIL");
        }
    }

    /// @notice 手动解救任意 ERC20（保险）
    function rescueToken(address _token, uint256 amount) external onlyOwner {
        require(IERC20(_token).transfer(msg.sender, amount), "RESCUE_FAIL");
    }

    /// @notice 手动解救 ETH（保险）
    function rescueETH(uint256 amount) external onlyOwner {
        (bool sent, ) = msg.sender.call{value: amount}("");
        require(sent, "RESCUE_ETH_FAIL");
    }

    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "ZERO_OWNER");
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    receive() external payable {}
}
