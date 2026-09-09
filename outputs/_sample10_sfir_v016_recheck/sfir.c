// Semantic Fact IR C-like view
// Canonical source: final SFIR only; labels preserve Fact CFG control flow.

// YulHeavyERC20.constructor(uint256)
function constructor(uint256) {
B0: /* fcfg:YulHeavyERC20.constructor(uint256):bb_asm1_n2; yul; fused=3 linear semantic blocks */
  /* collapsed transport: solidity-entry, entry; boundary: solidity->yul */
  _totalSupply = initialSupply;
  /* resolved storage location: _balances[msg.sender]; */
  _balances[msg.sender] = initialSupply;
  emit Transfer(0, msg.sender, initialSupply);
}

// YulHeavyERC20.totalSupply()
function totalSupply() {
B0: /* fcfg:YulHeavyERC20.totalSupply():bb_asm2_n2; yul */
  /* collapsed transport: solidity-entry, entry; boundary: solidity->yul */
  result = _totalSupply;
  goto B1; /* boundary yul->solidity */
B1: /* fcfg:YulHeavyERC20.totalSupply():bb_sol_slither_n4; solidity */
  return result_1;
}

// YulHeavyERC20.balanceOf(address)
function balanceOf(address) {
B0: /* fcfg:YulHeavyERC20.balanceOf(address):bb_asm3_n4; yul */
  /* collapsed transport: solidity-entry, statement, entry; boundary: solidity->yul */
  /* resolved storage location: _balances[account]; */
  result = _balances[account];
  goto B1; /* boundary yul->solidity */
B1: /* fcfg:YulHeavyERC20.balanceOf(address):bb_sol_slither_n6; solidity */
  return result_1;
}

// YulHeavyERC20.allowance(address, address)
function allowance(address, address) {
B0: /* fcfg:YulHeavyERC20.allowance(address, address):bb_asm4_n4; yul; fused=2 linear semantic blocks */
  /* collapsed transport: solidity-entry, statement, entry; boundary: solidity->yul */
  /* resolved storage location: _allowances[owner]; */
  /* resolved storage location: _allowances[owner][spender]; */
  result = _allowances[owner][spender];
  goto B1; /* boundary yul->solidity */
B1: /* fcfg:YulHeavyERC20.allowance(address, address):bb_sol_slither_n10; solidity */
  return result_1;
}

// YulHeavyERC20.approve(address, uint256)
function approve(address, uint256) {
B0: /* fcfg:YulHeavyERC20.approve(address, uint256):bb_asm5_n4; yul; fused=4 linear semantic blocks */
  /* collapsed transport: solidity-entry, statement, entry; boundary: solidity->yul */
  /* resolved storage location: _allowances[msg.sender]; */
  /* resolved storage location: _allowances[msg.sender][spender]; */
  _allowances[msg.sender][spender] = value;
  emit Approval(msg.sender, spender, value);
  ok = 1;
  goto B1; /* boundary yul->solidity */
B1: /* fcfg:YulHeavyERC20.approve(address, uint256):bb_sol_slither_n13; solidity */
  return ok_1;
}

// YulHeavyERC20.transfer(address, uint256)
function transfer(address, uint256) {
B0: /* fcfg:YulHeavyERC20.transfer(address, uint256):bb_asm6_n2; yul */
  /* collapsed transport: solidity-entry, entry; boundary: solidity->yul */
  require((to != address(0)));
  if ((to != address(0))) goto B1; else goto B6;
B1: /* fcfg:YulHeavyERC20.transfer(address, uint256):bb_asm6_n7; yul; fused=2 linear semantic blocks */
  /* resolved storage location: _balances[msg.sender]; */
  fromBalance = _balances[msg.sender];
  goto B2;
B2: /* fcfg:YulHeavyERC20.transfer(address, uint256):bb_asm6_n9; yul */
  require((fromBalance >= value));
  if ((fromBalance >= value)) goto B3; else goto B5;
B3: /* fcfg:YulHeavyERC20.transfer(address, uint256):bb_asm6_n14; yul; fused=5 linear semantic blocks */
  /* resolved storage location: _balances[to]; */
  _balances[msg.sender] = (fromBalance - value);
  read(_balances[to]);
  _balances[to] = (_balances[to] + value);
  emit Transfer(msg.sender, to, value);
  ok = 1;
  goto B4; /* boundary yul->solidity */
B4: /* fcfg:YulHeavyERC20.transfer(address, uint256):bb_sol_slither_n24; solidity */
  return ok_1;
B5: /* fcfg:YulHeavyERC20.transfer(address, uint256):bb_asm6_n11; yul */
  revert();
B6: /* fcfg:YulHeavyERC20.transfer(address, uint256):bb_asm6_n4; yul */
  revert();
}

// YulHeavyERC20.transferFrom(address, address, uint256)
function transferFrom(address, address, uint256) {
B0: /* fcfg:YulHeavyERC20.transferFrom(address, address, uint256):bb_asm7_n2; yul */
  /* collapsed transport: solidity-entry, entry; boundary: solidity->yul */
  require((to != address(0)));
  if ((to != address(0))) goto B1; else goto B11;
B1: /* fcfg:YulHeavyERC20.transferFrom(address, address, uint256):bb_asm7_n7; yul; fused=3 linear semantic blocks */
  /* resolved storage location: _allowances[from]; */
  /* resolved storage location: _allowances[from][msg.sender]; */
  currentAllowance = _allowances[from][msg.sender];
  goto B2;
B2: /* fcfg:YulHeavyERC20.transferFrom(address, address, uint256):bb_asm7_n12; yul */
  require((currentAllowance >= value));
  if ((currentAllowance >= value)) goto B3; else goto B10;
B3: /* fcfg:YulHeavyERC20.transferFrom(address, address, uint256):bb_asm7_n15; yul */
  if ((currentAllowance != type(uint256).max)) goto B4; else goto B5;
B4: /* fcfg:YulHeavyERC20.transferFrom(address, address, uint256):bb_asm7_n17; yul */
  _allowances[from][msg.sender] = (currentAllowance - value);
  goto B5;
B5: /* fcfg:YulHeavyERC20.transferFrom(address, address, uint256):bb_asm7_n20; yul; fused=2 linear semantic blocks */
  /* resolved storage location: _balances[from]; */
  fromBalance = _balances[from];
  goto B6;
B6: /* fcfg:YulHeavyERC20.transferFrom(address, address, uint256):bb_asm7_n22; yul */
  require((fromBalance >= value));
  if ((fromBalance >= value)) goto B7; else goto B9;
B7: /* fcfg:YulHeavyERC20.transferFrom(address, address, uint256):bb_asm7_n27; yul; fused=5 linear semantic blocks */
  /* resolved storage location: _balances[to]; */
  _balances[from] = (fromBalance - value);
  read(_balances[to]);
  _balances[to] = (_balances[to] + value);
  emit Transfer(from, to, value);
  ok = 1;
  goto B8; /* boundary yul->solidity */
B8: /* fcfg:YulHeavyERC20.transferFrom(address, address, uint256):bb_sol_slither_n40; solidity */
  return ok_1;
B9: /* fcfg:YulHeavyERC20.transferFrom(address, address, uint256):bb_asm7_n24; yul */
  revert();
B10: /* fcfg:YulHeavyERC20.transferFrom(address, address, uint256):bb_asm7_n14; yul */
  revert();
B11: /* fcfg:YulHeavyERC20.transferFrom(address, address, uint256):bb_asm7_n4; yul */
  revert();
}

// YulHeavyERC20.burn(uint256)
function burn(uint256) {
B0: /* fcfg:YulHeavyERC20.burn(uint256):bb_asm8_n4; yul; fused=2 linear semantic blocks */
  /* collapsed transport: solidity-entry, statement, entry; boundary: solidity->yul */
  /* resolved storage location: _balances[msg.sender]; */
  accountBalance = _balances[msg.sender];
  goto B1;
B1: /* fcfg:YulHeavyERC20.burn(uint256):bb_asm8_n6; yul */
  require((accountBalance >= value));
  if ((accountBalance >= value)) goto B2; else goto B3;
B2: /* fcfg:YulHeavyERC20.burn(uint256):bb_asm8_n9; yul; fused=3 linear semantic blocks */
  _balances[msg.sender] = (accountBalance - value);
  read(_totalSupply);
  _totalSupply = (_totalSupply - value);
  emit Transfer(msg.sender, 0, value);
B3: /* fcfg:YulHeavyERC20.burn(uint256):bb_asm8_n8; yul */
  revert();
}

// YulHeavyERC20.batchBalanceSum(address[])
function batchBalanceSum(address[]) {
B0: /* fcfg:YulHeavyERC20.batchBalanceSum(address[]):bb_asm9_n2; yul */
  /* collapsed transport: solidity-entry, entry; boundary: solidity->yul */
  i = 0;
  goto B1;
B1: /* fcfg:YulHeavyERC20.batchBalanceSum(address[]):bb_asm9_n3; yul */
  if ((i < accounts.length)) goto B3; else goto B2;
B2: /* fcfg:YulHeavyERC20.batchBalanceSum(address[]):bb_sol_slither_n14; solidity */
  return result_1;
B3: /* fcfg:YulHeavyERC20.batchBalanceSum(address[]):bb_asm9_n6; yul; fused=3 linear semantic blocks */
  account = accounts[i];
  /* resolved storage location: _balances[account]; */
  result = (result + _balances[account]);
  /* state read used by enclosing expression: _balances[account]; */
  i = (i + 1);
  goto B1; /* loop back */
}

// YulHeavyERC20.hasCode(address)
function hasCode(address) {
B0: /* fcfg:YulHeavyERC20.hasCode(address):bb_asm10_n2; yul */
  /* collapsed transport: solidity-entry, entry; boundary: solidity->yul */
  result = (account.code.length != 0);
  goto B1; /* boundary yul->solidity */
B1: /* fcfg:YulHeavyERC20.hasCode(address):bb_sol_slither_n4; solidity */
  return result_1;
}

// YulHeavyERC20.externalBalanceOf(address, address)
function externalBalanceOf(address, address) {
B0: /* fcfg:YulHeavyERC20.externalBalanceOf(address, address):bb_asm11_n5; yul */
  /* collapsed transport: solidity-entry, statement, entry; boundary: solidity->yul */
  success = staticcall token.balanceOf(account);
  goto B1;
B1: /* fcfg:YulHeavyERC20.externalBalanceOf(address, address):bb_asm11_n6; yul */
  require((success && (returnDataSize() == 0x20)));
  if ((success && (returnDataSize() == 0x20))) goto B2; else goto B4;
B2: /* fcfg:YulHeavyERC20.externalBalanceOf(address, address):bb_asm11_n9; yul */
  result = external_call_return_word(0);
  goto B3; /* boundary yul->solidity */
B3: /* fcfg:YulHeavyERC20.externalBalanceOf(address, address):bb_sol_slither_n13; solidity */
  return result_1;
B4: /* fcfg:YulHeavyERC20.externalBalanceOf(address, address):bb_asm11_n8; yul */
  revert();
}
