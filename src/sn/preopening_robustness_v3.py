"""Public-seed four-cell engineering robustness; never reads private cases."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import resource
import signal
import sys
import traceback
from time import monotonic

from . import planner_v3_single_r3 as planner
from .allocation_v3 import canonical
from .confirmation_v3_r2 import draw_fixture_set
from .core import Notice, Realization
from .core_v3 import initial_state_v3, step_v3
from .planner_v3_single_r3 import PlannerFailure
from .profile_v3 import _receipt
from .confirmation_structure_v3 import structure_digest


MANIFEST_REL = "configs/proposed-v3-preopening-robustness.json"
RESULT_ROOT = "results/v3_preopening_robustness"
DECISION = "owner_approved_v3_preopening_robustness"
PUBLIC_SEEDS = tuple(range(20261114, 20261124))
CELLS = ("C00", "C10", "C01", "C11")
SOURCES = (
    "src/sn/allocation_v3.py", "src/sn/core.py", "src/sn/core_v2.py",
    "src/sn/core_v3.py", "src/sn/handoff_v3_single.py",
    "src/sn/networks_v3.py", "src/sn/planner_v3_single_r3.py",
    "src/sn/confirmation_v3.py", "src/sn/confirmation_structure_v3.py",
    "src/sn/confirmation_v3_r2.py", "src/sn/preopening_robustness_v3.py",
    "src/sn/profile_v3.py",
)
INPUTS = (
    "configs/proposed-v3-allocation.json",
    "configs/v3-design/selection-freeze.json",
    "configs/v3-design/manifest.json",
    "configs/v3-confirmation-public.json",
    "docs/V3_SINGLE_SOLVE_R3_PROTOCOL.md",
    "docs/V3_PROTOCOL_R5_CONFIRMATION_POPULATION.md",
    "docs/V3_PROTOCOL_R6_FOUR_CELL_DESIGN.md",
    "docs/V3_PROTOCOL_R7_CONFIRMATION_FAILURE_POLICY.md",
    "tests/test_v3_confirmation_structure_r2.py",
)


class ResourceStop(RuntimeError):
    pass


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _rss_mib() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value / (1024 * 1024) if sys.platform == "darwin" else value / 1024


def expected_manifest(root: Path) -> dict:
    """Construct the exact task set using only the ten published test seeds."""
    root = root.resolve()
    tasks = []
    for seed in PUBLIC_SEEDS:
        drawn = draw_fixture_set(root, seed)
        if len(drawn) != 56:
            raise ValueError("public-seed fixture count differs")
        for _, case, attempts in drawn:
            tasks.append({"public_seed": seed, "case_id": case.case_id,
                          "content_sha256": sha256(canonical(asdict(case))).hexdigest(),
                          "structure_sha256": structure_digest(case),
                          "draw_attempts": attempts})
    if len(tasks) != 560 or len({(x["public_seed"], x["case_id"]) for x in tasks}) != 560:
        raise ValueError("public-seed task set differs")
    return {
        "schema": 1, "kind": "v3_public_seed_preopening_robustness",
        "status": "proposal_unrun_pending_exact_owner_hash",
        "source_sha256": {name: _digest(root / name) for name in SOURCES},
        "input_sha256": {name: _digest(root / name) for name in INPUTS},
        "test_sha256": _digest(root / "tests/test_v3_preopening_robustness.py"),
        "public_seeds": list(PUBLIC_SEEDS), "tasks": tasks, "cells": list(CELLS),
        "actual_type": "long", "demand_multiplier": 1.0,
        "planner": {"source": "planner_v3_single_r3", "presolve": False,
                    "threads": 1, "MIP_phases_per_period": 1},
        "resource_caps": {"cases": 560, "paths": 2240, "periods_per_path": 20,
                          "maximum_mip_phases": 44800, "per_mip_seconds": 30,
                          "wall_seconds": 1800, "peak_rss_mib": 512,
                          "saved_bytes": 134217728, "retries": 0},
        "receipt_decision": DECISION,
        "paths": {"approval_receipt": "configs/owner-approvals/v3-preopening-robustness.json",
                  "output_root": RESULT_ROOT,
                  "attempt_marker": RESULT_ROOT + "/attempt.started",
                  "first_solve_marker": RESULT_ROOT + "/first_solve.started",
                  "progress": RESULT_ROOT + "/progress.jsonl",
                  "result": RESULT_ROOT + "/result.json"},
        "saved_fields": {"per_path": ["path_id", "status", "periods", "failure"],
                         "per_period": ["period", "solver_certificate", "handoff_corrections"],
                         "scientific_quantities": False, "actions": False,
                         "ledgers": False, "supplier_payoffs": False},
        "scope": {"public_seed_only": True, "private_case_access": False,
                  "private_seed_access": False, "engineering_only": True,
                  "scientific_decision": False, "m5_paused": True},
    }


def verify_manifest(root: Path, path: Path) -> tuple[dict, str]:
    root, path = root.resolve(), path.resolve()
    if path != root / MANIFEST_REL:
        raise ValueError("unexpected pre-opening robustness manifest path")
    raw = path.read_bytes()
    value = json.loads(raw)
    if raw != canonical(value) or value != expected_manifest(root):
        raise ValueError("public-seed robustness manifest differs from bound inputs")
    return value, sha256(raw).hexdigest()


def _failure(path_id: str, period: int | None, error: BaseException) -> dict:
    row = {"path_id": path_id, "period": period, "type": type(error).__name__,
           "full_error_text": str(error), "traceback": traceback.format_exc()}
    if isinstance(error, PlannerFailure):
        row["solver_certificate"] = asdict(error.certificate)
    return row


def _alarm(_signum, _frame):
    raise ResourceStop("public-seed robustness wall cap reached")


def belief_scenario(case, period: int, cell: str) -> planner.Scenario:
    if cell not in CELLS or not 0 <= period < case.network.horizon:
        raise ValueError("unknown public-seed cell or period")
    long_belief = period >= case.contract.revelation_period or cell in ("C00", "C10")
    return planner.Scenario("long" if long_belief else "short", 1.0,
                            case.long_capacity if long_belief else case.short_capacity)


def run(root: Path, manifest_path: Path) -> dict:
    root = root.resolve()
    manifest, manifest_sha = verify_manifest(root, manifest_path)
    _receipt(root / manifest["paths"]["approval_receipt"], DECISION, manifest_sha)
    output = root / RESULT_ROOT
    if output.exists():
        raise FileExistsError("public-seed robustness attempt already exists; zero retries")
    output.mkdir(parents=True)
    (output / "attempt.started").write_bytes(canonical({"execution_manifest_sha256": manifest_sha}))
    caps = manifest["resource_caps"]
    started = monotonic()
    phases, phase_seconds, completed, failures = 0, [], 0, []
    original_solve = planner._solve

    def measured_solve(model, objective, time_limit, phase, **kwargs):
        nonlocal phases
        if phases >= caps["maximum_mip_phases"]:
            raise ResourceStop("public-seed robustness MIP phase cap reached")
        marker = output / "first_solve.started"
        if not marker.exists():
            marker.write_bytes(canonical({"execution_manifest_sha256": manifest_sha}))
        phases += 1
        began = monotonic()
        try:
            return original_solve(model, objective, time_limit, phase, **kwargs)
        finally:
            elapsed = monotonic() - began
            phase_seconds.append(elapsed)
            if elapsed > caps["per_mip_seconds"] + 0.01:
                raise ResourceStop("public-seed robustness per-MIP cap reached")
            if _rss_mib() > caps["peak_rss_mib"]:
                raise ResourceStop("public-seed robustness memory cap reached")
            if monotonic() - started > caps["wall_seconds"]:
                raise ResourceStop("public-seed robustness wall cap reached")

    planner._solve = measured_solve
    previous_alarm = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, caps["wall_seconds"])
    task_index = 0
    active_path, period = "<runner>", None
    try:
        with (output / "progress.jsonl").open("xb") as stream:
            for seed in PUBLIC_SEEDS:
                for _, case, attempts in draw_fixture_set(root, seed):
                    task = manifest["tasks"][task_index]
                    task_index += 1
                    if (task["public_seed"] != seed or task["case_id"] != case.case_id
                            or task["draw_attempts"] != attempts
                            or task["content_sha256"] !=
                               sha256(canonical(asdict(case))).hexdigest()
                            or task["structure_sha256"] != structure_digest(case)):
                        raise ValueError("public-seed fixture differs from bound manifest")
                    for cell in CELLS:
                        active_path = f"seed-{seed}/{case.case_id}|{cell}"
                        saved = {"path_id": active_path, "status": "running", "periods": []}
                        period = None
                        stop = False
                        try:
                            state = initial_state_v3(case.network)
                            minor = cell in ("C10", "C11")
                            notice = Notice(case.short_capacity if minor else case.long_capacity,
                                            "minor" if minor else "major")
                            for period in range(case.network.horizon):
                                scenario = belief_scenario(case, period, cell)
                                problem = planner.PlanningProblem(
                                    case.network, case.contract, notice, state, (scenario,),
                                    case.long_capacity[period], case.baseline_demand[period],
                                    case.baseline_demand, caps["per_mip_seconds"], False, 1)
                                choice = planner.plan(problem)
                                state, _ledger = step_v3(
                                    case.network, case.contract, notice, state, choice.action,
                                    Realization(case.long_capacity[period], case.baseline_demand[period]),
                                    revealed_schedule=(case.long_capacity if period == case.contract.revelation_period
                                                       else None))
                                saved["periods"].append({
                                    "period": period,
                                    "solver_certificate": asdict(choice.certificate),
                                    "handoff_corrections": [asdict(x) for x in choice.handoff.corrections],
                                })
                            saved["status"] = "completed"
                            completed += 1
                        except Exception as error:
                            saved["status"] = "failed"
                            saved["failure"] = _failure(active_path, period, error)
                            failures.append(saved["failure"])
                            stop = isinstance(error, ResourceStop)
                        stream.write(canonical(saved))
                        stream.flush()
                        if stream.tell() > caps["saved_bytes"]:
                            raise ResourceStop("public-seed robustness progress byte cap reached")
                        if stop:
                            break
                    if stop:
                        break
                if stop:
                    break
    except Exception as error:
        failures.append(_failure(active_path, period, error))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_alarm)
        planner._solve = original_solve
    status = "completed" if completed == 2240 and not failures and phases == 44800 else "stopped"
    result = {"status": status, "execution_manifest_sha256": manifest_sha,
              "attempted_paths": sum(1 for _ in (output / "progress.jsonl").open("rb")),
              "completed_paths": completed, "failures": failures,
              "mip_phases": phases, "maximum_phase_seconds": max(phase_seconds, default=0.0),
              "elapsed_seconds": monotonic() - started, "peak_rss_mib": _rss_mib(),
              "progress_sha256": _digest(output / "progress.jsonl"),
              "private_case_access": False, "private_seed_access": False,
              "scientific_quantities_saved": False, "m5_paused": True}
    raw = canonical(result)
    if len(raw) + (output / "progress.jsonl").stat().st_size > caps["saved_bytes"]:
        raise ResourceStop("public-seed robustness saved-output byte cap reached")
    (output / "result.json").write_bytes(raw)
    return {"status": status, "result_sha256": sha256(raw).hexdigest(),
            "execution_manifest_sha256": manifest_sha}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.preopening_robustness_v3")
    parser.add_argument("command", choices=("propose", "review", "run"))
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    path = args.manifest or root / MANIFEST_REL
    if args.command == "propose":
        with path.open("xb") as stream:
            stream.write(canonical(expected_manifest(root)))
        print(json.dumps({"manifest_sha256": _digest(path), "mip_phases": 0}, sort_keys=True))
    elif args.command == "review":
        value, digest = verify_manifest(root, path)
        print(json.dumps({"manifest_sha256": digest, "cases": len(value["tasks"]),
                          "maximum_mip_phases": value["resource_caps"]["maximum_mip_phases"],
                          "mip_phases": 0}, sort_keys=True))
    else:
        print(json.dumps(run(root, path), sort_keys=True))


if __name__ == "__main__":
    main()
