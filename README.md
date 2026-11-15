# Strategic recovery notices

This study examines when understating a supply disruption suppresses backup qualification, benefits the reporting supplier, and harms the buyer. A single-buyer analysis separates the qualification window from the supplier-profit condition. Product-aware network models then trace the effects of orders, inventories, transport and mandatory backup commitments.

The release preserves the declared rolling planner, public design cases, saved public evidence, analysis code, and prepared manuscript materials. Design experiments and later descriptive extensions are distinguished from the sealed confirmation study. The archive contains public confirmation metadata and the confirmation summaries presented in the manuscript.

## Install in a separate environment

Use CPython 3.11. The pins require NumPy 2.2.6 and SciPy 1.17.1, whose bundled HiGHS must be 1.12.0. Installing the separately pinned `highspy` package does not substitute for the backend used by SciPy. The commands below keep build files and the environment outside the payload, so they do not invalidate its file manifest.

```sh
work=$(mktemp -d)
python3.11 -m venv "$work/env"
. "$work/env/bin/activate"
python -m pip install --retries 0 -r requirements-lock.txt
mkdir "$work/source"
cp pyproject.toml "$work/source/"
cp -R src "$work/source/src"
python -m pip install --no-index --no-deps --no-build-isolation "$work/source"
export PYTHONDONTWRITEBYTECODE=1
python verify_package.py --environment
```

The installation commands place the environment and build directory alongside the package. There is no automatic retry or version substitution.

## Check the supplied evidence

```sh
python verify_package.py
```

The release verifier checks every payload file against `PACKAGE_MANIFEST.txt`, cross-checks recorded hashes for included results, and checks the 56 distributed public case files against the complete 168-case design manifest. It also reports 112 further entries in the complete design manifest. The digest of `PACKAGE_MANIFEST.txt` is supplied separately because a file cannot contain its own SHA-256.

`RESULT_HASH_BINDINGS.json` identifies the included manifest or verification field supporting each cross-check. The index identifies hash-only evidence covered by the package manifest. The command performs file-integrity checks.

The included scientific verifier sources retain their original implementation. Use the release command above to check the supplied archive.

## Run the solver-free tests

```sh
python verify_package.py --run-tests
```

The command runs the seven modules in `SOLVER_FREE_TESTS.txt` plus synthetic release-verifier tests. It blocks optimization entry points, uses an external temporary directory, disables bytecode and pytest cache writes, and retains a 900-second wall limit and 512-MiB peak-memory limit. The exact-arithmetic G0 regression contains 14,560 fixed cases and can take several minutes. The command uses synthetic public fixtures and the explicit test selection listed above.

## Reproduce one small public experiment

This separate check runs the unchanged planner once on a 20-period public serial case, then compares its eight saved C00 summary fields, including qualification dates. It uses the saved 1e-6 comparison tolerance, at most 20 MIPs, a 30-second limit per MIP, and a 900-second/512-MiB overall limit. The command evaluates this public path. This optimization check is deliberately separate from the solver-free command.

```sh
PYTHONDONTWRITEBYTECODE=1 python - <<'PYCODE'
import json, math, resource, signal, sys, time
from pathlib import Path
from sn.belief_v3_common import load_case
from sn.core import Notice
from sn.planner_v3_single_r3 import run_benchmark
root=Path.cwd(); cid="v3-c0-r1-interior-serial-small-0"
m=json.loads((root/"configs/v3-design/manifest.json").read_text())
case=load_case(root,next(x for x in m["cases"] if x["case_id"]==cid))
def timeout(*_): raise RuntimeError("public check exceeded 900 seconds")
signal.signal(signal.SIGALRM,timeout); signal.alarm(900)
start=time.monotonic()
run=run_benchmark(network=case.network,contract=case.contract,
    notice=Notice(case.long_capacity,"major"),short_capacity=case.short_capacity,
    long_capacity=case.long_capacity,actual_type="long",realized_demand=case.baseline_demand,
    mean_demand=case.baseline_demand,benchmark="literal",prior_severe=.5,literal_severe=1.,
    presolve=False,threads=1,time_limit=30.)
rows=run.ledgers
actual=dict(buyer_cost=sum(x.operating_cost for x in rows),backup=sum(x.backup_delivered for x in rows),
    primary=sum(x.primary_delivered for x in rows),
    accepted_shortfall=sum(x.primary_shortfall-x.pre_horizon_order_shortfall for x in rows),
    pre_horizon_shortfall=sum(x.pre_horizon_order_shortfall for x in rows),
    lost_sales=sum(y.quantity for x in rows for y in x.lost_sales),spot=sum(x.spot_delivered for x in rows),
    qualification_dates=[i for i,a in enumerate(run.actions) if a.qualify])
saved=json.loads((root/"results/v3_design_false_notice_r2/result.json").read_text())
expected=next(x for x in saved["case_rows"] if x["case_id"]==cid)["cells"]["C00"]
for k,v in expected.items():
    assert actual[k]==v if isinstance(v,list) else math.isclose(actual[k],v,rel_tol=0,abs_tol=1e-6),(k,actual[k],v)
peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024**2 if sys.platform=="darwin" else 1024)
assert peak<=512, "public check exceeded 512 MiB"
assert len(run.certificates)==20 and all(c.status==0 and c.optimal for c in run.certificates)
signal.alarm(0)
print(json.dumps(dict(status="passed",case_id=cid,cell="C00",periods=len(rows),mip_phases=len(run.certificates),
    largest_total_difference=max(abs(actual[k]-v) for k,v in expected.items() if not isinstance(v,list)),
    qualification_dates=actual["qualification_dates"],wall_seconds=time.monotonic()-start,peak_rss_mib=peak,retries=0),indent=2))
PYCODE
```

## Manuscript and release scope

`manuscript/main.tex`, `manuscript/supplement.tex`, `manuscript/support/values.tex`, the figures, bibliography and Elsevier files form the prepared manuscript project. `manuscript/main.pdf` is the current article PDF. Typeset these sources with their prepared tables, values and figures.

`RELEASE_ASSETS.txt` lists paths, sizes and hashes for ten scientific evidence assets, including files over 25 MB. `docs/PROVENANCE.md` summarizes the research protocol and deviations. The scientific files retain their recorded bytes and historical identifiers.

The MIT license covers original code. The data license remains TODO; third-party LaTeX files retain their own notices. `CITATION.cff` and the LICENSE holder contain placeholders for the authors to complete.
