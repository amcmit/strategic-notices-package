"""Independent, no-MIP replay of saved v3 truthful-calibration actions."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
import math
from pathlib import Path

from .allocation_v3 import canonical
from .core import Action, Notice, Production, Realization, Shipment
from .core_v3 import initial_state_v3, step_v3
from .networks_v3 import _approved_hash, build_public_case, load_allocation
from .profile_v3 import _receipt


MANIFEST_REL = "configs/proposed-v3-truthful-calibration-execution-r4.json"
RESULT_REL = "results/v3_truthful_calibration_r4/result.json"


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _read_canonical(path: Path) -> dict:
    raw = path.read_bytes()
    value = json.loads(raw)
    if raw != canonical(value):
        raise ValueError(f"noncanonical evidence: {path}")
    return value


def _action(value: dict) -> Action:
    if (set(value) != {"qualify", "primary_order", "spot_order", "production", "shipments"}
            or type(value["qualify"]) is not bool):
        raise ValueError("saved action shape or qualification type changed")
    for name in ("primary_order", "spot_order"):
        if not isinstance(value[name], (int, float)) or not math.isfinite(value[name]):
            raise ValueError("saved action has nonfinite control")
    return Action(
        qualify=value["qualify"], primary_order=value["primary_order"],
        spot_order=value["spot_order"],
        production=tuple(Production(**item) for item in value["production"]),
        shipments=tuple(Shipment(**item) for item in value["shipments"]),
    )


def _replay(path: dict, task: dict, case) -> list[int]:
    if path["path_id"] != task["path_id"] or path["status"] not in ("completed", "failed"):
        raise ValueError("saved path identity or status differs")
    periods = path["periods"]
    if len(periods) > case.network.horizon or (
            path["status"] == "completed" and len(periods) != case.network.horizon):
        raise ValueError("saved path horizon differs")
    actual_type, notice_type = task["cell"].split("/")
    if (actual_type, notice_type) not in (("short", "minor"), ("long", "major")):
        raise ValueError("verifier refuses nontruthful cell")
    schedule = case.short_capacity if actual_type == "short" else case.long_capacity
    notice = Notice(schedule, notice_type)
    state = initial_state_v3(case.network)
    dates = []
    for t, saved in enumerate(periods):
        if saved["period"] != t:
            raise ValueError("saved period sequence differs")
        certificate = saved["certificate"]
        if (certificate["status"] != 0 or certificate["optimal"] is not True
                or not math.isfinite(certificate["primal"])
                or not math.isfinite(certificate["dual"])):
            raise ValueError("saved period lacks an optimal certificate")
        action = _action(saved["action"])
        if action.qualify:
            dates.append(t)
        state, ledger = step_v3(
            case.network, case.contract, notice, state, action,
            Realization(schedule[t], case.baseline_demand[t]),
            revealed_schedule=(schedule if t == case.contract.revelation_period else None),
        )
        if sha256(canonical(asdict(ledger))).hexdigest() != saved["ledger_sha256"]:
            raise ValueError(f"ledger replay differs: {task['path_id']} period {t}")
    if path["status"] == "completed":
        if sha256(canonical(asdict(state))).hexdigest() != path["final_state_sha256"]:
            raise ValueError("final physical state replay differs")
    elif path.get("failure", {}).get("path_id") != task["path_id"]:
        raise ValueError("failed path lacks its full failure record")
    if len(dates) > 1:
        raise ValueError("saved path qualifies more than once")
    return dates


def _assess(index: int, paths: dict[str, list[int]], tasks: list[dict]) -> dict:
    """Independent count and selection implementation; imports no runner code."""
    selected_tasks = [task for task in tasks if task["candidate_index"] == index]
    if len(selected_tasks) != 112:
        raise ValueError("candidate task count differs")
    counts = {cell: {regime: {"selected": 0, "total": 0}
                     for regime in ("R1", "R2", "R3", "R4")}
              for cell in ("short/minor", "long/major")}
    interior = {cell: {"selected": 0, "total": 0}
                for cell in ("short/minor", "long/major")}
    boundary = {}
    exceptions = []
    for task in selected_tasks:
        path_id = task["path_id"]
        dates = paths[path_id]
        yes = bool(dates)
        cell, regime, category = task["cell"], task["regime"], task["category"]
        counts[cell][regime]["total"] += 1
        counts[cell][regime]["selected"] += int(yes)
        if regime == "R1" and category == "interior":
            interior[cell]["total"] += 1
            interior[cell]["selected"] += int(yes)
        if regime == "R1" and category.endswith("_closed"):
            row = boundary.setdefault(task["case_id"], {
                "case_id": task["case_id"], "category": category,
                "family": task["family"], "size": task["size"], "cells": {},
            })
            row["cells"][cell] = {"selected": yes, "qualification_dates": dates}
            if yes:
                exceptions.append({"path_id": path_id,
                                   "reason": "R1 negative-control qualification",
                                   "qualification_dates": dates})
        if regime == "R2" and yes:
            exceptions.append({"path_id": path_id,
                               "reason": "R2 no-backup prediction exception",
                               "qualification_dates": dates})
        if regime == "R3" and not yes:
            exceptions.append({"path_id": path_id,
                               "reason": "R3 backup-use prediction exception",
                               "qualification_dates": []})
    if (any(interior[cell]["total"] != 24 for cell in interior)
            or len(boundary) != 8
            or any(set(row["cells"]) != {"short/minor", "long/major"}
                   for row in boundary.values())
            or any(counts[cell][regime]["total"] != (32 if regime == "R1" else 8)
                   for cell in counts for regime in counts[cell])):
        raise ValueError("independent stratum counts differ")
    return {"candidate_index": index,
            "passed": interior["long/major"]["selected"] >= 12,
            "screen": {"selected": interior["long/major"]["selected"],
                       "total": 24, "floor": 12, "no_upper_cap": True},
            "r1_interior_by_cell": interior, "regime_by_cell": counts,
            "boundary_controls": [boundary[key] for key in sorted(boundary)],
            "descriptive_prediction_exceptions": sorted(
                exceptions, key=lambda x: (x["path_id"], x["reason"])),
            "r4_violation_rule": "none: rarely used has no numeric threshold"}


def verify_result(root: Path, manifest_path: Path, result_path: Path) -> dict:
    root = root.resolve()
    if manifest_path.resolve() != root / MANIFEST_REL or result_path.resolve() != root / RESULT_REL:
        raise ValueError("unexpected calibration verification paths")
    manifest = _read_canonical(manifest_path)
    manifest_sha = _digest(manifest_path)
    for name, expected in manifest["source_sha256"].items():
        if _digest(root / name) != expected:
            raise ValueError(f"source changed: {name}")
    for key, path in (
        ("planning_manifest_sha256", "configs/proposed-v3-truthful-calibration-plan-r4.json"),
        ("protocol_sha256", "docs/V3_SINGLE_SOLVE_R3_PROTOCOL.md"),
        ("allocation_sha256", "configs/proposed-v3-allocation.json"),
        ("public_design_manifest_sha256", "configs/v3-design/manifest.json"),
        ("g1_regression_result_sha256", "results/v3_single_regression_r4/stability.json"),
        ("g1_full_result_sha256", "results/v3_single_full_r4/stability.json"),
    ):
        if _digest(root / path) != manifest[key]:
            raise ValueError(f"bound input changed: {path}")
    if _digest(root / "tests/test_v3_truthful_calibration.py") != manifest["test_sha256"]:
        raise ValueError("calibration selection tests changed")
    plan = _read_canonical(root / "configs/proposed-v3-truthful-calibration-plan-r4.json")
    if (manifest["candidate_order"] != [0, 1, 2]
            or manifest["selection"] != plan["screen"]
            or manifest["operating_configuration"] != plan["operating_configuration"]
            or manifest["resource_caps"] != plan["resource_cap_proposal"]
            or manifest["receipt_decision"] != "owner_approved_v3_truthful_calibration_r4"):
        raise ValueError("calibration scope differs from accepted planning manifest")
    _receipt(root / manifest["paths"]["approval_receipt"],
             manifest["receipt_decision"], manifest_sha)
    allocation = load_allocation(
        root / "configs/proposed-v3-allocation.json",
        root / "configs/design/manifest.json",
        _approved_hash(root / "configs/owner-approvals/v3-allocation.json"),
    )
    rows = {row["case_id"]: row for candidate in allocation["candidates"]
            for row in candidate["cases"]}
    tasks = manifest["tasks"]
    if len(tasks) != 336 or len({x["path_id"] for x in tasks}) != 336:
        raise ValueError("truthful task set differs")
    expected = []
    for path_id in plan["path_ids"]:
        case_id, cell = path_id.split("|", 1)
        if cell not in ("short/minor", "long/major"):
            raise ValueError("planning task contains an untruthful cell")
        row = rows[case_id]
        expected.append({
            "path_id": path_id, "candidate_index": row["candidate_index"],
            "case_id": case_id, "cell": cell, "regime": row["regime"],
            "category": row["category"], "family": row["family"],
            "size": row["size"],
            "case_sha256": plan["public_case_content_sha256"][case_id],
        })
    if tasks != expected:
        raise ValueError("execution tasks differ from independent allocation rebuild")
    result = _read_canonical(result_path)
    if result["execution_manifest_sha256"] != manifest_sha:
        raise ValueError("result binds another execution manifest")
    progress_path = root / manifest["paths"]["progress"]
    if _digest(progress_path) != result["progress_sha256"]:
        raise ValueError("saved action progress hash differs")
    lines = progress_path.read_bytes().splitlines(keepends=True)
    progress = [json.loads(line) for line in lines]
    if any(canonical(row) != line for row, line in zip(progress, lines)):
        raise ValueError("noncanonical path progress")
    if len(progress) > len(tasks) or any(row["path_id"] != task["path_id"]
                                      for row, task in zip(progress, tasks)):
        raise ValueError("progress does not follow bound task order")
    if result["status"] not in ("completed", "stopped"):
        raise ValueError("unknown calibration result status")
    qualifications = {}
    for saved, task in zip(progress, tasks):
        case = build_public_case(rows[task["case_id"]])
        if sha256(canonical(asdict(case))).hexdigest() != task["case_sha256"]:
            raise ValueError("public case content changed")
        qualifications[task["path_id"]] = _replay(saved, task, case)
    completed = sum(row["status"] == "completed" for row in progress)
    failed_rows = [row["failure"] for row in progress if row["status"] == "failed"]
    if (completed != result["completed_paths"]
            or any(row not in result["failures"] for row in failed_rows)
            or result["mip_phases"] > manifest["resource_caps"]["maximum_mip_phases"]
            or result["false_notice_paths"] != 0
            or result["confirmation_access"] is not False):
        raise ValueError("saved progress, failures, or resource scope differ")
    if result["status"] == "stopped":
        if (not result["failures"] or result["assessments"] is not None
                or result["selected_candidate"] is not None
                or result["closed_no_candidate"] is not False):
            raise ValueError("stopped calibration asserts a selection")
        return {"status": "verified_stop", "execution_manifest_sha256": manifest_sha,
                "result_sha256": _digest(result_path),
                "progress_sha256": _digest(progress_path),
                "replayed_path_prefixes": len(qualifications),
                "completed_paths": completed, "failure_count": len(result["failures"]),
                "selected_candidate": None}
    if len(progress) != len(tasks) or result["failures"]:
        raise ValueError("completed calibration lacks every truthful path")
    assessments = [_assess(index, qualifications, tasks) for index in (0, 1, 2)]
    selected = next((item["candidate_index"] for item in assessments if item["passed"]), None)
    if (assessments != result["assessments"] or selected != result["selected_candidate"]
            or result["closed_no_candidate"] != (selected is None)
            or result["completed_paths"] != 336 or result["attempted_paths"] != 336
            or result["mip_phases"] != sum(len(row["periods"]) for row in progress)
            or result["mip_phases"] > manifest["resource_caps"]["maximum_mip_phases"]):
        raise ValueError("independent counts, selection, or scope differ")
    return {"status": "verified", "execution_manifest_sha256": manifest_sha,
            "result_sha256": _digest(result_path), "progress_sha256": _digest(progress_path),
            "replayed_paths": len(qualifications), "selected_candidate": selected,
            "assessments_sha256": sha256(canonical(assessments)).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.verify_calibration_v3_r4")
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    manifest = args.manifest or root / MANIFEST_REL
    result = args.result or root / RESULT_REL
    verified = verify_result(root, manifest, result)
    output = root / "results/v3_truthful_calibration_r4/verification.json"
    if output.exists():
        raise FileExistsError("calibration verification already exists")
    output.write_bytes(canonical(verified))
    print(json.dumps(verified, sort_keys=True))


if __name__ == "__main__":
    main()
