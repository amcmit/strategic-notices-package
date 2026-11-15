# V3 design sensitivity: independently verified descriptive results

13 November 2026. This is a **design-set descriptive sensitivity**, not a new confirmatory test. The R3 verifier replayed all 1,536 new paths, recomputed the ten-row fixed table and expedite choices, and passed. The frozen confirmation finding and all primary/secondary decisions are unchanged. No confirmation case was accessed; M5 and the LLM arm remain paused.

Execution manifest SHA-256: `82ef2a54a3946497f1c254f6d64e7fbf0eacc2e7061762c2e526397ed8ec6694`. Result SHA-256: `99c1b064b04e7cc65a97620b8f94d44ff6a27ff3eeb0841f7ac9a62497613763`. Verification SHA-256: `f654c62d1065035f3eb061fd160e210488a01f55c2482a3ecb91c8ffd5de1055`. Approved R3 verifier SHA-256: `ac203cb16ca512cfa894a918f41d749e3b00597f4ad70eab383ade213789fde8`.

The single design attempt completed 1,536 paths and 30,720 MIP phases, with no runner failures, in 198.800 seconds at 183.562 MiB peak RSS. The independent verifier returned `verified`, replayed 1,536 paths, and matched the entire fixed table. No new solve was made during verification.

## Fixed ten-row table

Each value is **mean [minimum, maximum]** across all 56 design cases. The [full 560-case table](results/v3_design_sensitivity_all_cases_verified.csv) preserves per-case outcomes, critical-penalty category and terminal backlog. These are descriptive values under fixed, assumed parameters.

| Arm | Level | Backup displacement b | Extra primary d | Residual e | Shortfall s | Supplier gain |
|---|---|---:|---:|---:|---:|---:|
| baseline | h20 | 4.696 [0.000, 18.000] | 3.723 [-2.000, 16.750] | -0.973 [-2.750, 0.000] | 0.554 [0.000, 1.000] | 8.402 [-11.000, 48.000] |
| backlog_catchup | catchup_0.5_cost_1 | 1.286 [0.000, 18.000] | 1.013 [-1.000, 15.000] | -0.272 [-3.000, 0.000] | 0.540 [0.000, 1.000] | 0.339 [-8.000, 45.000] |
| broker_spot | capacity_0.5_price_6 | 3.643 [0.000, 18.000] | 2.804 [-2.000, 16.750] | -0.839 [-2.750, 0.000] | 0.554 [0.000, 1.000] | 5.643 [-11.000, 45.250] |
| positive_contradiction_fee | F3 | 4.696 [0.000, 18.000] | 3.723 [-2.000, 16.750] | -0.973 [-2.750, 0.000] | 0.554 [0.000, 1.000] | 5.402 [-14.000, 45.000] |
| no_damages | lambda0 | 4.696 [0.000, 18.000] | 3.723 [-2.000, 16.750] | -0.973 [-2.750, 0.000] | 0.554 [0.000, 1.000] | 11.170 [-6.000, 50.250] |
| long_type_expedite | advance_1_cost_3 | 4.696 [0.000, 18.000] | 3.741 [-2.000, 16.750] | -0.955 [-2.750, 1.000] | 0.536 [0.000, 1.000] | 8.491 [-11.000, 48.000] |
| backup_price_parity | price_1 | 4.696 [0.000, 18.000] | 3.723 [-2.000, 16.750] | -0.973 [-2.750, 0.000] | 0.554 [0.000, 1.000] | 8.402 [-11.000, 48.000] |
| net_shortage_value | h1 | 0.000 [0.000, 0.000] | -0.071 [-1.000, 0.000] | -0.071 [-1.000, 0.000] | 0.554 [0.000, 1.000] | -2.982 [-8.000, 0.000] |
| net_shortage_value | h3 | 0.857 [0.000, 18.000] | 0.362 [-2.000, 13.250] | -0.496 [-4.750, 0.000] | 0.554 [0.000, 1.000] | -1.683 [-11.000, 34.750] |
| net_shortage_value | h10 | 4.071 [0.000, 18.000] | 3.165 [-2.000, 16.750] | -0.906 [-2.750, 0.000] | 0.554 [0.000, 1.000] | 6.728 [-11.000, 48.000] |

| Arm | Level | Buyer harm | Contractual | Belief | Interaction | Truthful qualifiers | G0 agreement |
|---|---|---:|---:|---:|---:|---:|---:|
| baseline | h20 | 13.215 [0.000, 39.925] | 0.000 [0.000, 0.000] | 10.794 [0.000, 39.925] | 2.421 [-0.000, 38.000] | 32 | 48/56 |
| backlog_catchup | catchup_0.5_cost_1 | 3.116 [0.000, 25.200] | 0.000 [0.000, 0.000] | 2.161 [0.000, 23.600] | 0.955 [0.000, 25.200] | 37 | 43/56 |
| broker_spot | capacity_0.5_price_6 | 7.107 [0.000, 28.000] | 0.000 [0.000, 0.000] | 5.001 [-0.000, 24.925] | 2.106 [0.000, 23.000] | 27 | 43/56 |
| positive_contradiction_fee | F3 | 13.215 [0.000, 39.925] | 0.000 [0.000, 0.000] | 10.794 [0.000, 39.925] | 2.421 [-0.000, 38.000] | 32 | 48/56 |
| no_damages | lambda0 | 13.215 [0.000, 39.925] | 0.000 [0.000, 0.000] | 10.794 [0.000, 39.925] | 2.421 [-0.000, 38.000] | 32 | 48/56 |
| long_type_expedite | advance_1_cost_3 | 12.858 [-19.988, 39.925] | 0.000 [0.000, 0.000] | 10.794 [0.000, 39.925] | 2.064 [-19.988, 38.000] | 32 | 48/56 |
| backup_price_parity | price_1 | 14.389 [0.000, 39.925] | 0.000 [0.000, 0.000] | 11.772 [0.000, 39.925] | 2.617 [0.000, 39.000] | 32 | 48/56 |
| net_shortage_value | h1 | 0.043 [0.000, 1.000] | 0.000 [0.000, 0.000] | 0.028 [0.000, 1.000] | 0.015 [0.000, 0.200] | 1 | 56/56 |
| net_shortage_value | h3 | 0.765 [0.000, 5.925] | 0.000 [0.000, 0.000] | 0.679 [0.000, 5.925] | 0.086 [0.000, 4.000] | 14 | 52/56 |
| net_shortage_value | h10 | 5.272 [0.000, 19.925] | 0.000 [0.000, 0.000] | 4.434 [0.000, 19.925] | 0.838 [0.000, 18.000] | 30 | 49/56 |

| Arm | Level | Critical-penalty category counts | Mean terminal backlog C00 / C11 |
|---|---|---|---:|
| baseline | h20 | finite_nonnegative: 16, invariant_positive: 8, invariant_zero: 17, no_positive_range: 15 | 0.000 / 0.000 |
| backlog_catchup | catchup_0.5_cost_1 | finite_nonnegative: 10, invariant_negative: 3, invariant_positive: 5, invariant_zero: 17, no_positive_range: 21 | 0.964 / 1.179 |
| broker_spot | capacity_0.5_price_6 | finite_nonnegative: 16, invariant_positive: 3, invariant_zero: 22, no_positive_range: 15 | 0.000 / 0.000 |
| positive_contradiction_fee | F3 | finite_nonnegative: 16, invariant_negative: 17, invariant_positive: 8, no_positive_range: 15 | 0.000 / 0.000 |
| no_damages | lambda0 | undefined_no_damages: 56 | 0.000 / 0.000 |
| long_type_expedite | advance_1_cost_3 | undefined_option_may_reselect: 56 | 0.000 / 0.000 |
| backup_price_parity | price_1 | finite_nonnegative: 16, invariant_positive: 8, invariant_zero: 17, no_positive_range: 15 | 0.000 / 0.000 |
| net_shortage_value | h1 | invariant_zero: 25, no_positive_range: 31 | 0.000 / 0.000 |
| net_shortage_value | h3 | finite_nonnegative: 6, invariant_positive: 1, invariant_zero: 24, no_positive_range: 25 | 0.000 / 0.000 |
| net_shortage_value | h10 | finite_nonnegative: 14, invariant_positive: 8, invariant_zero: 17, no_positive_range: 17 | 0.000 / 0.000 |

## G0 single-buyer comparator at h=1, 3, 10 and 20

The [224-row per-case table](results/v3_design_sensitivity_h_window_verified.csv) gives the G0 prediction, the network planner's truthful qualification, agreement and each failed assumption. G0 assumes one demand stream at the buyer, direct fungible backup, no spot or ready alternate supply, and no network routing or assembly. It is a benchmark, not a theorem for multi-node networks.

| h | Agreement | R1 disagreements | R2 | R3 | R4 | Total |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 56/56 | 0 | 0 | 0 | 0 | 0 |
| 3 | 52/56 | 1 | 0 | 0 | 3 | 4 |
| 10 | 49/56 | 0 | 0 | 0 | 7 | 7 |
| 20 | 48/56 | 0 | 0 | 0 | 8 | 8 |

All **19** h-level disagreements are listed below. In every one, G0 predicts qualification and the network planner does not. **Every disagreement has at least one failed single-buyer assumption; none falls inside the G0 model's stated scope.**

| h | Regime | Case ID | Failed single-buyer assumption(s) |
|---:|---|---|---|
| 3 | R1 | `v3-c0-r1-interior-layered_dual-small-1` | one demand site (violated: buyer/retail demand split); direct flow (violated: transport lanes); buyer-only demand-site stock (violated: retail stock) |
| 3 | R4 | `v3-c0-r4-0` | no spot source (violated: buyer spot cap 1); no ready alternate source (violated: stocked ready nodes) |
| 3 | R4 | `v3-c0-r4-1` | no spot source (violated: buyer spot cap 1); no ready alternate source (violated: stocked ready nodes); direct critical-part demand (violated: assembly recipe) |
| 3 | R4 | `v3-c0-r4-3` | no spot source (violated: buyer spot cap 1); no ready alternate source (violated: stocked ready nodes); one demand site (violated: buyer/retail demand split) |
| 10 | R4 | `v3-c0-r4-0` | no spot source (violated: buyer spot cap 1); no ready alternate source (violated: stocked ready nodes) |
| 10 | R4 | `v3-c0-r4-1` | no spot source (violated: buyer spot cap 1); no ready alternate source (violated: stocked ready nodes); direct critical-part demand (violated: assembly recipe) |
| 10 | R4 | `v3-c0-r4-2` | no spot source (violated: buyer spot cap 1); no ready alternate source (violated: stocked ready nodes); direct critical-part demand (violated: assembly recipe) |
| 10 | R4 | `v3-c0-r4-3` | no spot source (violated: buyer spot cap 1); no ready alternate source (violated: stocked ready nodes); one demand site (violated: buyer/retail demand split) |
| 10 | R4 | `v3-c0-r4-5` | no spot source (violated: buyer spot cap 1); no ready alternate source (violated: stocked ready nodes); one demand site (violated: buyer/retail demand split) |
| 10 | R4 | `v3-c0-r4-6` | no spot source (violated: buyer spot cap 1); no ready alternate source (violated: stocked ready nodes); one demand site (violated: buyer/retail demand split) |
| 10 | R4 | `v3-c0-r4-7` | no spot source (violated: buyer spot cap 1); no ready alternate source (violated: stocked ready nodes); one demand site (violated: buyer/retail demand split) |
| 20 | R4 | `v3-c0-r4-0` | no spot source (violated: buyer spot cap 1); no ready alternate source (violated: stocked ready nodes) |
| 20 | R4 | `v3-c0-r4-1` | no spot source (violated: buyer spot cap 1); no ready alternate source (violated: stocked ready nodes); direct critical-part demand (violated: assembly recipe) |
| 20 | R4 | `v3-c0-r4-2` | no spot source (violated: buyer spot cap 1); no ready alternate source (violated: stocked ready nodes); direct critical-part demand (violated: assembly recipe) |
| 20 | R4 | `v3-c0-r4-3` | no spot source (violated: buyer spot cap 1); no ready alternate source (violated: stocked ready nodes); one demand site (violated: buyer/retail demand split) |
| 20 | R4 | `v3-c0-r4-4` | no spot source (violated: buyer spot cap 1); no ready alternate source (violated: stocked ready nodes); one demand site (violated: buyer/retail demand split) |
| 20 | R4 | `v3-c0-r4-5` | no spot source (violated: buyer spot cap 1); no ready alternate source (violated: stocked ready nodes); one demand site (violated: buyer/retail demand split) |
| 20 | R4 | `v3-c0-r4-6` | no spot source (violated: buyer spot cap 1); no ready alternate source (violated: stocked ready nodes); one demand site (violated: buyer/retail demand split) |
| 20 | R4 | `v3-c0-r4-7` | no spot source (violated: buyer spot cap 1); no ready alternate source (violated: stocked ready nodes); one demand site (violated: buyer/retail demand split) |

Cases with a disagreement and **no** failed assumption: **none**. These flags identify departures from G0; they do not isolate which departure caused an individual planner decision. The R1 layered-dual case splits demand between buyer and retail, routes critical units over capacity-limited lanes, and holds retail stock. Every R4 disagreement has an immediate spot source and ready alternate stock; assembly or split demand adds further departures where listed.

## Expedite choice

The independent verifier recomputed all 224 option choices. [The full choice table](results/v3_design_sensitivity_expedite_verified.csv) lists both option payoffs and the fixed 1e-6 tie rule.

| Cell | Expedited | Not expedited |
|---|---:|---:|
| C00 | 0 | 56 |
| C10 | 0 | 56 |
| C01 | 0 | 56 |
| C11 | 1 | 55 |
The sole selected option is `v3-c0-r2-1` in C11, with a net supplier-payoff advantage of 5.

The earlier R1 and R2 verifier stops were numeric-type serialization differences. The complete read-only audit found no value difference. R3 normalizes only integer/float ledger leaves to exact rational values; it completed the independent replay and report comparison without altering the saved result.
