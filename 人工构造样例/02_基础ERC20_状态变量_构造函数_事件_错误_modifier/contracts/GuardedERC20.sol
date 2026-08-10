// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

error ERC20ZeroAddress();
error ERC20InsufficientBalance(address from, uint256 requested, uint256 available);
error ERC20InsufficientAllowance(address spender, uint256 requested, uint256 available);
error NotOwner(address caller);
error Reentered();

contract GuardedERC20 {
    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);
    event OwnerMint(address indexed receiver, uint256 value);
    event Audit(bytes32 indexed topic, bytes data) anonymous;

    string public name;
    string public symbol;
    uint8 public constant decimals = 18;
    uint256 public totalSupply;
    address public immutable owner;

    uint256 transient private transferLock;
    uint256 internal maxOwnerMint;
    bytes32 private deploymentSalt;

    mapping(address account => uint256 balance) public balanceOf;
    mapping(address owner => mapping(address spender => uint256 value)) public allowance;

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner(msg.sender);
        _;
    }

    modifier nonReentrant() {
        if (transferLock == 1) revert Reentered();
        transferLock = 1;
        _;
        transferLock = 0;
    }

    constructor(string memory tokenName, string memory tokenSymbol, uint256 initialSupply) {
        owner = msg.sender;
        name = tokenName;
        symbol = tokenSymbol;
        maxOwnerMint = initialSupply * 2;
        deploymentSalt = keccak256(abi.encodePacked(block.chainid, address(this), tokenName));
        _mint(msg.sender, initialSupply);
        emit Audit(keccak256("DEPLOY"), abi.encode(msg.sender, initialSupply));
    }

    function transfer(address to, uint256 value) external nonReentrant returns (bool) {
        _transfer(msg.sender, to, value);
        return true;
    }

    function approve(address spender, uint256 value) external returns (bool) {
        if (spender == address(0)) revert ERC20ZeroAddress();
        allowance[msg.sender][spender] = value;
        emit Approval(msg.sender, spender, value);
        return true;
    }

    function transferFrom(address from, address to, uint256 value) external nonReentrant returns (bool) {
        uint256 currentAllowance = allowance[from][msg.sender];
        if (currentAllowance < value) {
            revert ERC20InsufficientAllowance(msg.sender, value, currentAllowance);
        }
        allowance[from][msg.sender] = currentAllowance - value;
        emit Approval(from, msg.sender, allowance[from][msg.sender]);
        _transfer(from, to, value);
        return true;
    }

    function ownerMint(address receiver, uint256 value) external onlyOwner {
        require(totalSupply + value <= maxOwnerMint, "mint cap");
        _mint(receiver, value);
        emit OwnerMint(receiver, value);
        emit Audit(keccak256("OWNER_MINT"), abi.encode(receiver, value));
    }

    function deploymentInfo() external view returns (address, bytes32) {
        return (owner, deploymentSalt);
    }

    function _mint(address to, uint256 value) internal {
        if (to == address(0)) revert ERC20ZeroAddress();
        totalSupply += value;
        balanceOf[to] += value;
        emit Transfer(address(0), to, value);
    }

    function _transfer(address from, address to, uint256 value) internal {
        if (to == address(0)) revert ERC20ZeroAddress();
        uint256 bal = balanceOf[from];
        if (bal < value) revert ERC20InsufficientBalance(from, value, bal);
        balanceOf[from] = bal - value;
        balanceOf[to] += value;
        emit Transfer(from, to, value);
    }
}
