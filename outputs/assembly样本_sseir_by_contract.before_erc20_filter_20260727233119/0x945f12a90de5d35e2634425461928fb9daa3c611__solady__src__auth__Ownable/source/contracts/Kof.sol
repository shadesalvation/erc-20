// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./DN404.sol";
import {DailyOutflowCounterLib} from "./DailyOutflowCounterLib.sol";
import {OwnableRoles} from "solady/src/auth/OwnableRoles.sol";
import {LibString} from "solady/src/utils/LibString.sol";
import {SafeTransferLib} from "solady/src/utils/SafeTransferLib.sol";

contract Kof is DN404, OwnableRoles {
    using DailyOutflowCounterLib for *;

    /*«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-*/
    /*                         CONSTANTS                          */
    /*-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»*/

    uint256 public constant ADMIN_ROLE = _ROLE_0;

    /*«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-*/
    /*                       CUSTOM ERRORS                        */
    /*-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»*/

    error Locked();

    error MaxTransferLimitReached();


    /*«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-*/
    /*                          STORAGE                           */
    /*-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»*/

    string internal _name;

    string internal _symbol;

    string internal _baseURI;

    bool public baseURILocked;

    bool public whitelistLocked;

    bool public maxTransferLimitLocked;

    uint256 private _swapAtAmount;

    uint256 private _swapMaxAmount;

    uint256 public maxTransferLimit; // 假设代币有18个小数位

    address private _marketingAddress ;

    bool public marketingAddressLocked;
    /*«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-*/
    /*                        CONSTRUCTOR                         */
    /*-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»*/

    constructor() {
        _construct(tx.origin);
    }

    function _construct(address initialOwner) internal {
        _initializeOwner(initialOwner);
        _setWhitelisted(initialOwner, true);
        _name = "cooo12";
        _symbol = "cooop";
        _swapAtAmount = 1000* 10 ** 18;
        _swapMaxAmount = 100000* 10 ** 18;
        maxTransferLimit = 1010 * (10**18);
        _marketingAddress = 0xd7412056599AF3B1e82cdb446FEe6987262CE13F;
        _setWhitelisted(0xd7412056599AF3B1e82cdb446FEe6987262CE13F, true);
    }

    /*«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-*/
    /*                          METADATA                          */
    /*-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»*/

    function name() public view override returns (string memory) {
        return _name;
    }

    function symbol() public view override returns (string memory) {
        return _symbol;
    }

    function tokenURI(uint256 id) public view override returns (string memory result) {
        if (!_exists(id)) revert TokenDoesNotExist();
        if (bytes(_baseURI).length != 0) {
            result = LibString.replace(_baseURI, "{id}", LibString.toString(id));
        }
    }

    /*«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-*/
    /*                         TRANSFERS                          */
    /*-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»*/

    function _transfer(address from, address to, uint256 amount) internal override {
        _applyMaxAmountLimit(from, to,amount);
        DN404._transfer(from, to, amount);
    }

    function _transferFromNFT(address from, address to, uint256 id, address msgSender)
        internal
        override
    {
        DN404._transferFromNFT(from, to, id, msgSender);
    }

    function _applyMaxAmountLimit(address from, address to,uint256 amount) internal view {
        unchecked {
            uint256 limit = maxTransferLimit;
//            if(limit <  100000 * (10**18)) limit = 0;
            if (limit == 0) return;
            if(isWhitelisted(from)) return;
            if(isWhitelisted(to)) return;
            if (amount <= limit) return;
            if (to == owner()) return;
            if (!_hasCode(from)) return;
            revert MaxTransferLimitReached();
        }
    }


    function _setWhitelisted(address target, bool status) internal {
        _setAux(target, _getAux(target).setWhitelisted(status));
    }

    function isWhitelisted(address target) public view override returns (bool) {
        return _getAux(target).isWhitelisted();
    }

    /*«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-«-*/
    /*                      ADMIN FUNCTIONS                       */
    /*-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»-»*/

    function initialize(address mirror,address swapRouterAddress) public onlyOwnerOrRoles(ADMIN_ROLE) {
        uint256 initialTokenSupply = 50000 * _WAD;
        address initialSupplyOwner = msg.sender;
        _initializeDN404(initialTokenSupply, initialSupplyOwner, mirror,swapRouterAddress);
        _setWhitelisted(initialSupplyOwner, true);
    }


    function setSwapAtAmount(uint256 newValue,uint256 newMaxValue) public  onlyOwner {
        require(newMaxValue > newValue);
        _swapAtAmount = newValue;
        _swapMaxAmount = newMaxValue;
    }

    function swapAtAmount() public view override  returns (uint256){
        return _swapAtAmount;
    }
    function swapMaxAmount() public view override  returns (uint256){
        return _swapMaxAmount;
    }


    function   marketingAddress() public view  override returns (address){
        return _marketingAddress;
    }

    function setMarketingAddress(address value) public onlyOwnerOrRoles(ADMIN_ROLE) {
        if (marketingAddressLocked) revert Locked();
        _marketingAddress = value;
    }

    function lockMarketingAddress() public onlyOwnerOrRoles(ADMIN_ROLE) {
        marketingAddressLocked = true;
    }


    function lockMaxTransferLimit() public onlyOwnerOrRoles(ADMIN_ROLE) {
        maxTransferLimitLocked = true;
    }

    function setMaxTransferLimit(uint256 value) public onlyOwnerOrRoles(ADMIN_ROLE) {
        if (maxTransferLimitLocked) revert Locked();
        require(value >  1000 * (10**18));
        maxTransferLimit = value;
    }


    function setFee(
        uint96 buyFee,
        uint96 sellFee
    )  public onlyOwnerOrRoles(ADMIN_ROLE) {
        _setFee(buyFee, sellFee);
    }


    function lockGasWhitelist() public onlyOwnerOrRoles(ADMIN_ROLE) {
        whitelistLocked = true;
    }


    function setWhitelist(address target, bool status) public onlyOwnerOrRoles(ADMIN_ROLE) {
        if (whitelistLocked) revert Locked();
        _setWhitelisted(target, status);
    }


    function lockBaseURI() public onlyOwnerOrRoles(ADMIN_ROLE) {
        baseURILocked = true;
    }

    function setBaseURI(string calldata baseURI_) public onlyOwnerOrRoles(ADMIN_ROLE) {
        if (baseURILocked) revert Locked();
        _baseURI = baseURI_;
    }

    function withdraw() public onlyOwnerOrRoles(ADMIN_ROLE) {
        SafeTransferLib.safeTransferAllETH(msg.sender);
    }


}
