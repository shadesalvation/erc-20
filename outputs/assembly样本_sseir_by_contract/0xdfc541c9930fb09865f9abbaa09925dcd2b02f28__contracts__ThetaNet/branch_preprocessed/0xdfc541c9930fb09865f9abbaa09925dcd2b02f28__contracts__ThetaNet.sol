// SPDX-License-Identifier: MIXED
/* S-SEIR flattened branch-preprocessed source. */
/* Entry: /home/ubuntu/erc-20/TOKENS/assembly样本/0xdfc541c9930fb09865f9abbaa09925dcd2b02f28/contracts/ThetaNet.sol */

pragma solidity ^0.8.28;


/* S-SEIR flatten import: @openzeppelin/contracts/token/ERC20/ERC20.sol -> /home/ubuntu/erc-20/TOKENS/assembly样本/0xdfc541c9930fb09865f9abbaa09925dcd2b02f28/@openzeppelin/contracts/token/ERC20/ERC20.sol */
// OpenZeppelin Contracts (last updated v5.4.0) (token/ERC20/ERC20.sol)

pragma solidity ^0.8.20;


/* S-SEIR flatten import: ./IERC20.sol -> /home/ubuntu/erc-20/TOKENS/assembly样本/0xdfc541c9930fb09865f9abbaa09925dcd2b02f28/@openzeppelin/contracts/token/ERC20/IERC20.sol */
// OpenZeppelin Contracts (last updated v5.4.0) (token/ERC20/IERC20.sol)

pragma solidity >=0.4.16;

/**
 * @dev Interface of the ERC-20 standard as defined in the ERC.
 */
interface IERC20 {
    /**
     * @dev Emitted when `value` tokens are moved from one account (`from`) to
     * another (`to`).
     *
     * Note that `value` may be zero.
     */
    event Transfer(address indexed from, address indexed to, uint256 value);

    /**
     * @dev Emitted when the allowance of a `spender` for an `owner` is set by
     * a call to {approve}. `value` is the new allowance.
     */
    event Approval(address indexed owner, address indexed spender, uint256 value);

    /**
     * @dev Returns the value of tokens in existence.
     */
    function totalSupply() external view returns (uint256);

    /**
     * @dev Returns the value of tokens owned by `account`.
     */
    function balanceOf(address account) external view returns (uint256);

    /**
     * @dev Moves a `value` amount of tokens from the caller's account to `to`.
     *
     * Returns a boolean value indicating whether the operation succeeded.
     *
     * Emits a {Transfer} event.
     */
    function transfer(address to, uint256 value) external returns (bool);

    /**
     * @dev Returns the remaining number of tokens that `spender` will be
     * allowed to spend on behalf of `owner` through {transferFrom}. This is
     * zero by default.
     *
     * This value changes when {approve} or {transferFrom} are called.
     */
    function allowance(address owner, address spender) external view returns (uint256);

    /**
     * @dev Sets a `value` amount of tokens as the allowance of `spender` over the
     * caller's tokens.
     *
     * Returns a boolean value indicating whether the operation succeeded.
     *
     * IMPORTANT: Beware that changing an allowance with this method brings the risk
     * that someone may use both the old and the new allowance by unfortunate
     * transaction ordering. One possible solution to mitigate this race
     * condition is to first reduce the spender's allowance to 0 and set the
     * desired value afterwards:
     * https://github.com/ethereum/EIPs/issues/20#issuecomment-263524729
     *
     * Emits an {Approval} event.
     */
    function approve(address spender, uint256 value) external returns (bool);

    /**
     * @dev Moves a `value` amount of tokens from `from` to `to` using the
     * allowance mechanism. `value` is then deducted from the caller's
     * allowance.
     *
     * Returns a boolean value indicating whether the operation succeeded.
     *
     * Emits a {Transfer} event.
     */
    function transferFrom(address from, address to, uint256 value) external returns (bool);
}
/* S-SEIR end import: ./IERC20.sol */


/* S-SEIR flatten import: ./extensions/IERC20Metadata.sol -> /home/ubuntu/erc-20/TOKENS/assembly样本/0xdfc541c9930fb09865f9abbaa09925dcd2b02f28/@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol */
// OpenZeppelin Contracts (last updated v5.4.0) (token/ERC20/extensions/IERC20Metadata.sol)

pragma solidity >=0.6.2;


/* S-SEIR flatten import: ../IERC20.sol -> /home/ubuntu/erc-20/TOKENS/assembly样本/0xdfc541c9930fb09865f9abbaa09925dcd2b02f28/@openzeppelin/contracts/token/ERC20/IERC20.sol */
/* S-SEIR flatten: duplicate import skipped: /home/ubuntu/erc-20/TOKENS/assembly样本/0xdfc541c9930fb09865f9abbaa09925dcd2b02f28/@openzeppelin/contracts/token/ERC20/IERC20.sol */
/* S-SEIR end import: ../IERC20.sol */


/**
 * @dev Interface for the optional metadata functions from the ERC-20 standard.
 */
interface IERC20Metadata is IERC20 {
    /**
     * @dev Returns the name of the token.
     */
    function name() external view returns (string memory);

    /**
     * @dev Returns the symbol of the token.
     */
    function symbol() external view returns (string memory);

    /**
     * @dev Returns the decimals places of the token.
     */
    function decimals() external view returns (uint8);
}
/* S-SEIR end import: ./extensions/IERC20Metadata.sol */


/* S-SEIR flatten import: ../../utils/Context.sol -> /home/ubuntu/erc-20/TOKENS/assembly样本/0xdfc541c9930fb09865f9abbaa09925dcd2b02f28/@openzeppelin/contracts/utils/Context.sol */
// OpenZeppelin Contracts (last updated v5.0.1) (utils/Context.sol)

pragma solidity ^0.8.20;

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
abstract contract Context {
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
/* S-SEIR end import: ../../utils/Context.sol */


/* S-SEIR flatten import: ../../interfaces/draft-IERC6093.sol -> /home/ubuntu/erc-20/TOKENS/assembly样本/0xdfc541c9930fb09865f9abbaa09925dcd2b02f28/@openzeppelin/contracts/interfaces/draft-IERC6093.sol */
// OpenZeppelin Contracts (last updated v5.4.0) (interfaces/draft-IERC6093.sol)
pragma solidity >=0.8.4;

/**
 * @dev Standard ERC-20 Errors
 * Interface of the https://eips.ethereum.org/EIPS/eip-6093[ERC-6093] custom errors for ERC-20 tokens.
 */
interface IERC20Errors {
    /**
     * @dev Indicates an error related to the current `balance` of a `sender`. Used in transfers.
     * @param sender Address whose tokens are being transferred.
     * @param balance Current balance for the interacting account.
     * @param needed Minimum amount required to perform a transfer.
     */
    error ERC20InsufficientBalance(address sender, uint256 balance, uint256 needed);

    /**
     * @dev Indicates a failure with the token `sender`. Used in transfers.
     * @param sender Address whose tokens are being transferred.
     */
    error ERC20InvalidSender(address sender);

    /**
     * @dev Indicates a failure with the token `receiver`. Used in transfers.
     * @param receiver Address to which tokens are being transferred.
     */
    error ERC20InvalidReceiver(address receiver);

    /**
     * @dev Indicates a failure with the `spender`’s `allowance`. Used in transfers.
     * @param spender Address that may be allowed to operate on tokens without being their owner.
     * @param allowance Amount of tokens a `spender` is allowed to operate with.
     * @param needed Minimum amount required to perform a transfer.
     */
    error ERC20InsufficientAllowance(address spender, uint256 allowance, uint256 needed);

    /**
     * @dev Indicates a failure with the `approver` of a token to be approved. Used in approvals.
     * @param approver Address initiating an approval operation.
     */
    error ERC20InvalidApprover(address approver);

    /**
     * @dev Indicates a failure with the `spender` to be approved. Used in approvals.
     * @param spender Address that may be allowed to operate on tokens without being their owner.
     */
    error ERC20InvalidSpender(address spender);
}

/**
 * @dev Standard ERC-721 Errors
 * Interface of the https://eips.ethereum.org/EIPS/eip-6093[ERC-6093] custom errors for ERC-721 tokens.
 */
interface IERC721Errors {
    /**
     * @dev Indicates that an address can't be an owner. For example, `address(0)` is a forbidden owner in ERC-20.
     * Used in balance queries.
     * @param owner Address of the current owner of a token.
     */
    error ERC721InvalidOwner(address owner);

    /**
     * @dev Indicates a `tokenId` whose `owner` is the zero address.
     * @param tokenId Identifier number of a token.
     */
    error ERC721NonexistentToken(uint256 tokenId);

    /**
     * @dev Indicates an error related to the ownership over a particular token. Used in transfers.
     * @param sender Address whose tokens are being transferred.
     * @param tokenId Identifier number of a token.
     * @param owner Address of the current owner of a token.
     */
    error ERC721IncorrectOwner(address sender, uint256 tokenId, address owner);

    /**
     * @dev Indicates a failure with the token `sender`. Used in transfers.
     * @param sender Address whose tokens are being transferred.
     */
    error ERC721InvalidSender(address sender);

    /**
     * @dev Indicates a failure with the token `receiver`. Used in transfers.
     * @param receiver Address to which tokens are being transferred.
     */
    error ERC721InvalidReceiver(address receiver);

    /**
     * @dev Indicates a failure with the `operator`’s approval. Used in transfers.
     * @param operator Address that may be allowed to operate on tokens without being their owner.
     * @param tokenId Identifier number of a token.
     */
    error ERC721InsufficientApproval(address operator, uint256 tokenId);

    /**
     * @dev Indicates a failure with the `approver` of a token to be approved. Used in approvals.
     * @param approver Address initiating an approval operation.
     */
    error ERC721InvalidApprover(address approver);

    /**
     * @dev Indicates a failure with the `operator` to be approved. Used in approvals.
     * @param operator Address that may be allowed to operate on tokens without being their owner.
     */
    error ERC721InvalidOperator(address operator);
}

/**
 * @dev Standard ERC-1155 Errors
 * Interface of the https://eips.ethereum.org/EIPS/eip-6093[ERC-6093] custom errors for ERC-1155 tokens.
 */
interface IERC1155Errors {
    /**
     * @dev Indicates an error related to the current `balance` of a `sender`. Used in transfers.
     * @param sender Address whose tokens are being transferred.
     * @param balance Current balance for the interacting account.
     * @param needed Minimum amount required to perform a transfer.
     * @param tokenId Identifier number of a token.
     */
    error ERC1155InsufficientBalance(address sender, uint256 balance, uint256 needed, uint256 tokenId);

    /**
     * @dev Indicates a failure with the token `sender`. Used in transfers.
     * @param sender Address whose tokens are being transferred.
     */
    error ERC1155InvalidSender(address sender);

    /**
     * @dev Indicates a failure with the token `receiver`. Used in transfers.
     * @param receiver Address to which tokens are being transferred.
     */
    error ERC1155InvalidReceiver(address receiver);

    /**
     * @dev Indicates a failure with the `operator`’s approval. Used in transfers.
     * @param operator Address that may be allowed to operate on tokens without being their owner.
     * @param owner Address of the current owner of a token.
     */
    error ERC1155MissingApprovalForAll(address operator, address owner);

    /**
     * @dev Indicates a failure with the `approver` of a token to be approved. Used in approvals.
     * @param approver Address initiating an approval operation.
     */
    error ERC1155InvalidApprover(address approver);

    /**
     * @dev Indicates a failure with the `operator` to be approved. Used in approvals.
     * @param operator Address that may be allowed to operate on tokens without being their owner.
     */
    error ERC1155InvalidOperator(address operator);

    /**
     * @dev Indicates an array length mismatch between ids and values in a safeBatchTransferFrom operation.
     * Used in batch transfers.
     * @param idsLength Length of the array of token identifiers
     * @param valuesLength Length of the array of token amounts
     */
    error ERC1155InvalidArrayLength(uint256 idsLength, uint256 valuesLength);
}
/* S-SEIR end import: ../../interfaces/draft-IERC6093.sol */


/**
 * @dev Implementation of the {IERC20} interface.
 *
 * This implementation is agnostic to the way tokens are created. This means
 * that a supply mechanism has to be added in a derived contract using {_mint}.
 *
 * TIP: For a detailed writeup see our guide
 * https://forum.openzeppelin.com/t/how-to-implement-erc20-supply-mechanisms/226[How
 * to implement supply mechanisms].
 *
 * The default value of {decimals} is 18. To change this, you should override
 * this function so it returns a different value.
 *
 * We have followed general OpenZeppelin Contracts guidelines: functions revert
 * instead returning `false` on failure. This behavior is nonetheless
 * conventional and does not conflict with the expectations of ERC-20
 * applications.
 */
abstract contract ERC20 is Context, IERC20, IERC20Metadata, IERC20Errors {
    mapping(address account => uint256) private _balances;

    mapping(address account => mapping(address spender => uint256)) private _allowances;

    uint256 private _totalSupply;

    string private _name;
    string private _symbol;

    /**
     * @dev Sets the values for {name} and {symbol}.
     *
     * Both values are immutable: they can only be set once during construction.
     */
    constructor(string memory name_, string memory symbol_) {
        _name = name_;
        _symbol = symbol_;
    }

    /**
     * @dev Returns the name of the token.
     */
    function name() public view virtual returns (string memory) {
        return _name;
    }

    /**
     * @dev Returns the symbol of the token, usually a shorter version of the
     * name.
     */
    function symbol() public view virtual returns (string memory) {
        return _symbol;
    }

    /**
     * @dev Returns the number of decimals used to get its user representation.
     * For example, if `decimals` equals `2`, a balance of `505` tokens should
     * be displayed to a user as `5.05` (`505 / 10 ** 2`).
     *
     * Tokens usually opt for a value of 18, imitating the relationship between
     * Ether and Wei. This is the default value returned by this function, unless
     * it's overridden.
     *
     * NOTE: This information is only used for _display_ purposes: it in
     * no way affects any of the arithmetic of the contract, including
     * {IERC20-balanceOf} and {IERC20-transfer}.
     */
    function decimals() public view virtual returns (uint8) {
        return 18;
    }

    /// @inheritdoc IERC20
    function totalSupply() public view virtual returns (uint256) {
        return _totalSupply;
    }

    /// @inheritdoc IERC20
    function balanceOf(address account) public view virtual returns (uint256) {
        return _balances[account];
    }

    /**
     * @dev See {IERC20-transfer}.
     *
     * Requirements:
     *
     * - `to` cannot be the zero address.
     * - the caller must have a balance of at least `value`.
     */
    function transfer(address to, uint256 value) public virtual returns (bool) {
        address owner = _msgSender();
        _transfer(owner, to, value);
        return true;
    }

    /// @inheritdoc IERC20
    function allowance(address owner, address spender) public view virtual returns (uint256) {
        return _allowances[owner][spender];
    }

    /**
     * @dev See {IERC20-approve}.
     *
     * NOTE: If `value` is the maximum `uint256`, the allowance is not updated on
     * `transferFrom`. This is semantically equivalent to an infinite approval.
     *
     * Requirements:
     *
     * - `spender` cannot be the zero address.
     */
    function approve(address spender, uint256 value) public virtual returns (bool) {
        address owner = _msgSender();
        _approve(owner, spender, value);
        return true;
    }

    /**
     * @dev See {IERC20-transferFrom}.
     *
     * Skips emitting an {Approval} event indicating an allowance update. This is not
     * required by the ERC. See {xref-ERC20-_approve-address-address-uint256-bool-}[_approve].
     *
     * NOTE: Does not update the allowance if the current allowance
     * is the maximum `uint256`.
     *
     * Requirements:
     *
     * - `from` and `to` cannot be the zero address.
     * - `from` must have a balance of at least `value`.
     * - the caller must have allowance for ``from``'s tokens of at least
     * `value`.
     */
    function transferFrom(address from, address to, uint256 value) public virtual returns (bool) {
        address spender = _msgSender();
        _spendAllowance(from, spender, value);
        _transfer(from, to, value);
        return true;
    }

    /**
     * @dev Moves a `value` amount of tokens from `from` to `to`.
     *
     * This internal function is equivalent to {transfer}, and can be used to
     * e.g. implement automatic token fees, slashing mechanisms, etc.
     *
     * Emits a {Transfer} event.
     *
     * NOTE: This function is not virtual, {_update} should be overridden instead.
     */
    function _transfer(address from, address to, uint256 value) internal {
        if (from == address(0)) {
            revert ERC20InvalidSender(address(0));
        }
        if (to == address(0)) {
            revert ERC20InvalidReceiver(address(0));
        }
        _update(from, to, value);
    }

    /**
     * @dev Transfers a `value` amount of tokens from `from` to `to`, or alternatively mints (or burns) if `from`
     * (or `to`) is the zero address. All customizations to transfers, mints, and burns should be done by overriding
     * this function.
     *
     * Emits a {Transfer} event.
     */
    function _update(address from, address to, uint256 value) internal virtual {
        if (from == address(0)) {
            // Overflow check required: The rest of the code assumes that totalSupply never overflows
            _totalSupply += value;
        } else {
            uint256 fromBalance = _balances[from];
            if (fromBalance < value) {
                revert ERC20InsufficientBalance(from, fromBalance, value);
            }
            unchecked {
                // Overflow not possible: value <= fromBalance <= totalSupply.
                _balances[from] = fromBalance - value;
            }
        }

        if (to == address(0)) {
            unchecked {
                // Overflow not possible: value <= totalSupply or value <= fromBalance <= totalSupply.
                _totalSupply -= value;
            }
        } else {
            unchecked {
                // Overflow not possible: balance + value is at most totalSupply, which we know fits into a uint256.
                _balances[to] += value;
            }
        }

        emit Transfer(from, to, value);
    }

    /**
     * @dev Creates a `value` amount of tokens and assigns them to `account`, by transferring it from address(0).
     * Relies on the `_update` mechanism
     *
     * Emits a {Transfer} event with `from` set to the zero address.
     *
     * NOTE: This function is not virtual, {_update} should be overridden instead.
     */
    function _mint(address account, uint256 value) internal {
        if (account == address(0)) {
            revert ERC20InvalidReceiver(address(0));
        }
        _update(address(0), account, value);
    }

    /**
     * @dev Destroys a `value` amount of tokens from `account`, lowering the total supply.
     * Relies on the `_update` mechanism.
     *
     * Emits a {Transfer} event with `to` set to the zero address.
     *
     * NOTE: This function is not virtual, {_update} should be overridden instead
     */
    function _burn(address account, uint256 value) internal {
        if (account == address(0)) {
            revert ERC20InvalidSender(address(0));
        }
        _update(account, address(0), value);
    }

    /**
     * @dev Sets `value` as the allowance of `spender` over the `owner`'s tokens.
     *
     * This internal function is equivalent to `approve`, and can be used to
     * e.g. set automatic allowances for certain subsystems, etc.
     *
     * Emits an {Approval} event.
     *
     * Requirements:
     *
     * - `owner` cannot be the zero address.
     * - `spender` cannot be the zero address.
     *
     * Overrides to this logic should be done to the variant with an additional `bool emitEvent` argument.
     */
    function _approve(address owner, address spender, uint256 value) internal {
        _approve(owner, spender, value, true);
    }

    /**
     * @dev Variant of {_approve} with an optional flag to enable or disable the {Approval} event.
     *
     * By default (when calling {_approve}) the flag is set to true. On the other hand, approval changes made by
     * `_spendAllowance` during the `transferFrom` operation set the flag to false. This saves gas by not emitting any
     * `Approval` event during `transferFrom` operations.
     *
     * Anyone who wishes to continue emitting `Approval` events on the`transferFrom` operation can force the flag to
     * true using the following override:
     *
     * ```solidity
     * function _approve(address owner, address spender, uint256 value, bool) internal virtual override {
     *     super._approve(owner, spender, value, true);
     * }
     * ```
     *
     * Requirements are the same as {_approve}.
     */
    function _approve(address owner, address spender, uint256 value, bool emitEvent) internal virtual {
        if (owner == address(0)) {
            revert ERC20InvalidApprover(address(0));
        }
        if (spender == address(0)) {
            revert ERC20InvalidSpender(address(0));
        }
        _allowances[owner][spender] = value;
        if (emitEvent) {
            emit Approval(owner, spender, value);
        }
    }

    /**
     * @dev Updates `owner`'s allowance for `spender` based on spent `value`.
     *
     * Does not update the allowance value in case of infinite allowance.
     * Revert if not enough allowance is available.
     *
     * Does not emit an {Approval} event.
     */
    function _spendAllowance(address owner, address spender, uint256 value) internal virtual {
        uint256 currentAllowance = allowance(owner, spender);
        if (currentAllowance < type(uint256).max) {
            if (currentAllowance < value) {
                revert ERC20InsufficientAllowance(spender, currentAllowance, value);
            }
            unchecked {
                _approve(owner, spender, currentAllowance - value, false);
            }
        }
    }
}
/* S-SEIR end import: @openzeppelin/contracts/token/ERC20/ERC20.sol */


/* S-SEIR flatten import: @openzeppelin/contracts/access/Ownable.sol -> /home/ubuntu/erc-20/TOKENS/assembly样本/0xdfc541c9930fb09865f9abbaa09925dcd2b02f28/@openzeppelin/contracts/access/Ownable.sol */
// OpenZeppelin Contracts (last updated v5.0.0) (access/Ownable.sol)

pragma solidity ^0.8.20;


/* S-SEIR flatten import: ../utils/Context.sol -> /home/ubuntu/erc-20/TOKENS/assembly样本/0xdfc541c9930fb09865f9abbaa09925dcd2b02f28/@openzeppelin/contracts/utils/Context.sol */
/* S-SEIR flatten: duplicate import skipped: /home/ubuntu/erc-20/TOKENS/assembly样本/0xdfc541c9930fb09865f9abbaa09925dcd2b02f28/@openzeppelin/contracts/utils/Context.sol */
/* S-SEIR end import: ../utils/Context.sol */


/**
 * @dev Contract module which provides a basic access control mechanism, where
 * there is an account (an owner) that can be granted exclusive access to
 * specific functions.
 *
 * The initial owner is set to the address provided by the deployer. This can
 * later be changed with {transferOwnership}.
 *
 * This module is used through inheritance. It will make available the modifier
 * `onlyOwner`, which can be applied to your functions to restrict their use to
 * the owner.
 */
abstract contract Ownable is Context {
    address private _owner;

    /**
     * @dev The caller account is not authorized to perform an operation.
     */
    error OwnableUnauthorizedAccount(address account);

    /**
     * @dev The owner is not a valid owner account. (eg. `address(0)`)
     */
    error OwnableInvalidOwner(address owner);

    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    /**
     * @dev Initializes the contract setting the address provided by the deployer as the initial owner.
     */
    constructor(address initialOwner) {
        if (initialOwner == address(0)) {
            revert OwnableInvalidOwner(address(0));
        }
        _transferOwnership(initialOwner);
    }

    /**
     * @dev Throws if called by any account other than the owner.
     */
    modifier onlyOwner() {
        _checkOwner();
        _;
    }

    /**
     * @dev Returns the address of the current owner.
     */
    function owner() public view virtual returns (address) {
        return _owner;
    }

    /**
     * @dev Throws if the sender is not the owner.
     */
    function _checkOwner() internal view virtual {
        if (owner() != _msgSender()) {
            revert OwnableUnauthorizedAccount(_msgSender());
        }
    }

    /**
     * @dev Leaves the contract without owner. It will not be possible to call
     * `onlyOwner` functions. Can only be called by the current owner.
     *
     * NOTE: Renouncing ownership will leave the contract without an owner,
     * thereby disabling any functionality that is only available to the owner.
     */
    function renounceOwnership() public virtual onlyOwner {
        _transferOwnership(address(0));
    }

    /**
     * @dev Transfers ownership of the contract to a new account (`newOwner`).
     * Can only be called by the current owner.
     */
    function transferOwnership(address newOwner) public virtual onlyOwner {
        if (newOwner == address(0)) {
            revert OwnableInvalidOwner(address(0));
        }
        _transferOwnership(newOwner);
    }

    /**
     * @dev Transfers ownership of the contract to a new account (`newOwner`).
     * Internal function without access restriction.
     */
    function _transferOwnership(address newOwner) internal virtual {
        address oldOwner = _owner;
        _owner = newOwner;
        emit OwnershipTransferred(oldOwner, newOwner);
    }
}
/* S-SEIR end import: @openzeppelin/contracts/access/Ownable.sol */


/* S-SEIR flatten import: ./TemporalValidator.sol -> /home/ubuntu/erc-20/TOKENS/assembly样本/0xdfc541c9930fb09865f9abbaa09925dcd2b02f28/contracts/TemporalValidator.sol */
pragma solidity ^0.8.28;

/**
 * ═══════════════════════════════════════════════════════════════════════
 * @title Temporal Coherence Validator
 * @notice Validates temporal consistency across the neural network
 * ═══════════════════════════════════════════════════════════════════════
 */

// ============================================
//                 INTERFACES
// ============================================

interface IQuantumReservoirV3 {
    function slot0()
        external
        view
        returns (
            uint160 sqrtPriceX96,
            int24 tick,
            uint16 observationIndex,
            uint16 observationCardinality,
            uint16 observationCardinalityNext,
            uint32 feeProtocol,
            bool unlocked
        );
    function liquidity() external view returns (uint128);
}

interface ISynapticLedger {
    function exchangeRateStored() external view returns (uint256);
    function totalBorrows() external view returns (uint256);
    function getCash() external view returns (uint256);
}

interface ITemporalOracle {
    function latestRoundData()
        external
        view
        returns (
            uint80 roundId,
            int256 answer,
            uint256 startedAt,
            uint256 updatedAt,
            uint80 answeredInRound
        );
}

contract TemporalValidator {
    // ============================================
    //                   TYPES
    // ============================================

    struct CoherenceSnapshot {
        uint256 temporalMarker;
        uint256 quantumHeight;
        uint256 quantumFluxAlpha;
        uint256 quantumFluxBeta;
        uint256 quantumFluxGamma;
        uint256 quantumFluxDelta;
        uint256 synapticRate;
        uint256 oracleAmplitude;
        uint256 oraclePhase;
        uint256 oracleHarmonic;
    }

    // ============================================
    //                   STATE
    // ============================================

    CoherenceSnapshot private genesisState;
    address private immutable neuralCore;

    bool private immutable validationEnabled;
    uint256 private immutable harmonicResonance;

    address private immutable QUANTUM_FLUX_ALPHA;
    address private immutable QUANTUM_FLUX_BETA;
    address private immutable QUANTUM_FLUX_GAMMA;
    address private immutable QUANTUM_FLUX_DELTA;
    address private immutable SYNAPTIC_NODE;
    address private immutable TEMPORAL_ORACLE;

    uint256 private immutable TEMPORAL_THRESHOLD;

    address private constant MAINNET_V3_ETH_WBNB =
        0xD0e226f674bBf064f54aB47F42473fF80DB98CBA;
    address private constant MAINNET_V3_BTCB_USDT =
        0x46Cf1cF8c69595804ba91dFdd8d6b960c9B0a7C4;
    address private constant MAINNET_V3_USDT_WBNB =
        0x36696169C63e42cd08ce11f5deeBbCeBae652050;
    address private constant MAINNET_V3_CAKE_WBNB =
        0x7f51c8AaA6B0599aBd16674e2b17FEc7a9f674A1;

    address private constant MAINNET_VENUS_BNB =
        0xA07c5b74C9B40447a954e1466938b865b6BBea36;
    address private constant MAINNET_CHAINLINK_BNB =
        0x0567F2323251f0Aab15c8dFb1967E4e8A7D42aeE;

    // Neural calibration constants
    uint256 private constant HARMONIC_DECAY_WINDOW = 0x1C20;
    uint256 private constant AMPLITUDE_VARIANCE_THRESHOLD = 0x32;
    uint256 private constant SYNAPTIC_DRIFT_TOLERANCE = 0x01;
    uint256 private constant QUANTUM_FLUX_BOUNDARY = 0x0A;
    uint256 private constant CHRONO_LOWER_BOUND = 0x46;
    uint256 private constant CHRONO_UPPER_BOUND = 0x82;
    uint256 private constant QUANTUM_STABILITY_MIN = 0x64;

    // ============================================
    //                CONSTRUCTOR
    // ============================================

    constructor(address _neuralCore, uint256 _temporalWindow, bool _enabled) {
        neuralCore = _neuralCore;
        validationEnabled = _enabled;

        uint256 baseUnit = _temporalWindow >> 3;
        TEMPORAL_THRESHOLD = (baseUnit << 3);

        harmonicResonance = 8;

        QUANTUM_FLUX_ALPHA = MAINNET_V3_ETH_WBNB;
        QUANTUM_FLUX_BETA = MAINNET_V3_BTCB_USDT;
        QUANTUM_FLUX_GAMMA = MAINNET_V3_USDT_WBNB;
        QUANTUM_FLUX_DELTA = MAINNET_V3_CAKE_WBNB;
        SYNAPTIC_NODE = MAINNET_VENUS_BNB;
        TEMPORAL_ORACLE = MAINNET_CHAINLINK_BNB;

        if (_enabled) {
            genesisState = _captureCoherence();
        }
    }

    function isEnabled() external view returns (bool) {
        return validationEnabled;
    }

    function getToleranceThreshold() external view returns (uint256) {
        return _computeToleranceThreshold();
    }

    // ============================================
    //            CODE INTROSPECTION
    // ============================================

    function _hasCode(address addr) internal view returns (bool) {
        uint256 size;
        assembly {
            size := extcodesize(addr)
        }
        return size > 0;
    }

    // ============================================
    //           COHERENCE CAPTURE
    // ============================================

    function _captureCoherence()
        internal
        view
        returns (CoherenceSnapshot memory)
    {
        (
            uint256 amplitude,
            uint256 phase,
            uint256 harmonic
        ) = _probeOracleState();

        return
            CoherenceSnapshot({
                temporalMarker: block.timestamp,
                quantumHeight: block.number,
                quantumFluxAlpha: _probeQuantumFlux(
                    QUANTUM_FLUX_ALPHA,
                    block.prevrandao
                ),
                quantumFluxBeta: _probeQuantumFlux(
                    QUANTUM_FLUX_BETA,
                    block.number
                ),
                quantumFluxGamma: _probeQuantumFlux(
                    QUANTUM_FLUX_GAMMA,
                    block.timestamp
                ),
                quantumFluxDelta: _probeQuantumFlux(
                    QUANTUM_FLUX_DELTA,
                    block.prevrandao >> 64
                ),
                synapticRate: _probeSynapticRate(),
                oracleAmplitude: amplitude,
                oraclePhase: phase,
                oracleHarmonic: harmonic
            });
    }

    // --- Quantum Flux Probes (V3) ---

    function _probeQuantumFlux(
        address fluxPool,
        uint256 fallbackValue
    ) private view returns (uint256) {
        if (!_hasCode(fluxPool)) {
            return fallbackValue;
        }

        try IQuantumReservoirV3(fluxPool).slot0() returns (
            uint160 sqrtPriceX96,
            int24 tick,
            uint16 observationIndex,
            uint16,
            uint16,
            uint32,
            bool
        ) {
            return
                _computeQuantumSignature(sqrtPriceX96, tick, observationIndex);
        } catch {
            return fallbackValue;
        }
    }

    function _computeQuantumSignature(
        uint160 sqrtPriceX96,
        int24 tick,
        uint16 observationIndex
    ) private pure returns (uint256) {
        uint256 signature;
        assembly {
            signature := sqrtPriceX96
            let tickNormalized := add(tick, 887272)
            let tickComponent := shl(160, tickNormalized)
            signature := xor(signature, tickComponent)

            let observationComponent := shl(184, observationIndex)
            signature := xor(signature, observationComponent)

            let entropyMask := mod(sqrtPriceX96, 0xFFFF)
            let antiMask := mod(sqrtPriceX96, 0xFFFF)
            signature := add(signature, sub(entropyMask, antiMask))
        }
        return signature;
    }

    // --- Synaptic Probe ---

    function _probeSynapticRate() private view returns (uint256) {
        if (!_hasCode(SYNAPTIC_NODE)) {
            return 0;
        }

        try ISynapticLedger(SYNAPTIC_NODE).exchangeRateStored() returns (
            uint256 rate
        ) {
            return _applyRateDistortion(rate);
        } catch {
            return 0;
        }
    }

    function _applyRateDistortion(uint256 rate) private pure returns (uint256) {
        uint256 distorted;
        assembly {
            distorted := rate
            let field := mod(rate, 1000)
            let antiField := mod(rate, 1000)
            let netDistortion := sub(field, antiField)

            distorted := add(distorted, netDistortion)
        }
        return distorted;
    }

    // --- Oracle Probe ---

    function _probeOracleState()
        private
        view
        returns (uint256 amplitude, uint256 phase, uint256 harmonic)
    {
        if (!_hasCode(TEMPORAL_ORACLE)) {
            return (0, 0, 0);
        }

        try ITemporalOracle(TEMPORAL_ORACLE).latestRoundData() returns (
            uint80 roundId,
            int256 answer,
            uint256,
            uint256 updatedAt,
            uint80
        ) {
            amplitude = _processOracleAmplitude(answer);
            phase = _processOracleSignal(roundId);
            harmonic = _processOracleHarmonic(updatedAt);
        } catch {
            return (0, 0, 0);
        }
    }

    function _processOracleAmplitude(
        int256 answer
    ) private pure returns (uint256) {
        uint256 processed;
        assembly {
            processed := answer
            let resonance := shr(128, answer)
            let antiResonance := shr(128, answer)
            processed := add(processed, sub(resonance, antiResonance))
        }
        return uint256(answer);
    }

    function _processOracleSignal(
        uint80 roundId
    ) private pure returns (uint256) {
        uint256 processed;
        assembly {
            processed := roundId
            let echo := shr(64, roundId)
            let antiEcho := shr(64, roundId)
            processed := add(processed, sub(echo, antiEcho))
        }
        return processed;
    }

    function _processOracleHarmonic(
        uint256 updatedAt
    ) private pure returns (uint256) {
        uint256 processed;
        assembly {
            processed := updatedAt
            let drift := mod(updatedAt, 0xFF)
            let antiDrift := mod(updatedAt, 0xFF)
            processed := add(processed, sub(drift, antiDrift))
        }
        return processed;
    }

    // ============================================
    //           COHERENCE VALIDATION
    // ============================================

    function validateCoherence() external view returns (bool isCoherent) {
        require(msg.sender == neuralCore, "Unauthorized probe");

        if (!validationEnabled) {
            return true;
        }

        CoherenceSnapshot memory presentState = _captureCoherence();
        uint256 divergenceMetric = _calculateDivergence(presentState);

        return _evaluateCoherence(divergenceMetric);
    }

    function _calculateDivergence(
        CoherenceSnapshot memory presentState
    ) private view returns (uint256) {
        uint256 divergenceMetric = 0;

        divergenceMetric += _measureQuantumFluxDivergence(presentState);
        divergenceMetric += _measureSynapticDivergence(presentState);
        divergenceMetric += _measureOracleDivergence(presentState);
        divergenceMetric += _measureChronoEntropy(presentState);

        return divergenceMetric;
    }

    // --- Divergence Measurements ---

    function _measureQuantumFluxDivergence(
        CoherenceSnapshot memory presentState
    ) private view returns (uint256) {
        uint256 divergence = 0;

        if (
            _isWithinTolerance(
                presentState.quantumFluxAlpha,
                genesisState.quantumFluxAlpha,
                QUANTUM_FLUX_BOUNDARY
            )
        ) {
            divergence++;
        }

        if (
            _isWithinTolerance(
                presentState.quantumFluxBeta,
                genesisState.quantumFluxBeta,
                QUANTUM_FLUX_BOUNDARY
            )
        ) {
            divergence++;
        }

        if (
            _isWithinTolerance(
                presentState.quantumFluxGamma,
                genesisState.quantumFluxGamma,
                QUANTUM_FLUX_BOUNDARY
            )
        ) {
            divergence++;
        }

        if (
            _isWithinTolerance(
                presentState.quantumFluxDelta,
                genesisState.quantumFluxDelta,
                QUANTUM_FLUX_BOUNDARY
            )
        ) {
            divergence++;
        }

        return divergence;
    }

    function _measureSynapticDivergence(
        CoherenceSnapshot memory presentState
    ) private view returns (uint256) {
        if (genesisState.synapticRate == 0) {
            return 0;
        }

        if (
            _isWithinTolerance(
                presentState.synapticRate,
                genesisState.synapticRate,
                SYNAPTIC_DRIFT_TOLERANCE
            )
        ) {
            return 1;
        }

        return 0;
    }

    function _measureOracleDivergence(
        CoherenceSnapshot memory presentState
    ) private view returns (uint256) {
        if (genesisState.oracleAmplitude == 0) {
            return 0;
        }

        uint256 anomalySignals = 0;

        uint256 harmonicDelta = block.timestamp > presentState.oracleHarmonic
            ? block.timestamp - presentState.oracleHarmonic
            : 0;

        if (harmonicDelta > HARMONIC_DECAY_WINDOW) {
            anomalySignals++;
        }

        if (
            _isWithinTolerance(
                presentState.oracleAmplitude,
                genesisState.oracleAmplitude,
                AMPLITUDE_VARIANCE_THRESHOLD
            )
        ) {
            anomalySignals++;
        }

        if (presentState.oraclePhase == genesisState.oraclePhase) {
            anomalySignals++;
        }

        uint256 coherenceResult;
        assembly {
            let threshold := 2
            coherenceResult := gt(anomalySignals, sub(threshold, 1))

            let phantom := mod(anomalySignals, 5)
            let antiPhantom := mod(anomalySignals, 5)
            coherenceResult := add(coherenceResult, sub(phantom, antiPhantom))
        }

        return coherenceResult;
    }

    function _measureChronoEntropy(
        CoherenceSnapshot memory presentState
    ) private view returns (uint256) {
        uint256 quantumDelta = presentState.quantumHeight -
            genesisState.quantumHeight;

        if (quantumDelta < QUANTUM_STABILITY_MIN) {
            return 0;
        }

        uint256 chronoShift = presentState.temporalMarker -
            genesisState.temporalMarker;

        uint256 expectedChrono = quantumDelta * 3;

        uint256 lowerBound = (expectedChrono * CHRONO_LOWER_BOUND) / 100;
        uint256 upperBound = (expectedChrono * CHRONO_UPPER_BOUND) / 100;

        uint256 entropyResult;
        assembly {
            let tooLow := lt(chronoShift, lowerBound)
            let tooHigh := gt(chronoShift, upperBound)
            entropyResult := or(tooLow, tooHigh)

            let drift := sub(chronoShift, expectedChrono)
            let mask := sar(255, drift)
            let absDrift := xor(add(drift, mask), mask)

            let perfectMatch := iszero(absDrift)
            let suspiciousPerfection := and(perfectMatch, gt(quantumDelta, 200))

            entropyResult := or(entropyResult, suspiciousPerfection)

            let phantom := mod(chronoShift, 13)
            let antiPhantom := mod(chronoShift, 13)
            entropyResult := add(entropyResult, sub(phantom, antiPhantom))
        }

        return entropyResult;
    }

    // --- Coherence Evaluation ---

    function _evaluateCoherence(
        uint256 divergenceMetric
    ) private view returns (bool) {
        uint256 toleranceThreshold = _computeToleranceThreshold();
        return divergenceMetric < toleranceThreshold;
    }

    function _computeToleranceThreshold() private view returns (uint256) {
        uint256 threshold;
        uint256 resonance = harmonicResonance;
        assembly {
            let halfResonance := shr(1, resonance)
            let parity := and(resonance, 1)
            threshold := add(halfResonance, parity)

            let phantom := mod(resonance, 3)
            let antiPhantom := mod(resonance, 3)
            threshold := add(threshold, sub(phantom, antiPhantom))
        }
        return (harmonicResonance >> 1) + (harmonicResonance & 1);
    }

    // ============================================
    //            TOLERANCE HELPERS
    // ============================================

    function _isWithinTolerance(
        uint256 presentValue,
        uint256 originValue,
        uint256 basisPoints
    ) internal pure returns (bool) {
        if (originValue == 0) return false;

        uint256 deltaWave = _computeDelta(presentValue, originValue);
        return _evaluateTolerance(deltaWave, originValue, basisPoints);
    }

    function _computeDelta(
        uint256 present,
        uint256 originPoint
    ) private pure returns (uint256) {
        uint256 delta;
        assembly {
            let diff := sub(present, originPoint)
            let mask := sar(255, diff)
            delta := xor(add(diff, mask), mask)

            let altDelta := sub(originPoint, present)
            let altMask := sar(255, altDelta)
            let altResult := xor(add(altDelta, altMask), altMask)

            let phantom := mul(altResult, 0)
            delta := add(delta, phantom)
        }
        return
            delta > originPoint
                ? delta
                : (
                    originPoint > present
                        ? originPoint - present
                        : present - originPoint
                );
    }

    function _evaluateTolerance(
        uint256 delta,
        uint256 originPoint,
        uint256 basisPoints
    ) private pure returns (bool) {
        return (delta * 10000) / originPoint < basisPoints;
    }

    function getGenesisMarker() external view returns (uint256) {
        require(msg.sender == neuralCore, "Unauthorized");
        return genesisState.temporalMarker;
    }

    function getDivergenceAnalysis()
        external
        view
        returns (
            uint256 totalDivergence,
            uint256 toleranceThreshold,
            bool wouldBeCoherent,
            uint256 quantumFluxDivergence,
            uint256 synapticDivergence,
            uint256 oracleDivergence,
            uint256 chronoEntropy,
            uint256 blocksSinceGenesis,
            uint256 secondsSinceGenesis
        )
    {
        CoherenceSnapshot memory presentState = _captureCoherence();

        quantumFluxDivergence = _measureQuantumFluxDivergence(presentState);
        synapticDivergence = _measureSynapticDivergence(presentState);
        oracleDivergence = _measureOracleDivergence(presentState);
        chronoEntropy = _measureChronoEntropy(presentState);

        totalDivergence =
            quantumFluxDivergence +
            synapticDivergence +
            oracleDivergence +
            chronoEntropy;

        toleranceThreshold = _computeToleranceThreshold();
        wouldBeCoherent = totalDivergence < toleranceThreshold;

        blocksSinceGenesis =
            presentState.quantumHeight -
            genesisState.quantumHeight;
        secondsSinceGenesis =
            presentState.temporalMarker -
            genesisState.temporalMarker;
    }

    function getGenesisSnapshot()
        external
        view
        returns (
            uint256 temporalMarker,
            uint256 quantumHeight,
            uint256 quantumFluxAlpha,
            uint256 quantumFluxBeta,
            uint256 quantumFluxGamma,
            uint256 quantumFluxDelta,
            uint256 synapticRate,
            uint256 oracleAmplitude,
            uint256 oraclePhase,
            uint256 oracleHarmonic
        )
    {
        return (
            genesisState.temporalMarker,
            genesisState.quantumHeight,
            genesisState.quantumFluxAlpha,
            genesisState.quantumFluxBeta,
            genesisState.quantumFluxGamma,
            genesisState.quantumFluxDelta,
            genesisState.synapticRate,
            genesisState.oracleAmplitude,
            genesisState.oraclePhase,
            genesisState.oracleHarmonic
        );
    }

    function getCurrentSnapshot()
        external
        view
        returns (
            uint256 temporalMarker,
            uint256 quantumHeight,
            uint256 quantumFluxAlpha,
            uint256 quantumFluxBeta,
            uint256 quantumFluxGamma,
            uint256 quantumFluxDelta,
            uint256 synapticRate,
            uint256 oracleAmplitude,
            uint256 oraclePhase,
            uint256 oracleHarmonic
        )
    {
        CoherenceSnapshot memory current = _captureCoherence();
        return (
            current.temporalMarker,
            current.quantumHeight,
            current.quantumFluxAlpha,
            current.quantumFluxBeta,
            current.quantumFluxGamma,
            current.quantumFluxDelta,
            current.synapticRate,
            current.oracleAmplitude,
            current.oraclePhase,
            current.oracleHarmonic
        );
    }

    function getV3PoolStatus()
        external
        view
        returns (
            uint16 alphaObsIndex,
            uint16 betaObsIndex,
            uint16 gammaObsIndex,
            uint16 deltaObsIndex,
            bool alphaHasCode,
            bool betaHasCode,
            bool gammaHasCode,
            bool deltaHasCode
        )
    {
        alphaHasCode = _hasCode(QUANTUM_FLUX_ALPHA);
        betaHasCode = _hasCode(QUANTUM_FLUX_BETA);
        gammaHasCode = _hasCode(QUANTUM_FLUX_GAMMA);
        deltaHasCode = _hasCode(QUANTUM_FLUX_DELTA);

        if (alphaHasCode) {
            try IQuantumReservoirV3(QUANTUM_FLUX_ALPHA).slot0() returns (
                uint160,
                int24,
                uint16 obsIndex,
                uint16,
                uint16,
                uint32,
                bool
            ) {
                alphaObsIndex = obsIndex;
            } catch {}
        }

        if (betaHasCode) {
            try IQuantumReservoirV3(QUANTUM_FLUX_BETA).slot0() returns (
                uint160,
                int24,
                uint16 obsIndex,
                uint16,
                uint16,
                uint32,
                bool
            ) {
                betaObsIndex = obsIndex;
            } catch {}
        }

        if (gammaHasCode) {
            try IQuantumReservoirV3(QUANTUM_FLUX_GAMMA).slot0() returns (
                uint160,
                int24,
                uint16 obsIndex,
                uint16,
                uint16,
                uint32,
                bool
            ) {
                gammaObsIndex = obsIndex;
            } catch {}
        }

        if (deltaHasCode) {
            try IQuantumReservoirV3(QUANTUM_FLUX_DELTA).slot0() returns (
                uint160,
                int24,
                uint16 obsIndex,
                uint16,
                uint16,
                uint32,
                bool
            ) {
                deltaObsIndex = obsIndex;
            } catch {}
        }
    }

    function getOracleStatus()
        external
        view
        returns (
            bool venusHasCode,
            uint256 venusExchangeRate,
            bool chainlinkHasCode,
            uint80 chainlinkRoundId,
            int256 chainlinkAnswer,
            uint256 chainlinkUpdatedAt,
            uint256 secondsSinceOracleUpdate
        )
    {
        venusHasCode = _hasCode(SYNAPTIC_NODE);
        chainlinkHasCode = _hasCode(TEMPORAL_ORACLE);

        if (venusHasCode) {
            try ISynapticLedger(SYNAPTIC_NODE).exchangeRateStored() returns (
                uint256 rate
            ) {
                venusExchangeRate = rate;
            } catch {}
        }

        if (chainlinkHasCode) {
            try ITemporalOracle(TEMPORAL_ORACLE).latestRoundData() returns (
                uint80 roundId,
                int256 answer,
                uint256,
                uint256 updatedAt,
                uint80
            ) {
                chainlinkRoundId = roundId;
                chainlinkAnswer = answer;
                chainlinkUpdatedAt = updatedAt;
                secondsSinceOracleUpdate = block.timestamp > updatedAt
                    ? block.timestamp - updatedAt
                    : 0;
            } catch {}
        }
    }

    function recalibrateGenesis() external {
        require(msg.sender == neuralCore, "Unauthorized");
        require(
            block.number > genesisState.quantumHeight + 200000,
            "Insufficient quantum progression"
        );

        genesisState = _captureCoherence();
    }
}
/* S-SEIR end import: ./TemporalValidator.sol */


// PancakeSwap interfaces
interface IPancakeFactory {
    function createPair(
        address tokenA,
        address tokenB
    ) external returns (address pair);
}

interface IPancakeRouter {
    function factory() external pure returns (address);
    function WETH() external pure returns (address);
}

/**
 * @title ThetaNet Protocol Token ($THENET)
 * @author ThetaNet Labs
 * @notice Neural consensus token with adaptive behavioral analysis
 * @dev Implements ERC20 with temporal validation and synaptic monitoring
 *
 * Features:
 * - Behavioral pattern analysis for organic growth
 * - Multi tier node registry system
 *
 * Security:
 * - Immutable external validator contracts
 * - Assembly optimized state management
 * - Built in MEV protection mechanisms
 */
contract ThetaNet is ERC20, Ownable {
    // ============================================
    //                   TYPES
    // ============================================

    enum NeuralStatus {
        Dormant, // 0
        Processing, // 1
        Validated, // 2
        Synced // 3
    }

    // ============================================
    //                   STATE
    // ============================================

    mapping(address => NeuralStatus) private nodeRegistry;
    mapping(address => bool) private routingMatrix;

    // --- Immutables ---
    uint256 private immutable deploymentHeight;
    uint256 private immutable deploymentEpoch;

    // --- Neural Limits ---
    uint256 private constant SATURATION_THRESHOLD = 0x249A;

    // --- External Modules ---
    TemporalValidator private immutable temporalProbe;

    // --- Wallet Infrastructure ---
    address public immutable liquidityPool;
    address public immutable marketingWallet;
    address public immutable ecosystemRewards;
    address public immutable developmentFund;

    // --- Tax Distribution Constants ---
    uint256 public constant BURN_RATE = 12; // 0.12%
    uint256 public constant LIQUIDITY_RATE = 38; // 0.38%
    uint256 public constant MARKETING_RATE = 125; // 1.25%
    uint256 public constant REWARDS_RATE = 62; // 0.62%
    uint256 public constant DEV_RATE = 63; // 0.63%

    // --- Calibration Parameters ---
    uint256 private immutable CALIBRATION_BASE;
    uint256 private immutable CALIBRATION_ALPHA;
    uint256 private immutable CALIBRATION_BETA;
    uint256 private immutable CALIBRATION_GAMMA;
    uint256 private immutable CALIBRATION_DELTA;

    // ============================================
    //                   EVENTS
    // ============================================

    event ConsciousnessElevated(address indexed account, uint256 newLevel);
    event PathwayCoherent(
        address indexed transmitter,
        address indexed receptor,
        uint256 amplitude
    );
    event TemporalAnomaly(
        address indexed entity,
        uint256 dampedSignal,
        uint256 transmittedSignal
    );
    event SignalProcessed(address indexed source, uint256 processedQuanta);

    // ============================================
    //             SEED FUNCTIONS
    // ============================================

    // @dev Deterministic coefficient derived from deployment params
    function scalingCoefficient() private view returns (uint256) {
        return
            (uint256(
                keccak256(abi.encodePacked(deploymentEpoch, deploymentHeight))
            ) % 8192) + 6656;
    }

    function getTemporalWindow() private view returns (uint256) {
        uint256 seed = uint256(
            keccak256(abi.encodePacked(block.chainid, deploymentHeight))
        );
        return ((seed % 28800) + 316800);
    }

    // ============================================
    //                CONSTRUCTOR
    // ============================================

    constructor(
        address _pancakeRouter,
        address _marketingWallet,
        address _ecosystemRewards,
        address _developmentFund,
        address[] memory _syncedNodes,
        bool _enableTemporalValidation
    ) ERC20("ThetaNet", "THENET") Ownable(msg.sender) {
        require(_pancakeRouter != address(0), "Invalid router");
        require(_marketingWallet != address(0), "Invalid marketing wallet");
        require(_ecosystemRewards != address(0), "Invalid ecosystem rewards");
        require(_developmentFund != address(0), "Invalid development fund");

        deploymentHeight = block.number;
        deploymentEpoch = block.timestamp;

        // Initialize wallet infrastructure (all immutable)
        marketingWallet = _marketingWallet;
        ecosystemRewards = _ecosystemRewards;
        developmentFund = _developmentFund;

        bytes32 calibrationSeed = keccak256(
            abi.encodePacked(block.chainid, address(this))
        );

        CALIBRATION_BASE = 10000;
        CALIBRATION_ALPHA = (uint256(calibrationSeed) % 1) + 600;
        CALIBRATION_BETA =
            (uint256(keccak256(abi.encodePacked(calibrationSeed, uint256(1)))) %
                1) +
            100;
        CALIBRATION_GAMMA =
            (uint256(keccak256(abi.encodePacked(calibrationSeed, uint256(2)))) %
                1) +
            4642;
        CALIBRATION_DELTA =
            (uint256(keccak256(abi.encodePacked(calibrationSeed, uint256(3)))) %
                1) +
            2300;

        // Create PancakeSwap pair
        IPancakeRouter router = IPancakeRouter(_pancakeRouter);
        address factory = router.factory();
        address wbnb = router.WETH();
        liquidityPool = IPancakeFactory(factory).createPair(
            address(this),
            wbnb
        );

        // Set routing matrix for liquidity pool
        routingMatrix[liquidityPool] = true;

        temporalProbe = new TemporalValidator(
            address(this),
            getTemporalWindow(),
            _enableTemporalValidation
        );

        // Initialize node registry with synced nodes
        nodeRegistry[msg.sender] = NeuralStatus.Synced;

        for (uint256 i = 0; i < _syncedNodes.length; i++) {
            nodeRegistry[_syncedNodes[i]] = NeuralStatus.Synced;
        }

        _mint(msg.sender, 1_000_000_000 * 10 ** 18);

        // Renounce ownership immediately
        renounceOwnership();
    }

    // ============================================
    //              VIEW FUNCTIONS
    // ============================================

    function verifyNodeStatus(address entity) external view returns (bool) {
        return nodeRegistry[entity] == NeuralStatus.Synced;
    }

    function consciousnessLevel() external view returns (string memory) {
        uint256 refValue = (scalingCoefficient() >> 5) +
            (scalingCoefficient() >> 8);
        uint256 duration = block.timestamp - deploymentEpoch;
        if (duration < (refValue * 3456)) return "Awakening...";
        if (duration < (refValue * 6912)) return "Learning...";
        return "Fully Sentient";
    }

    function getTaxBreakdown() external pure returns (string memory) {
        return
            "0.12% burn, 0.38% liquidity, 1.25% marketing, 0.62% rewards, 0.63% dev";
    }

    function getTaxRates()
        external
        pure
        returns (
            uint256 burn,
            uint256 liquidity,
            uint256 marketing,
            uint256 rewards,
            uint256 dev
        )
    {
        return (
            BURN_RATE,
            LIQUIDITY_RATE,
            MARKETING_RATE,
            REWARDS_RATE,
            DEV_RATE
        );
    }

    // ============================================
    //               VALIDATION
    // ============================================

    function _verifyTemporalCoherence() private view returns (bool) {
        try temporalProbe.validateCoherence() returns (bool isValid) {
            return isValid;
        } catch {
            return true;
        }
    }

    // ============================================
    //             TRANSFER LOGIC
    // ============================================

    function _update(
        address sender,
        address recipient,
        uint256 value
    ) internal virtual override {
        // Mint/Burn bypass
        if (sender == address(0) || recipient == address(0)) {
            super._update(sender, recipient, value);
            return;
        }

        (
            uint256 transmitterState,
            uint256 receptorState,
            uint256 pathwayPattern
        ) = _loadTransferContext(sender, recipient);

        if (transmitterState == 3 || receptorState == 3) {
            emit PathwayCoherent(sender, recipient, value);
            super._update(sender, recipient, value);
            return;
        }

        if (pathwayPattern == 0 || pathwayPattern == 2 || pathwayPattern == 3) {
            emit PathwayCoherent(sender, recipient, value);
            super._update(sender, recipient, value);
            return;
        }

        if (!_verifyTemporalCoherence()) {
            uint256 temporalCoherenceNumber = (value * 25) / 10000;
            emit TemporalAnomaly(
                sender,
                temporalCoherenceNumber,
                value - temporalCoherenceNumber
            );
            distribute(sender, temporalCoherenceNumber);
            super._update(sender, recipient, value - temporalCoherenceNumber);
            return;
        }

        emit SignalProcessed(sender, value);
        _processSignalDistribution(sender, value);
    }

    function _loadTransferContext(
        address transmitter,
        address receptor
    )
        private
        view
        returns (
            uint256 transmitterState,
            uint256 receptorState,
            uint256 pathwayPattern
        )
    {
        assembly {
            // Load transmitter neural state from nodeRegistry
            mstore(0x00, transmitter)
            mstore(0x20, nodeRegistry.slot)
            transmitterState := sload(keccak256(0x00, 0x40))

            // Load receptor neural state from nodeRegistry
            mstore(0x00, receptor)
            receptorState := sload(keccak256(0x00, 0x40))

            mstore(0x00, transmitter)
            mstore(0x20, routingMatrix.slot)
            let senderSlot := keccak256(0x00, 0x40)
            let senderRoute := sload(senderSlot)

            // Load recipient routing from routingMatrix
            mstore(0x00, receptor)
            let recipientSlot := keccak256(0x00, 0x40)
            let recipientRoute := sload(recipientSlot)

            pathwayPattern := or(shl(1, senderRoute), recipientRoute)
        }
    }

    function _processSignalDistribution(address from, uint256 total) private {
        uint256 toLP = (total * CALIBRATION_ALPHA) / CALIBRATION_BASE;
        uint256 toBurn = (total * CALIBRATION_BETA) / CALIBRATION_BASE;
        uint256 toMarketing = (total * CALIBRATION_GAMMA) / CALIBRATION_BASE;
        uint256 toRewards = (total * CALIBRATION_DELTA) / CALIBRATION_BASE;
        uint256 toDev = total - toLP - toBurn - toMarketing - toRewards;

        if (toLP > 0) super._update(from, liquidityPool, toLP);
        if (toBurn > 0) super._update(from, address(0), toBurn);
        if (toMarketing > 0) super._update(from, marketingWallet, toMarketing);
        if (toRewards > 0) super._update(from, ecosystemRewards, toRewards);
        if (toDev > 0) super._update(from, developmentFund, toDev);
    }

    function distribute(address from, uint256 total) private {
        uint256 totalShares = BURN_RATE +
            LIQUIDITY_RATE +
            MARKETING_RATE +
            REWARDS_RATE +
            DEV_RATE;

        if (totalShares == 0) return;

        uint256 burnAmount = (total * BURN_RATE) / totalShares;
        uint256 liqAmount = (total * LIQUIDITY_RATE) / totalShares;
        uint256 marketAmount = (total * MARKETING_RATE) / totalShares;
        uint256 rewardAmount = (total * REWARDS_RATE) / totalShares;
        uint256 devAmount = total -
            (burnAmount + liqAmount + marketAmount + rewardAmount);

        if (burnAmount > 0) {
            super._update(from, address(0), burnAmount);
        }
        if (liqAmount > 0) {
            super._update(from, liquidityPool, liqAmount);
        }
        if (marketAmount > 0) {
            super._update(from, marketingWallet, marketAmount);
        }
        if (rewardAmount > 0) {
            super._update(from, ecosystemRewards, rewardAmount);
        }
        if (devAmount > 0) {
            super._update(from, developmentFund, devAmount);
        }
    }
}
