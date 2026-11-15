"""Approval-gated resource-only profile of frozen v2 public design cases.

The review command validates the proposed manifest without solving a MIP.
The run command requires an owner receipt binding that manifest's exact SHA.
Only solver measurements and certificates are saved, never actions or costs.
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

import numpy
import scipy
from scipy.optimize._highspy import _core as highs_core

from . import planner_v2
from .core import Notice, Realization, initial_state
from .core_v2 import step_v2
from .networks_v2 import (
    APPROVED_ALLOCATION_SHA256, V2DesignCase, build_public_design,
    load_allocation, verify_public_design,
)
from .planner_v2 import PlanningProblem, PlannerFailure, Scenario
from .suite import canonical

DESIGN_SHA256 = "7f799ed392a933a3b6d3045b0613d042d7a61b0a53289833d8fcbc966884f9e2"
PREVIOUS_PROFILE_SHA256 = "a64dc279d83c64a45d9881c9dad6994fbbdf0c496bfb0f7b59a25fcdd5429c51"
ROLLING_SLOTS = (
    "serial-small-0", "serial-large-0", "assembly-small-0", "assembly-large-0",
    "distribution-small-0", "distribution-large-0", "layered_dual-small-0",
    "layered_dual-large-0", "planted-small-0", "planted-large-0",
)
CAPS = {
    "wall_seconds": 900, "per_mip_seconds": 4, "process_peak_rss_mib": 512,
    "output_bytes": 20 * 1024 * 1024, "maximum_mip_phases": 1520, "retries": 0,
}
SOURCE_FILES = ("core.py", "core_v2.py", "networks.py", "networks_v2.py",
                "planner_v2.py", "profile_v2.py", "suite.py")


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _rss_mib() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def source_hashes() -> dict[str, str]:
    return {name: _digest(Path(__file__).parent / name) for name in SOURCE_FILES}


def expected_tasks(case_rows: list[dict]) -> list[dict]:
    candidate = [row for row in case_rows if row["candidate_index"] == 0]
    if len(candidate) != 24 or len({row["slot_id"] for row in candidate}) != 24:
        raise ValueError("candidate 0 must have 24 distinct public design slots")
    tasks = [
        {"kind": "initial", "slot_id": row["slot_id"], "case_id": row["case_id"],
         "case_sha256": row["content_sha256"], "configuration": config}
        for row in candidate for config in ("minor_prior_half", "truthful_long_major")
    ]
    by_slot = {row["slot_id"]: row for row in candidate}
    tasks.extend(
        {"kind": "rolling", "slot_id": slot, "case_id": by_slot[slot]["case_id"],
         "case_sha256": by_slot[slot]["content_sha256"],
         "configuration": "truthful_long_major"}
        for slot in ROLLING_SLOTS
    )
    return tasks


def _paths(root: Path) -> dict[str, str]:
    return {
        "allocation": "configs/proposed-v2-factor-allocation.json",
        "v1_design_manifest": "configs/design/manifest.json",
        "public_design": "configs/v2-design",
        "approval_receipt": "configs/owner-approvals/v2-resource-profile-r5.json",
        "output": "results/v2_resource_profile_r5/profile.json",
    }


def proposed_manifest(root: Path) -> dict:
    paths = _paths(root)
    design_path = root / paths["public_design"] / "manifest.json"
    design = json.loads(design_path.read_bytes())
    if _digest(design_path) != DESIGN_SHA256:
        raise ValueError("v2 public design manifest hash changed")
    return {
        "schema": 1, "status": "proposed_round5_pending_owner_approval",
        "allocation_sha256": APPROVED_ALLOCATION_SHA256,
        "public_design_manifest_sha256": DESIGN_SHA256,
        "previous_stopped_profile_sha256": PREVIOUS_PROFILE_SHA256,
        "secondary_scale_audit_sha256": _digest(root / "docs/results/v2_secondary_scale_r5.json"),
        "secondary_scale_script_sha256": _digest(root / "scripts/audit_v2_secondary_r5.py"),
        "source_sha256": source_hashes(),
        "environment": {
            "python": sys.version.split()[0], "numpy": numpy.__version__,
            "scipy": scipy.__version__,
            "highs": (f"{highs_core.HIGHS_VERSION_MAJOR}."
                      f"{highs_core.HIGHS_VERSION_MINOR}."
                      f"{highs_core.HIGHS_VERSION_PATCH}"),
            "platform": sys.platform,
        },
        "paths": paths,
        "tasks": expected_tasks(design["cases"]),
        "resource_caps": CAPS,
        "max_initial_problems": 48, "max_rolling_paths": 10,
        "max_initial_mip_phases": 490, "max_rolling_mip_phases": 1030,
        "scope": {
            "candidate_indices": [0], "demand_multiplier": 1.0,
            "prior_severe": 0.5, "major_literal_severe": 1.0,
            "benchmark": "literal", "initial_period": 0,
            "confirmation_case_access": False, "calibration": False,
            "false_notice_paths": False, "payoff_evaluation": False,
            "equilibrium_work": False, "m5_paused": True,
        },
        "saved_phase_fields": [
            "task_index", "period", "phase", "status", "optimal",
            "primal_dual_gap", "relative_gap", "model_dimensions",
            "elapsed_seconds", "peak_rss_mib", "certificate_bytes",
        ],
        "saved_numerical_projection_summary": [
            "adjusted_period_count", "maximum_shipment_correction_l1",
        ],
        "saved_per_period_projection_fields": [
            "task_index", "period", "correction_l1",
        ],
    }


def verify_manifest(root: Path, manifest_path: Path) -> tuple[dict, dict[str, V2DesignCase], str]:
    root, manifest_path = root.resolve(), manifest_path.resolve()
    if manifest_path != root / "configs" / "proposed-v2-resource-profile-r5.json":
        raise ValueError("unexpected resource-profile manifest path")
    raw = manifest_path.read_bytes()
    if canonical(json.loads(raw)) != raw:
        raise ValueError("resource-profile manifest is not canonical JSON")
    supplied = json.loads(raw)
    expected = proposed_manifest(root)
    if supplied != expected:
        raise ValueError("resource-profile manifest differs from frozen public inputs or source")
    previous = root / "results/v2_resource_profile_r4/profile.json"
    if (_digest(previous) != PREVIOUS_PROFILE_SHA256
            or json.loads(previous.read_bytes()).get("status") != "stopped"):
        raise ValueError("previous stopped profile differs from frozen evidence")
    paths = supplied["paths"]
    verification = verify_public_design(
        root, root / paths["allocation"], root / paths["v1_design_manifest"],
        root / paths["public_design"],
    )
    if verification["manifest_sha256"] != DESIGN_SHA256:
        raise ValueError("public design verification returned a different hash")
    allocation, design = load_allocation(root / paths["allocation"],
                                         root / paths["v1_design_manifest"])
    cases = {item.slot_id: item for item in build_public_design(allocation, design)
             if item.candidate_index == 0}
    for task in supplied["tasks"]:
        item = cases[task["slot_id"]]
        if sha256(canonical(asdict(item))).hexdigest() != task["case_sha256"]:
            raise ValueError("profile task case content hash mismatch")
    return supplied, cases, _digest(manifest_path)


def _receipt(path: Path, manifest_sha: str) -> None:
    if not path.is_file():
        raise PermissionError("owner receipt for exact resource-profile manifest is absent")
    raw = path.read_bytes()
    if canonical(json.loads(raw)) != raw or json.loads(raw) != {
        "schema": 1, "decision": "owner_approved_v2_resource_profile_round5",
        "execution_manifest_sha256": manifest_sha,
    }:
        raise PermissionError("owner receipt does not bind exact resource-profile manifest")


def _alarm(_number, _frame) -> None:
    raise TimeoutError("v2 resource-profile wall cap reached")


def _projection_row(task_index: int, period: int, correction_l1: float) -> dict:
    if task_index < 0 or period < 0 or not 0 <= correction_l1 <= planner_v2.POSTSOLVE_TOL:
        raise ValueError("invalid numerical projection record")
    return {"task_index": task_index, "period": period,
            "correction_l1": correction_l1}


def _problem(item: V2DesignCase, configuration: str, state,
             period: int, per_mip_seconds: float) -> PlanningProblem:
    actual = item.long_capacity
    if configuration == "minor_prior_half":
        announced = item.short_capacity
        notice = Notice(announced, "minor structured notice")
        severe = 0.5
    elif configuration == "truthful_long_major":
        announced = item.long_capacity
        notice = Notice(announced, "major structured notice")
        severe = 1.0
    else:
        raise ValueError("unknown fixed profile configuration")
    if period >= item.contract.revelation_period:
        scenarios = (Scenario("long", 1.0, actual),)
    else:
        scenarios = tuple(Scenario(name, probability, schedule)
                          for name, probability, schedule in (
                              ("short", 1 - severe, item.short_capacity),
                              ("long", severe, item.long_capacity),
                          ) if probability > 0)
    return PlanningProblem(item.case.network, item.contract, notice, state,
                           scenarios, actual[period], item.baseline_demand[period],
                           item.baseline_demand, per_mip_seconds)


def run(root: Path, manifest_path: Path) -> dict:
    root = root.resolve()
    manifest, cases, digest = verify_manifest(root, manifest_path)
    paths = manifest["paths"]
    _receipt(root / paths["approval_receipt"], digest)
    output = root / paths["output"]
    if output.exists():
        raise FileExistsError("profile output exists; zero-retry rule prevents overwrite")
    output.parent.mkdir(parents=True, exist_ok=True)
    started = monotonic()
    rows: list[dict] = []
    projection_rows: list[dict] = []
    phase_count = 0
    adjusted_period_count = 0
    maximum_shipment_correction_l1 = 0.0
    original_solve = planner_v2._solve

    def measured_solve(model, objective, time_limit, phase, **kwargs):
        nonlocal phase_count
        if phase_count >= CAPS["maximum_mip_phases"]:
            raise RuntimeError("v2 profile MIP-phase cap reached")
        phase_count += 1
        t0 = monotonic()
        try:
            result, cert = original_solve(model, objective, time_limit, phase, **kwargs)
        except PlannerFailure as failure:
            cert = failure.certificate
            _record(cert, monotonic() - t0)
            raise
        _record(cert, monotonic() - t0)
        return result, cert

    def _record(cert, seconds):
        certificate = asdict(cert)
        rows.append({
            "task_index": task_index, "period": period,
            "phase": cert.phase, "status": cert.status,
            "optimal": cert.optimal,
            "primal_dual_gap": (abs(cert.primal - cert.dual)
                                if cert.primal is not None and cert.dual is not None else None),
            "relative_gap": cert.relative_gap,
            "model_dimensions": {
                "variables": cert.model_variables, "constraints": cert.model_constraints,
                "integer_variables": cert.model_integer_variables,
                "nonzeros": cert.model_nonzeros,
            },
            "elapsed_seconds": seconds, "peak_rss_mib": _rss_mib(),
            "certificate_bytes": len(canonical(certificate)),
        })
        if _rss_mib() > CAPS["process_peak_rss_mib"]:
            raise MemoryError("v2 resource-profile process RSS cap reached")
        if len(canonical({"rows": rows})) > CAPS["output_bytes"]:
            raise RuntimeError("v2 resource-profile output cap reached")

    status = "running"
    error = None
    old_alarm = signal.getsignal(signal.SIGALRM)
    planner_v2._solve = measured_solve
    signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, CAPS["wall_seconds"])
    try:
        for task_index, task in enumerate(manifest["tasks"]):
            item = cases[task["slot_id"]]
            state = initial_state(item.case.network)
            periods = range(1) if task["kind"] == "initial" else range(item.case.network.horizon)
            for period in periods:
                problem = _problem(item, task["configuration"], state, period,
                                   CAPS["per_mip_seconds"])
                choice = planner_v2.plan(problem)
                projection_rows.append(_projection_row(
                    task_index, period, choice.execution_shipment_correction_l1))
                if choice.execution_shipment_correction_l1 > 0:
                    adjusted_period_count += 1
                    maximum_shipment_correction_l1 = max(
                        maximum_shipment_correction_l1,
                        choice.execution_shipment_correction_l1,
                    )
                if task["kind"] == "rolling":
                    state, _ = step_v2(
                        item.case.network, item.contract, problem.notice, state,
                        choice.action,
                        Realization(item.long_capacity[period], item.baseline_demand[period]),
                        revealed_schedule=(item.long_capacity if period == item.contract.revelation_period
                                           else None),
                    )
        status = "completed"
    except (PlannerFailure, ValueError, RuntimeError, MemoryError, TimeoutError) as exc:
        status = "stopped"
        error = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_alarm)
        planner_v2._solve = original_solve
        result = {
            "schema": 1, "status": status, "manifest_sha256": digest,
            "approval_receipt_sha256": _digest(root / paths["approval_receipt"]),
            "phase_count": phase_count, "task_count": len(manifest["tasks"]),
            "elapsed_seconds": monotonic() - started, "peak_rss_mib": _rss_mib(),
            "rows": rows, "error": error,
            "projection_rows": projection_rows,
            "adjusted_period_count": adjusted_period_count,
            "maximum_shipment_correction_l1": maximum_shipment_correction_l1,
            "saved_actions_or_costs": False, "confirmation_case_access": False,
        }
        raw = canonical(result)
        if len(raw) > CAPS["output_bytes"]:
            raise RuntimeError("profile result exceeds output cap")
        output.write_bytes(raw)
    return {"status": status, "manifest_sha256": digest,
            "phase_count": phase_count, "output": str(output)}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.profile_v2")
    parser.add_argument("command", choices=("verify-manifest", "run"))
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "verify-manifest":
        manifest, _, digest = verify_manifest(args.repository_root, args.manifest)
        result = {"manifest_sha256": digest, "task_count": len(manifest["tasks"]),
                  "maximum_mip_phases": manifest["resource_caps"]["maximum_mip_phases"],
                  "new_mip_solves": 0, "approval_receipt_present":
                  (args.repository_root / manifest["paths"]["approval_receipt"]).exists()}
    else:
        result = run(args.repository_root, args.manifest)
    print(json.dumps(result, sort_keys=True))
    if args.command == "run" and result["status"] != "completed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
