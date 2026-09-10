// Semantic Fact IR C-like view
// Canonical source: final SFIR only; labels preserve Fact CFG control flow.

// IComplexQuoteOracle.quote(address, uint256)
function quote(address, uint256) {
}

// ComplexSoliditySFIR._discount(uint256, uint256)
function _discount(uint256, uint256) {
B0: /* fcfg:ComplexSoliditySFIR._discount(uint256, uint256):bb_sol_slither_n2; solidity */
  /* collapsed transport: solidity-entry */
  TMP_0 = quote_1 > quota_1;
  if (quote > quota) goto B2; else goto B1;
B1: /* fcfg:ComplexSoliditySFIR._discount(uint256, uint256):bb_sol_slither_n4; solidity */
  return quote_1;
B2: /* fcfg:ComplexSoliditySFIR._discount(uint256, uint256):bb_sol_slither_n3; solidity */
  return quota_1;
}

// ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[])
function settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]) {
B0: /* fcfg:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):bb_sol_slither_n1; solidity; fused=5 linear semantic blocks */
  /* collapsed transport: solidity-entry */
  /* type_conversion: semantic node sfir:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):sol_atom_2 */
  TMP_2 = recipient_1 != TMP_1;
  TMP_3 = amount_1 != 0;
  TMP_4 = TMP_2 && TMP_3;
  require(/* unresolved condition */);
  /* resolved storage location: accounts[msg.sender]; */
  REF_0 = accounts[msg.sender];
  /* value_assign: semantic node sfir:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):sol_atom_8 */
  /* member_access: semantic node sfir:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):sol_atom_9 */
  require(/* unresolved condition */);
  /* value_assign: semantic node sfir:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):sol_atom_11 */
  /* value_assign: semantic node sfir:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):sol_atom_12 */
  goto B1;
B1: /* fcfg:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):bb_sol_slither_n5; solidity */
  goto B2;
B2: /* fcfg:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):bb_sol_slither_n8; solidity */
  /* length_read: semantic node sfir:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):sol_atom_14 */
  TMP_7 = i_2 < REF_2;
  if (i < multipliers.length) goto B3; else goto B8;
B3: /* fcfg:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):bb_sol_slither_n9; solidity */
  /* index_access: semantic node sfir:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):sol_atom_17 */
  /* value_assign: semantic node sfir:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):sol_atom_18 */
  goto B4;
B4: /* fcfg:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):bb_sol_slither_n10; solidity */
  TMP_8 = factor_1 != 0;
  if (factor != 0) goto B5; else goto B6;
B5: /* fcfg:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):bb_sol_slither_n11; solidity */
  weight_2 = weight_1 + factor_1;
  goto B6;
B6: /* fcfg:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):bb_sol_slither_n12; solidity */
  goto B7;
B7: /* fcfg:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):bb_sol_slither_n13; solidity */
  i_3 = i_2 + 1;
  goto B2;
B8: /* fcfg:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):bb_sol_slither_n6; solidity */
  goto B9;
B9: /* fcfg:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):bb_sol_slither_n14; solidity */
  TMP_9 = amount_1 * weight_1;
  /* value_assign: semantic node sfir:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):sol_atom_25 */
  goto B10;
B10: /* fcfg:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):bb_sol_slither_n28; solidity */
  /* member_access: semantic node sfir:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):sol_atom_57 */
  TMP_17 = requested_1 > REF_14;
  if (requested > sender.quota) goto B12; else goto B11;
B11: /* fcfg:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):bb_sol_slither_n30; solidity */
  /* value_assign: semantic node sfir:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):sol_atom_62 */
  goto B13;
B12: /* fcfg:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):bb_sol_slither_n29; solidity */
  /* member_access: semantic node sfir:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):sol_atom_60 */
  /* value_assign: semantic node sfir:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):sol_atom_61 */
  goto B13;
B13: /* fcfg:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):bb_sol_slither_n31; solidity */
  goto B14;
B14: /* fcfg:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):bb_sol_slither_n16; solidity; fused=6 linear semantic blocks */
  /* member_access: semantic node sfir:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):sol_atom_26 */
  TMP_10 = actual_3 <= REF_4;
  require(/* unresolved condition */);
  /* member_access: semantic node sfir:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):sol_atom_29 */
  REF_5 = REF_5 - actual_3;
  /* resolved storage location: accounts[recipient]; */
  /* resolved storage location: accounts[recipient].balance; */
  REF_7 = accounts[recipient].balance;
  accounts[recipient].balance = (accounts[recipient].balance + actual);
  /* resolved storage location: allowances[msg.sender]; */
  /* resolved storage location: allowances[msg.sender][spender]; */
  allowances[msg.sender][spender] = actual;
  TMP_12 = call(oracle_1, recipient_1, actual_3);
  /* value_assign: semantic node sfir:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):sol_atom_43 */
  /* member_access: semantic node sfir:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):sol_atom_44 */
  TMP_13 = _discount(quote_1, REF_11);
  /* value_assign: semantic node sfir:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):sol_atom_46 */
  goto B15;
B15: /* fcfg:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):bb_sol_slither_n22; solidity */
  TMP_14 = discounted_1 > 0;
  if (discounted > 0) goto B17; else goto B16;
B16: /* fcfg:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):bb_sol_slither_n25; solidity */
  emit SettlementSkipped();
  goto B18;
B17: /* fcfg:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):bb_sol_slither_n23; solidity; fused=2 linear semantic blocks */
  /* resolved storage location: accounts[recipient]; */
  /* resolved storage location: accounts[recipient].quota; */
  REF_13 = accounts[recipient].quota;
  accounts[recipient].quota = (accounts[recipient].quota + _discount(quote,sender.quota));
  emit Settled();
  goto B18;
B18: /* fcfg:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):bb_sol_slither_n26; solidity */
  goto B19;
B19: /* fcfg:ComplexSoliditySFIR.settle(contract IComplexQuoteOracle, address, address, uint256, uint256[]):bb_sol_slither_n27; solidity */
  return discounted_1;
}
