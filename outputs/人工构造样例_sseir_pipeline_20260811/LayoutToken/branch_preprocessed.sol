// SPDX-License-Identifier: MIXED
/* S-SEIR flattened branch-preprocessed source. */
/* Entry: /home/ubuntu/erc-20/人工构造样例/01_源文件布局_接口_库_文件级定义/contracts/LayoutToken.sol */

pragma solidity ^0.8.24;


/* S-SEIR flatten import: ./IERC20Like.sol -> /home/ubuntu/erc-20/人工构造样例/01_源文件布局_接口_库_文件级定义/contracts/IERC20Like.sol */
pragma solidity ^0.8.24;

/// @title ERC-20 compatible interface used by the handcrafted samples.
interface IERC20Like {
    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    function totalSupply() external view returns (uint256);
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 value) external returns (bool);
    function allowance(address owner, address spender) external view returns (uint256);
    function approve(address spender, uint256 value) external returns (bool);
    function transferFrom(address from, address to, uint256 value) external returns (bool);
}

interface IERC20MetadataLike is IERC20Like {
    function name() external view returns (string memory);
    function symbol() external view returns (string memory);
    function decimals() external view returns (uint8);
}
/* S-SEIR end import: ./IERC20Like.sol */


/* S-SEIR flatten import: ./TokenUnits.sol -> /home/ubuntu/erc-20/人工构造样例/01_源文件布局_接口_库_文件级定义/contracts/TokenUnits.sol */
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
/* S-SEIR end import: ./TokenUnits.sol */


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
