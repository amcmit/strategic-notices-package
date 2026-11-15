# Exploratory backup nonuse by design network

All rows use backup unit cost 2, qualification lead 2, minimum 1 per active period, fee 2, spot unit cost 6 and capacity 2, backlog charge 20 per unit-period, long outage length 3, and two demand periods overlapping outage. The q0 mandatory payment is 18.
`U−M` is the cached post-arrival spot-plus-backlog line-item ceiling minus the mandatory backup payment; it is not a global benefit bound or a counterfactual. Values are listed for demand multipliers 0.8 / 1 / 1.2. `Certified paths` counts demand paths for which every qualification date is excluded by a structural or independent cost certificate.

| Network | Substitute | Topological lead to demand | Initial main buffer | Alternate nominal cap | q0 U−M (0.8 / 1 / 1.2) | Certified paths | Causal reason |
|---|---|---|---:|---:|---|---:|---|
| serial-small-0 | sole | stage2/unit:2 | 0.00 | 0.00 | -8.40 / -6.00 / 42.80 | 0 | unresolved on ≥1 path |
| serial-small-1 | sole | stage2/unit:1 | 2.00 | 0.00 | -18.00 / -18.00 / 8.00 | 2 | unresolved on ≥1 path |
| serial-small-2 | sole | stage2/unit:0 | 4.00 | 0.00 | -18.00 / -18.00 / -14.40 | 3 | all dates excluded |
| serial-large-0 | sole | stage4/unit:0 | 4.00 | 0.00 | -18.00 / -18.00 / -16.80 | 3 | all dates excluded |
| serial-large-1 | sole | stage4/unit:4 | 0.00 | 0.00 | -18.00 / -18.00 / 22.00 | 3 | all dates excluded |
| assembly-small-0 | sole | stage2/finished:1 | 2.00 | 0.00 | -18.00 / -18.00 / 8.00 | 2 | unresolved on ≥1 path |
| assembly-small-1 | sole | stage2/finished:0 | 0.00 | 0.00 | -18.00 / -18.00 / -10.80 | 2 | unresolved on ≥1 path |
| assembly-large-0 | dual | stage4/finished:4 | 2.00 | 1.50 | -18.00 / -18.00 / -6.00 | 3 | all dates excluded |
| assembly-large-1 | sole | stage4/finished:2 | 4.00 | 0.00 | -18.00 / -18.00 / -7.60 | 3 | all dates excluded |
| assembly-large-2 | multi | stage4/finished:0 | 0.00 | 2.00 | -18.00 / -18.00 / -13.20 | 3 | all dates excluded |
| distribution-small-0 | dual | other_store/unit:1, stage2/unit:0 | 0.00 | 0.50 | -18.00 / -18.00 / -8.50 | 3 | all dates excluded |
| distribution-small-1 | sole | other_store/unit:3, stage2/unit:2 | 2.00 | 0.00 | -18.00 / -18.00 / 24.30 | 2 | unresolved on ≥1 path |
| distribution-large-0 | dual | other_store/unit:3, stage4/unit:2 | 4.00 | 1.50 | -18.00 / -18.00 / -12.00 | 3 | all dates excluded |
| distribution-large-1 | dual | other_store/unit:1, stage4/unit:0 | 2.00 | 0.50 | -18.00 / -18.00 / -12.90 | 3 | all dates excluded |
| layered_dual-small-0 | multi | stage2/unit:2 | 4.00 | 2.00 | -18.00 / -18.00 / -6.00 | 3 | all dates excluded |
| layered_dual-small-1 | dual | stage2/unit:1 | 0.00 | 1.50 | -18.00 / -18.00 / 6.00 | 3 | all dates excluded |
| layered_dual-small-2 | sole | stage2/unit:0 | 2.00 | 0.00 | -18.00 / -18.00 / -10.80 | 2 | unresolved on ≥1 path |
| layered_dual-large-0 | multi | stage4/unit:0 | 2.00 | 1.00 | -18.00 / -18.00 / -14.40 | 3 | all dates excluded |
| layered_dual-large-1 | dual | stage4/unit:3 | 4.00 | 0.50 | -18.00 / -18.00 / -12.00 | 3 | all dates excluded |
| planted-small-0 | multi | stage2/unit:1 | 0.00 | 2.00 | -18.00 / -18.00 / 6.00 | 3 | all dates excluded |
| planted-small-1 | multi | stage2/unit:0 | 4.00 | 1.00 | -18.00 / -18.00 / -14.40 | 3 | all dates excluded |
| planted-large-0 | sole | stage4/unit:3 | 0.00 | 0.00 | -18.00 / -18.00 / 18.00 | 3 | all dates excluded |
| planted-large-1 | multi | stage4/unit:2 | 2.00 | 2.00 | -18.00 / -18.00 / -6.00 | 3 | all dates excluded |
| planted-large-2 | dual | stage4/unit:0 | 4.00 | 1.50 | -18.00 / -18.00 / -16.80 | 3 | all dates excluded |
