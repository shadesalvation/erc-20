// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

abstract contract ERC20Core {
    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);
    event EtherReceived(address indexed sender, uint256 value, bytes data);

    string public name;
    string public symbol;
    uint8 public constant decimals = 18;
    uint256 public totalSupply;

    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    constructor(string memory tokenName, string memory tokenSymbol) {
        name = tokenName;
        symbol = tokenSymbol;
    }

    function transfer(address to, uint256 value) public virtual returns (bool);
    function approve(address spender, uint256 value) external virtual returns (bool);
    function transferFrom(address from, address to, uint256 value) external virtual returns (bool);

    function _afterTokenTransfer(address, address, uint256) internal virtual {}
}

contract MintableCore is ERC20Core {
    address private _minter;

    modifier onlyMinter() {
        require(msg.sender == _minter, "not minter");
        _;
    }

    constructor(string memory tokenName, string memory tokenSymbol) ERC20Core(tokenName, tokenSymbol) {
        _minter = msg.sender;
    }

    function minter() public view returns (address) {
        return _minter;
    }

    function mint(address to, uint256 value) external onlyMinter {
        _mint(to, value);
    }

    function _mint(address to, uint256 value) internal {
        require(to != address(0), "zero");
        totalSupply += value;
        balanceOf[to] += value;
        emit Transfer(address(0), to, value);
        _afterTokenTransfer(address(0), to, value);
    }
}

contract PayableInheritedERC20 is MintableCore {
    bytes public lastFallbackData;

    constructor(uint256 initialSupply) MintableCore("Inherited Payable Token", "IPT") payable {
        _mint(msg.sender, initialSupply);
    }

    receive() external payable {
        emit EtherReceived(msg.sender, msg.value, "");
    }

    fallback(bytes calldata data) external payable returns (bytes memory) {
        lastFallbackData = data;
        emit EtherReceived(msg.sender, msg.value, data);
        return abi.encode(msg.sig, data.length);
    }

    function transfer(address to, uint256 value) public override returns (bool) {
        _move(msg.sender, to, value);
        return true;
    }

    function approve(address spender, uint256 value) external override returns (bool) {
        allowance[msg.sender][spender] = value;
        emit Approval(msg.sender, spender, value);
        return true;
    }

    function transferFrom(address from, address to, uint256 value) external override returns (bool) {
        uint256 current = allowance[from][msg.sender];
        require(current >= value, "allowance");
        allowance[from][msg.sender] = current - value;
        _move(from, to, value);
        return true;
    }

    function _move(address from, address to, uint256 value) internal {
        require(to != address(0), "zero");
        uint256 bal = balanceOf[from];
        require(bal >= value, "balance");
        balanceOf[from] = bal - value;
        balanceOf[to] += value;
        emit Transfer(from, to, value);
        _afterTokenTransfer(from, to, value);
    }

    function _afterTokenTransfer(address from, address to, uint256 value) internal override {
        super._afterTokenTransfer(from, to, value);
    }
}

