"""Approval-gated v3 truthful calibration; no false notices or confirmation data."""

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
from .core import Notice, Realization
from .core_v3 import initial_state_v3, step_v3
from .networks_v3 import _approved_hash, build_public_case, load_allocation
from .planner_v3_single_r3 import PlannerFailure
from .profile_v3 import _receipt


PLAN_SHA256 = "da170883e12220ad426437ea395607aa441bb8af2c86e50fe69c84f68d4a2738"
MANIFEST_REL = "configs/proposed-v3-truthful-calibration-execution-r4.json"
DECISION = "owner_approved_v3_truthful_calibration_r4"
SOURCE_FILES = (
    "src/sn/allocation_v3.py", "src/sn/core.py", "src/sn/core_v2.py",
    "src/sn/core_v3.py", "src/sn/handoff_v3_single.py",
    "src/sn/networks_v3.py", "src/sn/planner_v3_single_r3.py",
    "src/sn/profile_v3.py", "src/sn/calibration_v3_r4.py",
    "src/sn/verify_calibration_v3_r4.py",
)


class ResourceStop(RuntimeError):
    pass


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _rss_mib() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def _inputs(root: Path) -> tuple[dict, dict, dict[str, dict]]:
    plan_path = root / "configs/proposed-v3-truthful-calibration-plan-r4.json"
    raw = plan_path.read_bytes()
    if _digest(plan_path) != PLAN_SHA256 or raw != canonical(json.loads(raw)):
        raise ValueError("accepted G1 calibration plan changed")
    plan = json.loads(raw)
    if plan["executable"] is not False or len(plan["path_ids"]) != 336:
        raise ValueError("unexpected truthful calibration planning scope")
    for key, path in (
        ("protocol_sha256", "docs/V3_SINGLE_SOLVE_R3_PROTOCOL.md"),
        ("allocation_sha256", "configs/proposed-v3-allocation.json"),
        ("public_design_manifest_sha256", "configs/v3-design/manifest.json"),
        ("g1_regression_result_sha256", "results/v3_single_regression_r4/stability.json"),
        ("g1_full_result_sha256", "results/v3_single_full_r4/stability.json"),
    ):
        if _digest(root / path) != plan[key]:
            raise ValueError(f"bound calibration input changed: {path}")
    for name, expected in plan["source_sha256"].items():
        if _digest(root / name) != expected:
            raise ValueError(f"G1 source changed: {name}")
    for name in ("regression", "full"):
        result = json.loads((root / f"results/v3_single_{name}_r4/stability.json").read_bytes())
        if (result.get("status") != "completed" or result.get("stability_acceptance") is not True
                or result.get("path_failure_count") != 0):
            raise ValueError(f"G1 {name} did not pass")
    allocation = load_allocation(
        root / "configs/proposed-v3-allocation.json",
        root / "configs/design/manifest.json",
        _approved_hash(root / "configs/owner-approvals/v3-allocation.json"),
    )
    rows = {row["case_id"]: row for candidate in allocation["candidates"]
            for row in candidate["cases"]}
    if len(rows) != 168 or len(plan["public_case_content_sha256"]) != 168:
        raise ValueError("public calibration cases incomplete")
    if set(rows) != set(plan["public_case_content_sha256"]):
        raise ValueError("public case IDs differ from G1 plan")
    return plan, allocation, rows


def expected_manifest(root: Path) -> dict:
    root = root.resolve()
    plan, _, rows = _inputs(root)
    tasks = []
    for path_id in plan["path_ids"]:
        case_id, cell = path_id.split("|", 1)
        if cell not in ("short/minor", "long/major"):
            raise ValueError("calibration task is not truthful")
        row = rows[case_id]
        tasks.append({
            "path_id": path_id, "candidate_index": row["candidate_index"],
            "case_id": case_id, "cell": cell, "regime": row["regime"],
            "category": row["category"], "family": row["family"],
            "size": row["size"],
            "case_sha256": plan["public_case_content_sha256"][case_id],
        })
    if len(tasks) != 336 or len({x["path_id"] for x in tasks}) != 336:
        raise ValueError("truthful task count changed")
    caps = plan["resource_cap_proposal"]
    if caps["maximum_mip_phases"] != 6720 or caps["retries"] != 0:
        raise ValueError("truthful calibration caps changed")
    return {
        "schema": 1, "kind": "v3_truthful_calibration_execution_r4",
        "status": "proposal_unrun_pending_exact_owner_hash",
        "planning_manifest_sha256": PLAN_SHA256,
        "protocol_sha256": plan["protocol_sha256"],
        "allocation_sha256": plan["allocation_sha256"],
        "public_design_manifest_sha256": plan["public_design_manifest_sha256"],
        "public_case_content_sha256": plan["public_case_content_sha256"],
        "g1_regression_manifest_sha256": plan["g1_regression_execution_manifest_sha256"],
        "g1_regression_result_sha256": plan["g1_regression_result_sha256"],
        "g1_full_manifest_sha256": plan["g1_full_execution_manifest_sha256"],
        "g1_full_result_sha256": plan["g1_full_result_sha256"],
        "source_sha256": {name: _digest(root / name) for name in SOURCE_FILES},
        "test_sha256": _digest(root / "tests/test_v3_truthful_calibration.py"),
        "tasks": tasks, "candidate_order": [0, 1, 2],
        "operating_configuration": plan["operating_configuration"],
        "selection": plan["screen"], "resource_caps": caps,
        "receipt_decision": DECISION,
        "paths": {
            "approval_receipt": "configs/owner-approvals/v3-truthful-calibration-r4.json",
            "output_root": "results/v3_truthful_calibration_r4",
            "attempt_marker": "results/v3_truthful_calibration_r4/attempt.started",
            "first_solve_marker": "results/v3_truthful_calibration_r4/first_solve.started",
            "progress": "results/v3_truthful_calibration_r4/progress.jsonl",
            "result": "results/v3_truthful_calibration_r4/result.json",
            "verification": "results/v3_truthful_calibration_r4/verification.json",
        },
        "saved_outputs": ["truthful projected actions and ledger digests for independent replay",
                          "certificate summaries and all failures with full text",
                          "all candidate counts, boundary-control rows and descriptive prediction exceptions",
                          "resource accounting"],
        "scope": {"false_notice_paths": False, "confirmation_access": False,
                  "sealing": False, "m5_paused": True, "retries": 0},
    }


def verify_manifest(root: Path, path: Path) -> tuple[dict, str]:
    root, path = root.resolve(), path.resolve()
    if path != root / MANIFEST_REL:
        raise ValueError("unexpected v3 calibration execution manifest path")
    raw = path.read_bytes()
    if raw != canonical(json.loads(raw)):
        raise ValueError("v3 calibration manifest is noncanonical")
    value = json.loads(raw)
    if value != expected_manifest(root):
        raise ValueError("v3 calibration manifest differs from bound sources")
    return value, sha256(raw).hexdigest()


def _qualification_dates(path: dict) -> list[int]:
    return [period["period"] for period in path["periods"]
            if period["action"]["qualify"]]


def assess_candidate(index: int, paths: list[dict], tasks: list[dict]) -> dict:
    """Count every truthful category; only long/major R1 interior gates."""
    task_by_id = {task["path_id"]: task for task in tasks}
    mine = [path for path in paths if task_by_id[path["path_id"]]["candidate_index"] == index]
    if len(mine) != 112 or any(len(path["periods"]) != 20 for path in mine):
        raise ValueError("candidate lacks 112 complete truthful paths")
    by_id = {path["path_id"]: path for path in mine}
    if len(by_id) != 112:
        raise ValueError("candidate has duplicate truthful path")
    counts = {cell: {regime: {"selected": 0, "total": 0}
                     for regime in ("R1", "R2", "R3", "R4")}
              for cell in ("short/minor", "long/major")}
    interior = {cell: {"selected": 0, "total": 0}
                for cell in ("short/minor", "long/major")}
    boundary: dict[str, dict] = {}
    exceptions = []
    for path in mine:
        task = task_by_id[path["path_id"]]
        dates = _qualification_dates(path)
        if len(dates) > 1:
            raise ValueError(f"multiple qualification decisions: {path['path_id']}")
        selected = bool(dates)
        cell, regime, category = task["cell"], task["regime"], task["category"]
        counts[cell][regime]["total"] += 1
        counts[cell][regime]["selected"] += int(selected)
        if regime == "R1" and category == "interior":
            interior[cell]["total"] += 1
            interior[cell]["selected"] += int(selected)
        if regime == "R1" and category.endswith("_closed"):
            row = boundary.setdefault(task["case_id"], {
                "case_id": task["case_id"], "category": category,
                "family": task["family"], "size": task["size"], "cells": {},
            })
            row["cells"][cell] = {"selected": selected, "qualification_dates": dates}
            if selected:
                exceptions.append({"path_id": path["path_id"],
                                   "reason": "R1 negative-control qualification",
                                   "qualification_dates": dates})
        if regime == "R2" and selected:
            exceptions.append({"path_id": path["path_id"],
                               "reason": "R2 no-backup prediction exception",
                               "qualification_dates": dates})
        if regime == "R3" and not selected:
            exceptions.append({"path_id": path["path_id"],
                               "reason": "R3 backup-use prediction exception",
                               "qualification_dates": []})
    if (any(interior[cell]["total"] != 24 for cell in interior)
            or len(boundary) != 8
            or any(set(row["cells"]) != {"short/minor", "long/major"}
                   for row in boundary.values())
            or any(counts[cell][regime]["total"] != (32 if regime == "R1" else 8)
                   for cell in counts for regime in counts[cell])):
        raise ValueError("candidate allocation strata incomplete")
    passed = interior["long/major"]["selected"] >= 12
    return {"candidate_index": index, "passed": passed,
            "screen": {"selected": interior["long/major"]["selected"],
                       "total": 24, "floor": 12, "no_upper_cap": True},
            "r1_interior_by_cell": interior,
            "regime_by_cell": counts,
            "boundary_controls": [boundary[key] for key in sorted(boundary)],
            "descriptive_prediction_exceptions": sorted(
                exceptions, key=lambda x: (x["path_id"], x["reason"])),
            "r4_violation_rule": "none: rarely used has no numeric threshold",
    }


def select_first_passing(assessments: list[dict]) -> int | None:
    if [row["candidate_index"] for row in assessments] != [0, 1, 2]:
        raise ValueError("candidate order must be 0, 1, 2")
    return next((row["candidate_index"] for row in assessments if row["passed"]), None)


def _failure(path_id: str, period: int | None, error: Exception) -> dict:
    result = {"path_id": path_id, "period": period, "type": type(error).__name__,
              "full_error_text": str(error), "traceback": traceback.format_exc()}
    if isinstance(error, PlannerFailure):
        result["solver_certificate"] = asdict(error.certificate)
    return result


def _alarm(_signum, _frame) -> None:
    raise ResourceStop("calibration global wall cap reached")


def run(root: Path, manifest_path: Path) -> dict:
    root = root.resolve()
    manifest, manifest_sha = verify_manifest(root, manifest_path)
    _receipt(root / manifest["paths"]["approval_receipt"], DECISION, manifest_sha)
    output_root = root / manifest["paths"]["output_root"]
    if output_root.exists():
        raise FileExistsError("calibration attempt already exists; zero retries")
    output_root.mkdir(parents=True)
    (root / manifest["paths"]["attempt_marker"]).write_bytes(
        canonical({"execution_manifest_sha256": manifest_sha}))
    progress_path = root / manifest["paths"]["progress"]
    caps = manifest["resource_caps"]
    _, _, rows = _inputs(root)
    started = monotonic()
    phases = 0
    completed = []
    failures = []
    original_solve = planner._solve
    phase_elapsed = []

    def measured_solve(model, objective, time_limit, phase, **kwargs):
        nonlocal phases
        if phases >= caps["maximum_mip_phases"]:
            raise ResourceStop("calibration MIP-phase cap reached")
        marker = root / manifest["paths"]["first_solve_marker"]
        if not marker.exists():
            marker.write_bytes(canonical({"execution_manifest_sha256": manifest_sha}))
        phases += 1
        began = monotonic()
        try:
            return original_solve(model, objective, time_limit, phase, **kwargs)
        finally:
            elapsed = monotonic() - began
            phase_elapsed.append(elapsed)
            if elapsed > caps["per_mip_seconds"] + 0.01:
                raise ResourceStop("calibration per-MIP time cap reached")
            if _rss_mib() > caps["peak_rss_mib"]:
                raise ResourceStop("calibration peak RSS cap reached")
            if monotonic() - started > caps["wall_seconds"]:
                raise ResourceStop("calibration global wall cap reached")

    planner._solve = measured_solve
    old_alarm = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, caps["wall_seconds"])
    active = None
    period = None
    try:
        with progress_path.open("xb") as stream:
            for task in manifest["tasks"]:
                active = {"path_id": task["path_id"], "status": "running", "periods": []}
                period = None
                stop = False
                try:
                    case = build_public_case(rows[task["case_id"]])
                    if sha256(canonical(asdict(case))).hexdigest() != task["case_sha256"]:
                        raise ValueError(f"public case content changed: {task['case_id']}")
                    actual_type, notice_type = task["cell"].split("/")
                    schedule = (case.short_capacity if actual_type == "short"
                                else case.long_capacity)
                    notice = Notice(schedule, notice_type)
                    state = initial_state_v3(case.network)
                    for period in range(case.network.horizon):
                        problem = planner.PlanningProblem(
                            case.network, case.contract, notice, state,
                            (planner.Scenario(actual_type, 1.0, schedule),),
                            schedule[period], case.baseline_demand[period],
                            case.baseline_demand, caps["per_mip_seconds"], False, 1,
                        )
                        choice = planner.plan(problem)
                        state, ledger = step_v3(
                            case.network, case.contract, notice, state, choice.action,
                            Realization(schedule[period], case.baseline_demand[period]),
                            revealed_schedule=(schedule if period == case.contract.revelation_period
                                               else None),
                        )
                        cert = choice.certificate
                        active["periods"].append({
                            "period": period, "action": asdict(choice.action),
                            "ledger_sha256": sha256(canonical(asdict(ledger))).hexdigest(),
                            "certificate": {"status": cert.status, "optimal": cert.optimal,
                                            "primal": cert.primal, "dual": cert.dual,
                                            "relative_gap": cert.relative_gap},
                            "handoff_corrections": [asdict(x) for x in choice.handoff.corrections],
                        })
                    active["status"] = "completed"
                    active["final_state_sha256"] = sha256(canonical(asdict(state))).hexdigest()
                    completed.append(active)
                    active = None
                except Exception as error:
                    failure = _failure(task["path_id"], period, error)
                    active["status"] = "failed"
                    active["failure"] = failure
                    failures.append(failure)
                    stop = isinstance(error, ResourceStop)
                stream.write(canonical(active if active is not None else completed[-1]))
                stream.flush()
                active = None
                if progress_path.stat().st_size > caps["saved_bytes"]:
                    raise ResourceStop("calibration progress byte cap reached")
                if failures and stop:
                    break
    except Exception as error:
        failures.append(_failure(active["path_id"] if active else "<runner>",
                                 period if active else None, error))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_alarm)
        planner._solve = original_solve
    assessments = None
    selected = None
    status = "stopped"
    if not failures and len(completed) == len(manifest["tasks"]):
        try:
            assessments = [assess_candidate(i, completed, manifest["tasks"])
                           for i in manifest["candidate_order"]]
            selected = select_first_passing(assessments)
            status = "completed"
        except Exception as error:
            failures.append(_failure("<selection>", None, error))
    result = {
        "status": status, "execution_manifest_sha256": manifest_sha,
        "attempted_paths": len(completed) + sum(
            failure["path_id"] not in ("<runner>", "<selection>") for failure in failures),
        "completed_paths": len(completed), "failures": failures,
        "mip_phases": phases, "phase_elapsed_seconds_max": max(phase_elapsed, default=0.0),
        "elapsed_seconds": monotonic() - started, "peak_rss_mib": _rss_mib(),
        "progress_sha256": _digest(progress_path),
        "assessments": assessments, "selected_candidate": selected,
        "closed_no_candidate": status == "completed" and selected is None,
        "false_notice_paths": 0, "confirmation_access": False,
    }
    raw = canonical(result)
    if len(raw) + progress_path.stat().st_size > caps["saved_bytes"]:
        raise ResourceStop("calibration saved-output byte cap reached")
    (root / manifest["paths"]["result"]).write_bytes(raw)
    return {"status": status, "execution_manifest_sha256": manifest_sha,
            "result_sha256": sha256(raw).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.calibration_v3_r4")
    parser.add_argument("command", choices=("propose", "review", "run"))
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    path = args.manifest or root / MANIFEST_REL
    if args.command == "propose":
        with path.open("xb") as out:
            out.write(canonical(expected_manifest(root)))
        print(json.dumps({"manifest_sha256": _digest(path), "mip_phases": 0}))
    elif args.command == "review":
        manifest, digest = verify_manifest(root, path)
        print(json.dumps({"manifest_sha256": digest, "tasks": len(manifest["tasks"]),
                          "maximum_mip_phases": manifest["resource_caps"]["maximum_mip_phases"],
                          "mip_phases": 0}))
    else:
        print(json.dumps(run(root, path), sort_keys=True))


if __name__ == "__main__":
    main()
