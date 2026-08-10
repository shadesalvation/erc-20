// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20View {
    function totalSupply() external view returns (uint256);
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 value) external returns (bool);
    function approve(address spender, uint256 value) external returns (bool);
}

contract FactoryMintedERC20 {
    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    string public name;
    string public symbol;
    uint8 public constant decimals = 18;
    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    constructor(string memory tokenName, string memory tokenSymbol, address receiver, uint256 initialSupply) payable {
        name = tokenName;
        symbol = tokenSymbol;
        _mint(receiver, initialSupply);
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
        uint256 current = allowance[from][msg.sender];
        require(current >= value, "allowance");
        allowance[from][msg.sender] = current - value;
        _transfer(from, to, value);
        return true;
    }

    function _mint(address to, uint256 value) internal {
        totalSupply += value;
        balanceOf[to] += value;
        emit Transfer(address(0), to, value);
    }

    function _transfer(address from, address to, uint256 value) internal {
        require(to != address(0), "zero");
        uint256 bal = balanceOf[from];
        require(bal >= value, "balance");
        balanceOf[from] = bal - value;
        balanceOf[to] += value;
        emit Transfer(from, to, value);
    }
}

contract AbiFactoryAndProbe {
    event TokenCreated(address indexed token, bytes32 indexed salt);
    event ProbeResult(address indexed token, bool ok, bytes data);

    function createToken(string calldata name, string calldata symbol, uint256 supply) external payable returns (FactoryMintedERC20 token) {
        token = new FactoryMintedERC20{value: msg.value}(name, symbol, msg.sender, supply);
        emit TokenCreated(address(token), bytes32(0));
    }

    function createToken2(bytes32 salt, string calldata name, string calldata symbol, uint256 supply) external returns (FactoryMintedERC20 token) {
        token = new FactoryMintedERC20{salt: salt}(name, symbol, msg.sender, supply);
        emit TokenCreated(address(token), salt);
    }

    function encodeSamples(address token, address to, uint256 amount) external pure returns (bytes memory, bytes memory, bytes memory, bytes32) {
        bytes memory a = abi.encode(to, amount);
        bytes memory b = abi.encodeWithSelector(IERC20View.transfer.selector, to, amount);
        bytes memory c = abi.encodeCall(IERC20View.approve, (to, amount));
        bytes32 packedHash = keccak256(abi.encodePacked(token, to, amount));
        return (a, b, c, packedHash);
    }

    function decodePair(bytes calldata data) external pure returns (address account, uint256 amount) {
        (account, amount) = abi.decode(data, (address, uint256));
    }

    function lowLevelTransfer(address token, address to, uint256 amount) external returns (bool) {
        (bool ok, bytes memory data) = token.call(
            abi.encodeWithSignature("transfer(address,uint256)", to, amount)
        );
        emit ProbeResult(token, ok, data);
        return ok && (data.length == 0 || abi.decode(data, (bool)));
    }

    function staticSupply(address token) external view returns (uint256 supply) {
        (bool ok, bytes memory data) = token.staticcall(
            abi.encodeWithSelector(IERC20View.totalSupply.selector)
        );
        require(ok, "staticcall");
        supply = abi.decode(data, (uint256));
    }

    function tryBalance(IERC20View token, address account) external view returns (uint256 balance, bool success) {
        try token.balanceOf(account) returns (uint256 value) {
            return (value, true);
        } catch Error(string memory) {
            return (0, false);
        } catch Panic(uint256) {
            return (0, false);
        } catch (bytes memory) {
            return (0, false);
        }
    }

    function destructure(address token, address account) external view returns (uint256 onlyBalance) {
        (uint256 supply, uint256 balance) = _supplyAndBalance(IERC20View(token), account);
        supply;
        (, onlyBalance) = _supplyAndBalance(IERC20View(token), account);
    }

    function _supplyAndBalance(IERC20View token, address account) internal view returns (uint256, uint256) {
        return (token.totalSupply(), token.balanceOf(account));
    }
}

