# Exploratory backup nonuse by cached demand path

The first-demand buffer cover is shown as units / periods at each demand site and product. Alternate shipments are observed cached shipments. The q0 ceiling is restricted to cached spot and backlog line items from backup arrival at the primary site; it is not a counterfactual. All three scenarios have a three-period outage and two overlapping demand periods.

| Network | Substitute | Demand multiplier | First-demand buffer cover | Alternate shipped | Spot units / cost | Backlog unit-periods / charge | q0 U / M / U−M | Certified dates of 8 | Causal reason |
|---|---|---:|---|---:|---|---|---|---:|---|
| serial-small-0 | sole | 0.80 | stage2/unit:0.00/0.00 | 0.00 | 1.60 / 9.60 | 0.00 / 0.00 | 9.60 / 18.00 / -8.40 | 2 | unresolved |
| serial-small-0 | sole | 1.00 | stage2/unit:0.00/0.00 | 0.00 | 2.00 / 12.00 | 0.00 / 0.00 | 12.00 / 18.00 / -6.00 | 2 | unresolved |
| serial-small-0 | sole | 1.20 | stage2/unit:0.00/0.00 | 0.00 | 2.80 / 16.80 | 2.20 / 44.00 | 60.80 / 18.00 / 42.80 | 2 | unresolved |
| serial-small-1 | sole | 0.80 | stage2/unit:2.00/2.50 | 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| serial-small-1 | sole | 1.00 | stage2/unit:2.00/2.00 | 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| serial-small-1 | sole | 1.20 | stage2/unit:2.00/1.67 | 0.00 | 1.00 / 6.00 | 1.00 / 20.00 | 26.00 / 18.00 / 8.00 | 6 | unresolved |
| serial-small-2 | sole | 0.80 | stage2/unit:4.00/5.00 | 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| serial-small-2 | sole | 1.00 | stage2/unit:4.00/4.00 | 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| serial-small-2 | sole | 1.20 | stage2/unit:4.00/3.33 | 0.00 | 0.60 / 3.60 | 0.00 / 0.00 | 3.60 / 18.00 / -14.40 | 8 | all dates excluded |
| serial-large-0 | sole | 0.80 | stage4/unit:4.00/5.00 | 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| serial-large-0 | sole | 1.00 | stage4/unit:4.00/4.00 | 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| serial-large-0 | sole | 1.20 | stage4/unit:4.00/3.33 | 0.00 | 0.20 / 1.20 | 0.00 / 0.00 | 1.20 / 18.00 / -16.80 | 8 | all dates excluded |
| serial-large-1 | sole | 0.80 | stage4/unit:0.00/0.00 | 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| serial-large-1 | sole | 1.00 | stage4/unit:0.00/0.00 | 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| serial-large-1 | sole | 1.20 | stage4/unit:0.00/0.00 | 0.00 | 0.00 / 0.00 | 2.00 / 40.00 | 40.00 / 18.00 / 22.00 | 8 | all dates excluded |
| assembly-small-0 | sole | 0.80 | stage2/finished:2.00/2.50 | 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| assembly-small-0 | sole | 1.00 | stage2/finished:2.00/2.00 | 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| assembly-small-0 | sole | 1.20 | stage2/finished:2.00/1.67 | 0.00 | 1.00 / 6.00 | 1.00 / 20.00 | 26.00 / 18.00 / 8.00 | 6 | unresolved |
| assembly-small-1 | sole | 0.80 | stage2/finished:0.00/0.00 | 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| assembly-small-1 | sole | 1.00 | stage2/finished:0.00/0.00 | 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| assembly-small-1 | sole | 1.20 | stage2/finished:0.00/0.00 | 0.00 | 1.20 / 7.20 | 0.00 / 0.00 | 7.20 / 18.00 / -10.80 | 6 | unresolved |
| assembly-large-0 | dual | 0.80 | stage4/finished:2.00/2.50 | 1.40 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| assembly-large-0 | dual | 1.00 | stage4/finished:2.00/2.00 | 2.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| assembly-large-0 | dual | 1.20 | stage4/finished:2.00/1.67 | 2.60 | 0.00 / 0.00 | 0.60 / 12.00 | 12.00 / 18.00 / -6.00 | 8 | all dates excluded |
| assembly-large-1 | sole | 0.80 | stage4/finished:4.00/5.00 | 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| assembly-large-1 | sole | 1.00 | stage4/finished:4.00/4.00 | 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| assembly-large-1 | sole | 1.20 | stage4/finished:4.00/3.33 | 0.00 | 0.40 / 2.40 | 0.40 / 8.00 | 10.40 / 18.00 / -7.60 | 8 | all dates excluded |
| assembly-large-2 | multi | 0.80 | stage4/finished:0.00/0.00 | 3.40 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| assembly-large-2 | multi | 1.00 | stage4/finished:0.00/0.00 | 4.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| assembly-large-2 | multi | 1.20 | stage4/finished:0.00/0.00 | 4.00 | 0.80 / 4.80 | 0.00 / 0.00 | 4.80 / 18.00 / -13.20 | 8 | all dates excluded |
| distribution-small-0 | dual | 0.80 | other_store/unit:0.00/0.00, stage2/unit:1.50/2.50 | 4.50 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| distribution-small-0 | dual | 1.00 | other_store/unit:0.00/0.00, stage2/unit:1.50/2.00 | 4.50 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| distribution-small-0 | dual | 1.20 | other_store/unit:0.00/0.00, stage2/unit:1.50/1.67 | 4.50 | 0.75 / 4.50 | 0.25 / 5.00 | 9.50 / 18.00 / -8.50 | 8 | all dates excluded |
| distribution-small-1 | sole | 0.80 | other_store/unit:0.00/0.00, stage2/unit:2.00/3.33 | 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| distribution-small-1 | sole | 1.00 | other_store/unit:0.00/0.00, stage2/unit:2.00/2.67 | 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| distribution-small-1 | sole | 1.20 | other_store/unit:0.00/0.00, stage2/unit:2.00/2.22 | 0.00 | 0.55 / 3.30 | 1.95 / 39.00 | 42.30 / 18.00 / 24.30 | 7 | unresolved |
| distribution-large-0 | dual | 0.80 | other_store/unit:0.00/0.00, stage4/unit:4.00/6.67 | 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| distribution-large-0 | dual | 1.00 | other_store/unit:0.00/0.00, stage4/unit:4.00/5.33 | 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| distribution-large-0 | dual | 1.20 | other_store/unit:0.00/0.00, stage4/unit:4.00/4.44 | 0.30 | 0.00 / 0.00 | 0.30 / 6.00 | 6.00 / 18.00 / -12.00 | 8 | all dates excluded |
| distribution-large-1 | dual | 0.80 | other_store/unit:0.00/0.00, stage4/unit:2.00/3.33 | 1.20 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| distribution-large-1 | dual | 1.00 | other_store/unit:0.00/0.00, stage4/unit:2.00/2.67 | 1.75 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| distribution-large-1 | dual | 1.20 | other_store/unit:0.00/0.00, stage4/unit:2.00/2.22 | 1.75 | 0.35 / 2.10 | 0.15 / 3.00 | 5.10 / 18.00 / -12.90 | 8 | all dates excluded |
| layered_dual-small-0 | multi | 0.80 | stage2/unit:4.00/5.00 | 1.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| layered_dual-small-0 | multi | 1.00 | stage2/unit:4.00/4.00 | 2.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| layered_dual-small-0 | multi | 1.20 | stage2/unit:4.00/3.33 | 3.00 | 0.00 / 0.00 | 0.60 / 12.00 | 12.00 / 18.00 / -6.00 | 8 | all dates excluded |
| layered_dual-small-1 | dual | 0.80 | stage2/unit:0.00/0.00 | 5.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| layered_dual-small-1 | dual | 1.00 | stage2/unit:0.00/0.00 | 6.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| layered_dual-small-1 | dual | 1.20 | stage2/unit:0.00/0.00 | 7.00 | 0.00 / 0.00 | 1.20 / 24.00 | 24.00 / 18.00 / 6.00 | 8 | all dates excluded |
| layered_dual-small-2 | sole | 0.80 | stage2/unit:2.00/2.50 | 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| layered_dual-small-2 | sole | 1.00 | stage2/unit:2.00/2.00 | 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| layered_dual-small-2 | sole | 1.20 | stage2/unit:2.00/1.67 | 0.00 | 1.20 / 7.20 | 0.00 / 0.00 | 7.20 / 18.00 / -10.80 | 7 | unresolved |
| layered_dual-large-0 | multi | 0.80 | stage4/unit:2.00/2.50 | 1.40 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| layered_dual-large-0 | multi | 1.00 | stage4/unit:2.00/2.00 | 2.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| layered_dual-large-0 | multi | 1.20 | stage4/unit:2.00/1.67 | 2.20 | 0.60 / 3.60 | 0.00 / 0.00 | 3.60 / 18.00 / -14.40 | 8 | all dates excluded |
| layered_dual-large-1 | dual | 0.80 | stage4/unit:4.00/5.00 | 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| layered_dual-large-1 | dual | 1.00 | stage4/unit:4.00/4.00 | 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| layered_dual-large-1 | dual | 1.20 | stage4/unit:4.00/3.33 | 0.50 | 0.00 / 0.00 | 0.30 / 6.00 | 6.00 / 18.00 / -12.00 | 8 | all dates excluded |
| planted-small-0 | multi | 0.80 | stage2/unit:0.00/0.00 | 5.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| planted-small-0 | multi | 1.00 | stage2/unit:0.00/0.00 | 6.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| planted-small-0 | multi | 1.20 | stage2/unit:0.00/0.00 | 7.00 | 0.00 / 0.00 | 1.20 / 24.00 | 24.00 / 18.00 / 6.00 | 8 | all dates excluded |
| planted-small-1 | multi | 0.80 | stage2/unit:4.00/5.00 | 1.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| planted-small-1 | multi | 1.00 | stage2/unit:4.00/4.00 | 2.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| planted-small-1 | multi | 1.20 | stage2/unit:4.00/3.33 | 2.60 | 0.60 / 3.60 | 0.00 / 0.00 | 3.60 / 18.00 / -14.40 | 8 | all dates excluded |
| planted-large-0 | sole | 0.80 | stage4/unit:1.00/1.25 | 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| planted-large-0 | sole | 1.00 | stage4/unit:1.00/1.00 | 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| planted-large-0 | sole | 1.20 | stage4/unit:1.00/0.83 | 0.00 | 0.00 / 0.00 | 1.80 / 36.00 | 36.00 / 18.00 / 18.00 | 8 | all dates excluded |
| planted-large-1 | multi | 0.80 | stage4/unit:2.00/2.50 | 1.40 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| planted-large-1 | multi | 1.00 | stage4/unit:2.00/2.00 | 2.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| planted-large-1 | multi | 1.20 | stage4/unit:2.00/1.67 | 2.60 | 0.00 / 0.00 | 0.60 / 12.00 | 12.00 / 18.00 / -6.00 | 8 | all dates excluded |
| planted-large-2 | dual | 0.80 | stage4/unit:4.00/5.00 | 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| planted-large-2 | dual | 1.00 | stage4/unit:4.00/4.00 | 0.00 | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 18.00 / -18.00 | 8 | all dates excluded |
| planted-large-2 | dual | 1.20 | stage4/unit:4.00/3.33 | 0.60 | 0.20 / 1.20 | 0.00 / 0.00 | 1.20 / 18.00 / -16.80 | 8 | all dates excluded |
