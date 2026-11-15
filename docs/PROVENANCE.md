# Research protocol and provenance

The study concerns a single reporting supplier and a buyer using a declared rolling mixed-integer planner. A structured notice controls order acceptance; its wording determines the receiver's initial belief. Capacity revelation corrects future beliefs and order acceptance, while previously accepted obligations remain enforceable. The primary network model uses lost sales, finite or through-horizon backup commitments, and a fixed demand multiplier of 1.0. Compensation is excluded from the main buyer objective.

The analytical benchmark identifies the conditions for complete backup suppression and separately evaluates the supplier's gain from additional primary deliveries net of shortfall liability. The network study uses serial, assembly, distribution, layered and bypass-route families. The final selected public design contains 56 cases: 24 window networks, eight boundary controls and eight cases in each of three comparison regimes. The complete public-generation manifest records three rotations, or 168 generated cases. The supplied public case inputs comprise the selected 56-case design.

## Preregistered design and acceptance criteria

Acceptance proceeded through exact analytical comparisons, physical and planner checks, public resource and stability checks, truthful-only calibration, a selected-design freeze, structural holdout generation, public metadata verification, design-only precision planning and confirmation. The calibration screen selected the first rotation satisfying the fixed qualification floor in the window networks; all qualifying cases were permitted. Comparison regimes and controls were reported without affecting rotation selection.

The confirmation population retained the allocated factors while varying network structure within prespecified ranges. All 24 confirmation window networks entered the primary analysis regardless of truthful qualification. An all-case content, allocation and range audit preceded planning. The primary joint rule required positive lower confidence bounds for mean displaced backup and mean additional primary deliveries. Family-weighted network resampling and a prespecified sign-flip sensitivity were used. The secondary family was gated on the primary result and controlled its three contrasts with the declared multiplicity adjustment. Public design outcomes informed precision reporting, without changing the confirmation hypotheses or sample size.

The declared final planner uses one cost-minimizing MIP per period, with the fixed 1e-3 tie term, dominant current qualification penalty and fixed irrational shipment weights. Presolve is off in the declared configuration. Stability required exact binary agreement, continuous-action agreement within 1e-6, and the cumulative accuracy budget of 1e-3. Hand-off projection is limited to 2e-7. Presolve-on performance is a sensitivity, with solver faults recorded separately. The original source and its numerical conventions are unchanged in this release.

## Deviations and interpretation

The v1 network experiments provided pilot evidence. The associated sealed set was retired unopened. The v2 calibration ended without a selected candidate following numerical and resource stops; the incomplete candidate was not assessed. These pilots are not independent confirmations of v3.

Before final v3 confirmation, numerical revisions addressed finite-commitment state transfer, solver-scale discrepancies, tie objectives and action canonicalization. An early numerical-method revision and rerun preceded examination of the revised method; later numerical revisions were examined before evaluation. Failed and incomplete work was distinguished from completed acceptance checks. The final single-solve convention replaced the earlier secondary-solve method. These changes were not selected using v3 strategic outcomes.

Two design-result verification stops arose from representation comparisons of numerically equal ledgers. Canonical tuple/list comparison and exact integer/float normalization resolved those verification discrepancies without changing the saved quantities. The archive contains versioned verifier sources and results that distinguish stopped runs from completed checks.

The original financial score used margin 3 at purchase price 1. Post-confirmation repricing restricted ordinary margin to the admissible interval [0,1], retaining the original score as historical reporting. This repricing changed no physical action, delivery, buyer cost or confirmatory delivery decision. Later discounted-belief, paired-commitment, ex ante contract and discount analyses were descriptive extensions on public evidence, without new confirmatory tests. Reporting likelihoods were specified rather than estimated in equilibrium.

Historical objective-recomputation warnings in the discounted-belief experiment remain unresolved. A subsequent check reproduced sampled public actions and found the sampled objective calculations consistent within their numerical bounds; it did not retrospectively certify every historical recomputation. Saved action/ledger verification and that limited diagnostic are distinct forms of evidence.

The held-out results test structural robustness at fixed allocated factors under the declared planner. Backup displacement largely follows the allocated commitment and timing factors; additional primary deliveries provide the informative structural comparison. The study does not estimate the prevalence of misreporting in practice or establish a network reporting equilibrium.

## Distributed evidence and integrity

The package contains final public results, public case inputs, public confirmation metadata, scientific source and prepared manuscript files. `RELEASE_ASSETS.txt` lists the paths, sizes and hashes of the scientific evidence assets.

`PACKAGE_MANIFEST.txt` gives SHA-256 values for all payload files. `RESULT_HASH_BINDINGS.json` lists result hashes and their source-field locations in included scientific manifests and verification records, and identifies hash-only evidence covered by the package manifest. The release verifier checks these files and reports 56 public case files and 112 further entries in the complete design manifest.

The manuscript and supplement retain their text and numbers, and the bibliography supplies the citations. Methodological details appear in the manuscript, research code and this protocol summary.

### Final result-file hashes

| Included result file | SHA-256 |
|---|---|
| `paper/ex_ante_contract_r1/study_execution_r3/result.json` | `46b35f86ea4a8dbe962dd0ab53580d99d512df4f0f9e1c16998c16c75a5d5bdb` |
| `paper/followup_diagnostic_r2/execution/result.json` | `48b012a9e59213182346a59161113f45581e184df28814b74c7c464688dfa133` |
| `paper/paired_commitment_r1/study_execution/result.json` | `b913b12faef2f9e540d91fe8dbc0b009e6a46983f1bd28d89cb83c3d2fe9dadb` |
| `results/v2_truthful_calibration_last/result.json` | `e49367546b027faf73d94cc7e22256ddd91fa8dfb3d540ff5051c5ab4558c6a2` |
| `results/v3_design_false_notice_r2/result.json` | `bb0a2208ca1668198cdf09ff091d8cdf37fcbfbe0ec9baf8f5897cc0f51ed224` |
| `results/v3_design_sensitivities_r1/result.json` | `99c1b064b04e7cc65a97620b8f94d44ff6a27ff3eeb0841f7ac9a62497613763` |
| `results/v3_discounted_belief_study_r1/result.json` | `1b262e14552a24ab4fcb808a3cbc1d33f6f48423b7e178665940addb94f912f1` |
| `results/v3_g0/acceptance.json` | `3b4d9a042f301ad8c1f0fa7d253f76945c0ef18f551ae0e6eaa4c3240beca615` |
| `results/v3_preopening_robustness/result.json` | `4e7965966aa2e034784a053af72bde3cde51cd7c98b5aedc9b398852980f0630` |
| `results/v3_single_full_r4/stability.json` | `f24ea6ae32f8df6d2f14823af7068309b21e89b4268eef8159ca55778d42045b` |
| `results/v3_truthful_calibration_r4/result.json` | `97467f3c0a8d2f3816bcd28154b22c2ec7bd68587dd0aa3c5e599efa8f879878` |
