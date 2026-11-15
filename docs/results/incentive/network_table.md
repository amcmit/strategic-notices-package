# Per-network exploratory incentive diagnostic

Long type: false minor/belief 0 minus truthful major/belief 1. Demand scenarios are weighted within network.
Critical penalties use margin 3. `—` means no nonnegative critical penalty; a negative algebraic root is shown only as a diagnostic.
Order columns count a difference in at least one paired demand scenario. `J` is the joint notice-and-belief comparison; `F` holds belief at 1.

| Network | Δ primary delivered | Δ shortfall | Δ backup | Root class | Critical κ≥0 | Algebraic root | Gain κ=0 | Gain κ=5 | J primary order | J spot order | J accepted | F any order |
|---|---:|---:|---:|---|---:|---:|---:|---:|:---:|:---:|:---:|:---:|
| serial-small-0 | 0 | 1.0000 | 0 | zero_endpoint_only | 0 | 0 | 0 | -5.0000 | Y |  | Y |  |
| serial-small-1 | -0.8667 | 0 | 0 | constant_negative | — | — | -2.6000 | -2.6000 | Y | Y | Y |  |
| serial-small-2 | 0 | 0 | 0 | identically_zero | — | — | 0 | 0 |  |  |  |  |
| serial-large-0 | 0 | 0 | 0 | identically_zero | — | — | 0 | 0 |  |  |  |  |
| serial-large-1 | 0 | 0 | 0 | identically_zero | — | — | 0 | 0 |  |  |  |  |
| assembly-small-0 | -0.8667 | 0 | 0 | constant_negative | — | — | -2.6000 | -2.6000 | Y | Y | Y |  |
| assembly-small-1 | -1.8667 | 1.0000 | 0 | negative_root_outside_domain | — | -5.6000 | -5.6000 | -10.6000 | Y | Y | Y |  |
| assembly-large-0 | 0 | 0 | 0 | identically_zero | — | — | 0 | 0 |  |  |  |  |
| assembly-large-1 | 0 | 0 | 0 | identically_zero | — | — | 0 | 0 |  |  |  |  |
| assembly-large-2 | 0 | 0 | 0 | identically_zero | — | — | 0 | 0 |  |  |  |  |
| distribution-small-0 | 0 | 0 | 0 | identically_zero | — | — | 0 | 0 |  |  |  |  |
| distribution-small-1 | -1.8833 | 1.0000 | 0 | negative_root_outside_domain | — | -5.6500 | -5.6500 | -10.6500 | Y | Y | Y |  |
| distribution-large-0 | 0 | 0 | 0 | identically_zero | — | — | 0 | 0 |  |  |  |  |
| distribution-large-1 | 0 | 0 | 0 | identically_zero | — | — | 0 | 0 |  |  |  |  |
| layered_dual-small-0 | 0 | 0 | 0 | identically_zero | — | — | 0 | 0 |  |  |  |  |
| layered_dual-small-1 | 0 | 0 | 0 | identically_zero | — | — | 0 | 0 |  |  |  |  |
| layered_dual-small-2 | 0 | 0 | 0 | identically_zero | — | — | 0 | 0 |  |  |  |  |
| layered_dual-large-0 | 0 | 0 | 0 | identically_zero | — | — | 0 | 0 |  |  |  |  |
| layered_dual-large-1 | 0 | 0 | 0 | identically_zero | — | — | 0 | 0 |  |  |  |  |
| planted-small-0 | 0 | 0 | 0 | identically_zero | — | — | 0 | 0 |  |  |  |  |
| planted-small-1 | 0 | 0 | 0 | identically_zero | — | — | 0 | 0 |  |  |  |  |
| planted-large-0 | -0.5000 | 0.5000 | 0 | negative_root_outside_domain | — | -3.0000 | -1.5000 | -4.0000 | Y | Y | Y |  |
| planted-large-1 | 0 | 0 | 0 | identically_zero | — | — | 0 | 0 |  |  |  |  |
| planted-large-2 | 0 | 0 | 0 | identically_zero | — | — | 0 | 0 |  |  |  |  |
