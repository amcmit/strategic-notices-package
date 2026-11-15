"""Unforced-equivalence test for the versioned forced-qualification planner.

The only CLI command here tests the versioned planner without imposing the
forced action. Scientific forced comparisons require a later approval gate.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import resource
import signal
import sys
from time import monotonic

from . import planner_forced_v1
from .core import (Action, Contract, Demand, Notice, Production, Realization,
                   Shipment, audit_step, initial_state, step)
from .forced_check_plan import inspect
from .m3 import Task, _key, _read_cached, load_approved, scientific_input, source_hashes
from .planner_forced_v1 import PlanningProblem, Scenario, SolverCertificate
from .suite import canonical


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _rss_mib() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def _alarm(_number, _frame) -> None:
    raise RuntimeError("unforced-equivalence test wall cap reached")


def cached_opening_problem(case, record: dict, q: int) -> PlanningProblem:
    """Reconstruct one cached planner state without a new policy rollout."""
    payload = record["input"]
    actual = tuple(payload["long_capacity"])
    contract = Contract(**payload["contract"])
    notice = Notice(tuple(payload["notice"]["announced_capacity"]), payload["notice"]["text"])
    state = initial_state(case.network)
    for t in range(q):
        saved_action = record["rollout"]["actions"][t]
        action = Action(
            qualify=saved_action["qualify"],
            primary_order=saved_action["primary_order"],
            spot_order=saved_action["spot_order"],
            production=tuple(Production(**item) for item in saved_action["production"]),
            shipments=tuple(Shipment(**item) for item in saved_action["shipments"]),
        )
        demand = tuple(Demand(**item) for item in payload["realized_demand"][t])
        previous = state
        state, ledger = step(
            case.network, contract, notice, state, action,
            Realization(actual[t], demand),
            revealed_schedule=actual if t == contract.revelation_period else None,
        )
        audit_step(case.network, previous, state, ledger)
        if json.loads(canonical(asdict(ledger))) != record["rollout"]["ledgers"][t]:
            raise ValueError("cached prefix ledger mismatch")
    if state.period != q or state.qualified_at is not None:
        raise ValueError("cached opening state differs from unqualified period")
    realized = tuple(tuple(Demand(**item) for item in period)
                     for period in payload["realized_demand"])
    forecast = [tuple(Demand(**item) for item in period)
                for period in payload["mean_demand"]]
    forecast[q] = realized[q]
    return PlanningProblem(
        case.network, contract, notice, state,
        (Scenario("long", 1.0, actual),), actual[q], realized[q],
        tuple(forecast), payload["per_solve_phase_time_limit_seconds"],
    )


def compare_unforced(cached: dict, versioned: SolverCertificate,
                     *, tolerance: float = 1e-8) -> dict:
    """Require the copy to reproduce both cached primary optimality bounds."""
    if (cached["phase"] != "primary" or not cached["optimal"]
            or versioned.phase != "primary" or not versioned.optimal
            or cached["status"] != versioned.status
            or cached["primal"] is None or cached["dual"] is None
            or versioned.primal is None or versioned.dual is None):
        raise ValueError("unforced primary certificate unavailable")
    dimensions = ("model_variables", "model_constraints", "model_integer_variables",
                  "model_nonzeros")
    if any(cached[key] != getattr(versioned, key) for key in dimensions):
        raise ValueError("unforced versioned MIP dimensions differ from cached planner")
    primal_delta = versioned.primal - cached["primal"]
    dual_delta = versioned.dual - cached["dual"]
    if abs(primal_delta) > tolerance or abs(dual_delta) > tolerance:
        raise ValueError("unforced versioned primal or dual differs from cached certificate")
    return {
        "cached_primal": cached["primal"], "cached_dual": cached["dual"],
        "versioned_primal": versioned.primal, "versioned_dual": versioned.dual,
        "primal_delta": primal_delta, "dual_delta": dual_delta,
        "versioned_certificate": asdict(versioned),
    }


def validate_unforced(root: Path, manifest_path: Path, backup_archive: Path,
                      m3_path: Path, m3_run: Path, output: Path) -> dict:
    manifest = json.loads(manifest_path.read_bytes())
    if output.exists() or not output.resolve().is_relative_to(root):
        raise ValueError("unforced test output must be new and inside repository")
    started = monotonic()
    signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, manifest["resource_caps"]["wall_seconds"])
    try:
        preflight = inspect(root, manifest_path, backup_archive, m3_path, m3_run)
        m3, cases, m3_digest = load_approved(m3_path, root)
        by_id = {case.identifier: case for case in cases}
        codes = source_hashes()
        rows = []
        for path_row in manifest["paths"]:
            task = Task(path_row["case_id"], "long", "major",
                        float(path_row["demand_multiplier"]), "literal", 1.0)
            case = by_id[task.case_id]
            payload = scientific_input(task, case, m3, m3_digest, codes)
            record = _read_cached(m3_run / "trajectories" / f"{_key(payload)}.json", payload)
            if record is None:
                raise ValueError("exact cached trajectory missing after preflight")
            for q in path_row["qualification_dates"]:
                problem = cached_opening_problem(case, record, q)
                certificate = planner_forced_v1.plan(problem, primary_only=True)
                if not isinstance(certificate, SolverCertificate):
                    raise ValueError("unforced versioned planner returned no primary certificate")
                comparison = compare_unforced(record["rollout"]["certificates"][q][0], certificate)
                rows.append({"case_id": task.case_id, "demand_multiplier": task.demand_multiplier,
                             "qualification_date": q, **comparison})
                if (_rss_mib() > manifest["resource_caps"]["process_rss_mib"]
                        or monotonic() - started >= manifest["resource_caps"]["wall_seconds"]):
                    raise RuntimeError("unforced-equivalence test resource cap reached")
        if len(rows) != manifest["uncertified_path_date_count"]:
            raise ValueError("unforced-equivalence state count mismatch")
        result = {
            "schema": 1, "status": "unforced_versioned_planner_matches_cache",
            "manifest_sha256": preflight["manifest_sha256"],
            "m3_trajectory_tree_sha256": manifest["m3_trajectory_tree_sha256"],
            "planner_v1_sha256": codes["planner.py"],
            "planner_forced_v1_sha256": _digest(Path(planner_forced_v1.__file__)),
            "unforced_engine_sha256": _digest(Path(__file__)),
            "comparison_tolerance": 1e-8,
            "compared_state_count": len(rows),
            "max_abs_primal_delta": max(abs(row["primal_delta"]) for row in rows),
            "max_abs_dual_delta": max(abs(row["dual_delta"]) for row in rows),
            "new_unforced_primary_mip_solves": len(rows),
            "new_forced_mip_solves": 0, "new_physical_trajectories": 0,
            "rows": rows,
            "resources": {"active_wall_seconds": monotonic() - started,
                          "peak_process_rss_mib": _rss_mib()},
        }
        raw = canonical(result)
        if (len(raw) > manifest["resource_caps"]["disk_mib"] * 1024 * 1024
                or _rss_mib() > manifest["resource_caps"]["process_rss_mib"]
                or monotonic() - started >= manifest["resource_caps"]["wall_seconds"]):
            raise RuntimeError("unforced-equivalence test output cap reached")
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        if temporary.exists():
            raise ValueError("stale unforced-equivalence temporary output")
        temporary.write_bytes(raw)
        temporary.replace(output)
        return {"result_sha256": sha256(raw).hexdigest(),
                "state_count": len(rows),
                "max_abs_primal_delta": result["max_abs_primal_delta"],
                "max_abs_dual_delta": result["max_abs_dual_delta"],
                "resources": result["resources"], "output_bytes": len(raw)}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.forced_check_engine")
    parser.add_argument("command", choices=("validate-unforced",))
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--backup-archive", type=Path, required=True)
    parser.add_argument("--m3-manifest", type=Path, required=True)
    parser.add_argument("--m3-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(validate_unforced(
        args.repository_root.resolve(), args.manifest.resolve(),
        args.backup_archive.resolve(), args.m3_manifest.resolve(),
        args.m3_run.resolve(), args.output.resolve()), sort_keys=True))


if __name__ == "__main__":
    main()
