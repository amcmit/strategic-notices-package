# V2 truthful qualifier patterns: descriptive pilot table

Date: 12 November 2026. This is a read-only tabulation of the preserved
[last stopped calibration result](../results/v2_truthful_calibration_last/result.json)
(SHA-256 `e49367546b027faf73d94cc7e22256ddd91fa8dfb3d540ff5051c5ab4558c6a2`)
against the frozen [public v2 design manifest](../configs/v2-design/manifest.json).
No planner solve, new trajectory, private case, or formal candidate selection
enters this table. Candidate 0 and 1 have 24 complete truthful long/major and
short/minor pairs each. Candidate 2 has **14 complete pairs of 24**; its next
long path stopped after period 4 and is excluded. Its observed rate cannot
be extrapolated to the remaining ten slots.

A **strict qualifier** has an executed `strict_qualify` action and positive
mandatory backup delivery during the long outage, following the frozen
calibration condition. A **skipped-tie network** has at least one
`primary_tie` period, resolved toward skipping qualification then. A network
can have a skipped tie and qualify strictly later. There are **nine** strict
qualifier candidate-slot observations and **seven** skipped-tie observations;
six overlap, leaving **ten distinct** rows below. There are 62 completed
long/major candidate-slot observations, comprising 24 + 24 + 14; these reuse
24 structural design slots across rotated factor allocations and are not 62
independent networks. No completed short/minor path has a strict
qualification or skipped tie.

`Sub.` is structural substitutability: 1 = sole, 2 = dual, 3 = multi.
Alternate stock is per eligible source; `—` means there is no eligible
alternative. `H` means backup delivery through the horizon. Backup units are
observed mandatory units **during the long outage**, not an estimated
counterfactual. Qualification and skipped-tie dates are period indices.

| Candidate | Public slot | Family | Size | Outage | Commitment | Main buffer | Sub. | Alternate stock | Strict dates | Skipped-tie dates | Outage backup units |
|---:|---|---|---|---:|---|---:|---:|---:|---|---|---:|
| 0 | `serial-small-0` | serial | small | 3 | 2 | 0 | 1 | — | 2 | 0, 1 | 2 |
| 0 | `serial-small-2` | serial | small | 5 | H | 1 | 1 | — | — | 5 | 0 |
| 0 | `distribution-small-1` | distribution | small | 5 | H | 1 | 1 | — | 3 | — | 3 |
| 1 | `serial-small-1` | serial | small | 5 | H | 1 | 1 | — | 4 | — | 2 |
| 1 | `distribution-small-1` | distribution | small | 3 | 2 | 0 | 1 | — | 2 | 0, 1 | 2 |
| 1 | `layered_dual-small-2` | layered_dual | small | 5 | 4 | 0 | 1 | — | 2 | 0, 1 | 4 |
| 2* | `serial-small-0` | serial | small | 5 | H | 1 | 1 | — | 3 | — | 3 |
| 2* | `serial-small-1` | serial | small | 3 | 2 | 0 | 1 | — | 2 | 0, 1 | 2 |
| 2* | `assembly-small-0` | assembly | small | 4 | 2 | 0 | 1 | — | 3 | 0, 1, 2 | 2 |
| 2* | `assembly-small-1` | assembly | small | 5 | 4 | 0 | 1 | — | 2 | 0, 1 | 4 |

*Candidate 2 is incomplete and unassessed.* The `layered_dual` family name
does not imply structural substitutability in every slot: this qualifying
slot has `Sub.=1` in the public manifest.

## Completed-network counts by factor

These counts classify each completed **long/major candidate-slot** exactly
once. `Strict only` and `Strict + tie` both count toward the nine strict
qualifiers. `Tie only` and `Neither` are the **53 non-qualifying** completed
observations. The `Tie only` row is candidate 0 `serial-small-2`.

| Factor | Level | Strict only | Strict + tie | Tie only | Neither | Total |
|---|---|---:|---:|---:|---:|---:|
| Long outage | 3 | 0 | 3 | 0 | 18 | 21 |
| Long outage | 4 | 0 | 1 | 0 | 19 | 20 |
| Long outage | 5 | 3 | 2 | 1 | 15 | 21 |
| Backup commitment | 2 | 0 | 4 | 0 | 17 | 21 |
| Backup commitment | 4 | 0 | 2 | 0 | 19 | 21 |
| Backup commitment | H | 3 | 0 | 1 | 16 | 20 |
| Main buffer | 0 | 0 | 6 | 0 | 14 | 20 |
| Main buffer | 1 | 3 | 0 | 1 | 17 | 21 |
| Main buffer | 3 | 0 | 0 | 0 | 21 | 21 |
| Substitutability | sole (1) | 3 | 6 | 1 | 21 | 31 |
| Substitutability | dual (2) | 0 | 0 | 0 | 18 | 18 |
| Substitutability | multi (3) | 0 | 0 | 0 | 13 | 13 |
| Alternate stock/source | ineligible (sole) | 3 | 6 | 1 | 21 | 31 |
| Alternate stock/source | 0 | 0 | 0 | 0 | 11 | 11 |
| Alternate stock/source | 2 | 0 | 0 | 0 | 10 | 10 |
| Alternate stock/source | 20 | 0 | 0 | 0 | 10 | 10 |
| Family | serial | 2 | 2 | 1 | 10 | 15 |
| Family | assembly | 0 | 2 | 0 | 13 | 15 |
| Family | distribution | 1 | 1 | 0 | 10 | 12 |
| Family | layered_dual | 0 | 1 | 0 | 9 | 10 |
| Family | planted | 0 | 0 | 0 | 10 | 10 |
| Size | small | 3 | 6 | 1 | 21 | 31 |
| Size | large | 0 | 0 | 0 | 31 | 31 |

| Candidate | Completed long/major pairs | Strict only | Strict + tie | Tie only | Neither | Strict total | Tie total |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 24/24 | 1 | 1 | 1 | 21 | 2 | 2 |
| 1 | 24/24 | 1 | 2 | 0 | 21 | 3 | 2 |
| 2* | 14/24 | 1 | 3 | 0 | 10 | 4 | 3 |

All nine observed strict qualifiers are small and sole-source. The 31
completed dual/multi observations and 31 completed large observations have
no strict qualifier. Factor levels rotate across the same public slots and
the candidate-2 prefix is ordered rather than a random half-sample, so
these are descriptive associations, not causal effects or confirmation
estimates. Candidate 0 and 1 miss both the 8-of-24 strict-backup floor and
the structural-coverage condition on complete data. Candidate 2 has no
formal assessment or selected status. The stopped campaign remains closed.
