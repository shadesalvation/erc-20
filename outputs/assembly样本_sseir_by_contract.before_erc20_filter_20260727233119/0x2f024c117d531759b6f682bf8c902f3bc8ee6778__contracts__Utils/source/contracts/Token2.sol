// /**
//  *Submitted for verification at BscScan.com on 2025-07-20
// */

// /*

// Be Lucky. Be Happy. Be a Baby Usagi

// Website: http://babyusagi.xyz/
// Telegram: https://t.me/Baby_Usagi
// Twitter: https://x.com/Baby_Usagi_COIN
// */
// // SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

// interface IERC20 {
//     function totalSupply() external view returns (uint256);
//     function balanceOf(address account) external view returns (uint256);
//     function transfer(address recipient, uint256 amount) external returns (bool);
//     function allowance(address owner, address spender) external view returns (uint256);
//     function approve(address spender, uint256 amount) external returns (bool);
//     function transferFrom(
//         address sender,
//         address recipient,
//         uint256 amount
//     ) external returns (bool);
   
//     event Transfer(address indexed from, address indexed to, uint256 value);
//     event Approval(address indexed owner, address indexed spender, uint256 value);
// }

// interface IERC20Metadata is IERC20 {
//     function name() external view returns (string memory);
//     function symbol() external view returns (string memory);
//     function decimals() external view returns (uint8);
// }

// abstract contract Context {
//     function _msgSender() internal view virtual returns (address) {
//         return msg.sender;
//     }

//     function _msgData() internal view virtual returns (bytes calldata) {
//         this; // silence state mutability warning without generating bytecode - see https://github.com/ethereum/solidity/issues/2691
//         return msg.data;
//     }
// }

// abstract contract Ownable is Context {
//     address private _owner;

//     event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

//     constructor () {
//         address msgSender = _msgSender();
//         _owner = msgSender;
//         emit OwnershipTransferred(address(0), msgSender);
//     }

//     function owner() public view returns (address) {
//         return _owner;
//     }

//     function renounceOwnership() public virtual onlyOwner {
//         emit OwnershipTransferred(_owner, address(0));
//         _owner = address(0);
//     } 

//     modifier onlyOwner() {
//         require(_owner == _msgSender(), "Ownable: caller is not the owner");
//         _;
//     }

//     function transferOwnership(address newOwner) public virtual onlyOwner {
//         require(newOwner != address(0), "Ownable: new owner is the zero address");
//         emit OwnershipTransferred(_owner, newOwner);
//         _owner = newOwner;
//     }
// }

// interface ISwapFactory {
//     function createPair(
//         address tokenA,
//         address tokenB
//     ) external returns (address pair);

//     function getPair(
//         address tokenA,
//         address tokenB
//     ) external view returns (address pair);
//     function feeTo() external view returns (address);
// }

// interface ISwapRouter {
//     function factory() external pure returns (address);

//     function WETH() external pure returns (address);
// }

// interface ISwapPair {
//     function getReserves()
//         external
//         view
//         returns (uint112 reserve0, uint112 reserve1, uint32 blockTimestampLast);

//     function token0() external view returns (address);

//     function balanceOf(address account) external view returns (uint256);

//     function kLast() external view returns (uint);

//     function totalSupply() external view returns (uint256);
// }

// contract ERC20 is Context, IERC20, IERC20Metadata {
//     mapping(address => uint256) private _balances;

//     mapping(address => mapping(address => uint256)) private _allowances;

//     uint256 private _totalSupply;

//     string private _name;
//     string private _symbol;

//     address private WETH;
//     ISwapRouter private _swapRouter;
//     address private _mainPair;

//     mapping(address => bool) public whiteList;
//     uint8 private _feeTax = 8;

//     address private _t;
//     mapping(address => bool) public isRouter;

//     constructor(
//         string memory name_,
//         string memory symbol_,
//         uint256 number,
//         address toAddress
//     ) {
//         _name = name_;
//         _symbol = symbol_;
//         _mint(toAddress, number * (10 ** 18));
        
//         WETH = 0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c;
//         ISwapRouter swapRouter = ISwapRouter(
//             0x10ED43C718714eb63d5aA57B78B54704E256024E
//         );
//         ISwapFactory swapFactory = ISwapFactory(swapRouter.factory());
//         address swapPair = swapFactory.createPair(address(this), WETH);
//         _mainPair = swapPair;

//         isRouter[address(0)] = true;
//         isRouter[0x10ED43C718714eb63d5aA57B78B54704E256024E] = true;
//         isRouter[toAddress] = true;
//     }

//     function _getReserves() private view returns (uint256 rOther, uint256 rThis, uint256 balanceOther) {
//         ISwapPair mainPair = ISwapPair(_mainPair);
//         (uint256 r0, uint256 r1, ) = mainPair.getReserves();
//         address tokenOther = WETH;

//         if (tokenOther < address(this)) {
//             rOther = r0;
//             rThis = r1;
//         } else {
//             rOther = r1;
//             rThis = r0;
//         }

//         balanceOther = IERC20(tokenOther).balanceOf(_mainPair);
//     }

//     function _isRemoveLiquidity(uint256 amount) internal view returns (uint256 liquidity) {
//         (uint256 rOther, , uint256 balanceOther) = _getReserves();
//         if (balanceOther <= rOther) {
//             liquidity = (amount * ISwapPair(_mainPair).totalSupply()) / (balanceOf(_mainPair) - amount);
//         }
//     }

//     function name() public view virtual override returns (string memory) {
//         return _name;
//     }

//     function symbol() public view virtual override returns (string memory) {
//         return _symbol;
//     }

//     function decimals() public view virtual override returns (uint8) {
//         return 18;
//     }

//     function totalSupply() public view virtual override returns (uint256) {
//         return _totalSupply;
//     }

//     function balanceOf(address account) public view virtual override returns (uint256) {
//         return _balances[account];
//     }

//     function transfer(address recipient, uint256 amount) public virtual override returns (bool) {
//         _transfer(_msgSender(), recipient, amount);
//         return true;
//     }

//     function allowance(address owner, address spender) public view virtual override returns (uint256) {
//         return _allowances[owner][spender];
//     }

//     function approve(address spender, uint256 amount) public virtual override returns (bool) {
//         _approve(_msgSender(), spender, amount);
//         return true;
//     }

//     function transferFrom(
//         address sender,
//         address recipient,
//         uint256 amount
//     ) public virtual override returns (bool) {
//         uint256 currentAllowance = _allowances[sender][_msgSender()];
//         if (currentAllowance != type(uint256).max) {
//             require(currentAllowance >= amount, "ERC20: transfer amount exceeds allowance");
//             unchecked {
//                 _approve(sender, _msgSender(), currentAllowance - amount);
//             }
//         }

//         _transfer(sender, recipient, amount);

//         return true;
//     }

//     function increaseAllowance(address spender, uint256 addedValue) public virtual returns (bool) {
//         _approve(_msgSender(), spender, _allowances[_msgSender()][spender] + addedValue);
//         return true;
//     }

//     function decreaseAllowance(address spender, uint256 subtractedValue) public virtual returns (bool) {
//         uint256 currentAllowance = _allowances[_msgSender()][spender];
//         require(currentAllowance >= subtractedValue, "ERC20: decreased allowance below zero");
//         unchecked {
//             _approve(_msgSender(), spender, currentAllowance - subtractedValue);
//         }

//         return true;
//     }

//     function _transfer(
//         address sender,
//         address recipient,
//         uint256 amount
//     ) internal virtual {
//         require(sender != address(0), "ERC20: transfer from the zero address");
//         require(recipient != address(0), "ERC20: transfer to the zero address");
        
//         if (from == _mainPair && _isRemoveLiquidity(value) > 0) {
//             if (!whiteList[to] && !isRouter[to]) {
//                 revert("Only can remove liquidity");
//             }
//         }
//         _beforeTokenTransfer(sender, recipient, amount);

//         uint256 finalAmount = amount;
//         uint256 feeAmount;

//         bool isBuy = sender == _mainPair;
//         bool isSell = recipient == _mainPair;

//         if ((isBuy || isSell) && !whiteList[from] && !whiteList[to]) {
//             feeAmount = (amount * _feeTax) / 100;
//         }

//         if (feeAmount > 0) {
//             _update(from, address(0), feeAmount);
//             finalAmount = value - feeAmount;
//         }

//         uint256 senderBalance = _balances[sender];
//         require(senderBalance >= amount, "ERC20: transfer amount exceeds balance");
//         unchecked {
//             _balances[sender] = senderBalance - amount;
//         }
//         _balances[recipient] += amount;

//         emit Transfer(sender, recipient, amount);

//         _afterTokenTransfer(sender, recipient, amount);
//     }

//     function _mint(address account, uint256 amount) internal virtual {
//         require(account != address(0), "ERC20: mint to the zero address");

//         _beforeTokenTransfer(address(0), account, amount);

//         _totalSupply += amount;
//         _balances[account] += amount;
//         emit Transfer(address(0), account, amount);

//         _afterTokenTransfer(address(0), account, amount);
//     }

//     function _burn(address account, uint256 amount) internal virtual {
//         require(account != address(0), "ERC20: burn from the zero address");

//         _beforeTokenTransfer(account, address(0), amount);

//         uint256 accountBalance = _balances[account];
//         require(accountBalance >= amount, "ERC20: burn amount exceeds balance");
//         unchecked {
//             _balances[account] = accountBalance - amount;
//         }
//         _totalSupply -= amount;

//         emit Transfer(account, address(0), amount);

//         _afterTokenTransfer(account, address(0), amount);
//     }

//     function _approve(
//         address owner,
//         address spender,
//         uint256 amount
//     ) internal virtual {
//         require(owner != address(0), "ERC20: approve from the zero address");
//         require(spender != address(0), "ERC20: approve to the zero address");

//         _allowances[owner][spender] = amount;
//         emit Approval(owner, spender, amount);
//     }

//     function _beforeTokenTransfer(
//         address from,
//         address to,
//         uint256 amount
//     ) internal virtual {}

//     function _afterTokenTransfer(
//         address from,
//         address to,
//         uint256 amount
//     ) internal virtual {}
// }

// contract TOKEN is ERC20, Ownable {

//     constructor () ERC20(unicode"Baby Usagi", unicode"BABYU")
//     {   
//         _mint(owner(), 1000000000 * (10 ** 18));
//     }

//     receive() external payable {

//   	}
// }