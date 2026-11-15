"""Independent core replay and paired-ledger audit of design false notices; no MIPs."""

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
from .freeze_v3_design import verify_freeze
from .networks_v3 import _approved_hash, build_public_case, load_allocation
from .profile_v3 import _receipt
from .confirmation_v3_r2 import verify_metadata


MANIFEST_REL = "configs/proposed-v3-design-false-notice-r1.json"
RESULT_REL = "results/v3_design_false_notice_r1/result.json"
DECISION = "owner_approved_v3_design_false_notice_r1"


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict:
    raw = path.read_bytes()
    value = json.loads(raw)
    if canonical(value) != raw:
        raise ValueError(f"noncanonical evidence: {path}")
    return value


def _action(value: dict) -> Action:
    if (not isinstance(value, dict)
            or set(value) != {"qualify", "primary_order", "spot_order", "production", "shipments"}
            or type(value["qualify"]) is not bool):
        raise ValueError("saved action has wrong shape")
    for key in ("primary_order", "spot_order"):
        if not isinstance(value[key], (int, float)) or not math.isfinite(value[key]):
            raise ValueError("saved continuous action is nonfinite")
    return Action(
        qualify=value["qualify"], primary_order=value["primary_order"],
        spot_order=value["spot_order"],
        production=tuple(Production(**item) for item in value["production"]),
        shipments=tuple(Shipment(**item) for item in value["shipments"]),
    )


def _lines(path: Path) -> list[dict]:
    result = []
    for raw in path.read_bytes().splitlines(keepends=True):
        value = json.loads(raw)
        if canonical(value) != raw:
            raise ValueError(f"noncanonical path record: {path}")
        result.append(value)
    return result


def _replay(case, saved: dict, *, false_notice: bool) -> tuple:
    actual = case.long_capacity
    notice = Notice(case.short_capacity if false_notice else actual,
                    "minor" if false_notice else "major")
    state = initial_state_v3(case.network)
    rows = []
    if saved["status"] == "completed" and len(saved["periods"]) != case.network.horizon:
        raise ValueError("completed path lacks 20 periods")
    if len(saved["periods"]) > case.network.horizon:
        raise ValueError("path exceeds horizon")
    for t, period in enumerate(saved["periods"]):
        cert = period["certificate"]
        if (period["period"] != t or cert["status"] != 0 or cert["optimal"] is not True
                or not math.isfinite(cert["primal"]) or not math.isfinite(cert["dual"]) or
                abs(cert["primal"] - cert["dual"])
                > 1e-7 * max(1.0, abs(cert["primal"]), abs(cert["dual"]))):
            raise ValueError(f"saved optimal certificate differs at period {t}")
        state, ledger = step_v3(
            case.network, case.contract, notice, state, _action(period["action"]),
            Realization(actual[t], case.baseline_demand[t]),
            revealed_schedule=actual if t == case.contract.revelation_period else None,
        )
        if false_notice:
            if asdict(ledger) != period["ledger"]:
                raise ValueError(f"false-notice ledger replay differs at period {t}")
        elif sha256(canonical(asdict(ledger))).hexdigest() != period["ledger_sha256"]:
            raise ValueError(f"truthful ledger replay differs at period {t}")
        rows.append(ledger)
    if saved["status"] == "completed" and (
            sha256(canonical(asdict(state))).hexdigest() != saved["final_state_sha256"]):
        raise ValueError("final physical state replay differs")
    return tuple(rows)


def _paired(case_id: str, truthful: tuple, false: tuple) -> dict:
    # Independent totals and contrasts; this module imports no runner functions.
    def collect(rows):
        return {
            "backup": sum(row.backup_delivered for row in rows),
            "primary": sum(row.primary_delivered for row in rows),
            "accepted_shortfall": sum(row.primary_shortfall for row in rows)
                                  - sum(row.pre_horizon_order_shortfall for row in rows),
            "pre_horizon_shortfall": sum(row.pre_horizon_order_shortfall for row in rows),
            "buyer_cost": sum(row.operating_cost for row in rows),
        }
    truth, lie = collect(truthful), collect(false)
    displacement = truth["backup"] - lie["backup"]
    primary_gain = lie["primary"] - truth["primary"]
    shortfall_gain = lie["accepted_shortfall"] - truth["accepted_shortfall"]
    return {"case_id": case_id, "truth": truth, "false": lie,
            "b": displacement, "delta_d": primary_gain,
            "e": primary_gain - displacement, "s": shortfall_gain,
            "g_3_5": 3 * primary_gain - 5 * shortfall_gain,
            "buyer_harm": lie["buyer_cost"] - truth["buyer_cost"]}


def _same_summary(left, right) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return (set(left) == set(right)
                and all(_same_summary(left[key], right[key]) for key in left))
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _same_summary(a, b) for a, b in zip(left, right))
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isfinite(left) and math.isfinite(right) and math.isclose(
            left, right, rel_tol=1e-12, abs_tol=1e-9)
    return left == right


def verify_result(root: Path, manifest_path: Path, result_path: Path) -> dict:
    root = root.resolve()
    if manifest_path.resolve() != root / MANIFEST_REL or result_path.resolve() != root / RESULT_REL:
        raise ValueError("unexpected false-notice evidence paths")
    manifest = _read(manifest_path)
    manifest_hash = _digest(manifest_path)
    _receipt(root / manifest["paths"]["approval_receipt"], DECISION, manifest_hash)
    if (manifest["receipt_decision"] != DECISION or manifest["physical_cell"] != "long/minor"
            or manifest["truth_reference_cell"] != "long/major"
            or manifest["receiver"] != {"benchmark": "literal",
                "pre_revelation_severe_probability": 0.0,
                "post_revelation": "actual_long_schedule"}
            or manifest["operating_configuration"] != {"demand_multiplier": 1.0,
                "presolve": False, "threads": 1, "rolling_planner": "planner_v3_single_r3"}
            or manifest["scope"] != {"selected_rotation_only": True,
                "false_notice_paths": 56, "new_truthful_paths": 0,
                "confirmation_private_access": False, "sealing": False,
                "m5_paused": True, "retries": 0}
            or manifest["paths"] != {
                "approval_receipt": "configs/owner-approvals/v3-design-false-notice-r1.json",
                "output_root": "results/v3_design_false_notice_r1",
                "attempt_marker": "results/v3_design_false_notice_r1/attempt.started",
                "first_solve_marker": "results/v3_design_false_notice_r1/first_solve.started",
                "progress": "results/v3_design_false_notice_r1/progress.jsonl",
                "result": "results/v3_design_false_notice_r1/result.json",
                "verification": "results/v3_design_false_notice_r1/verification.json"}):
        raise ValueError("false-notice scope differs")
    for key, relative in (
        ("design_freeze_sha256", "configs/v3-design/selection-freeze.json"),
        ("public_design_manifest_sha256", "configs/v3-design/manifest.json"),
        ("allocation_sha256", "configs/proposed-v3-allocation.json"),
        ("truthful_calibration_result_sha256", "results/v3_truthful_calibration_r4/result.json"),
        ("truthful_calibration_verification_sha256", "results/v3_truthful_calibration_r4/verification.json"),
        ("truthful_calibration_progress_sha256", "results/v3_truthful_calibration_r4/progress.jsonl"),
        ("confirmation_public_metadata_sha256", "configs/v3-confirmation-public.json"),
        ("test_sha256", "tests/test_v3_design_false_notice.py"),
    ):
        if manifest[key] != _digest(root / relative):
            raise ValueError(f"bound input changed: {relative}")
    for collection in ("source_sha256", "protocol_sha256"):
        for relative, expected in manifest[collection].items():
            if _digest(root / relative) != expected:
                raise ValueError(f"bound {collection} changed: {relative}")
    if verify_freeze(root) != manifest["design_freeze_sha256"]:
        raise ValueError("selected-design freeze failed verification")
    if verify_metadata(root, root / "configs/v3-confirmation-public.json") != manifest["confirmation_public_metadata_sha256"]:
        raise ValueError("public metadata failed verification")
    freeze = _read(root / "configs/v3-design/selection-freeze.json")
    expected_tasks = [{"path_id": item["case_id"] + "|long/minor",
                       "truth_path_id": item["case_id"] + "|long/major",
                       **item} for item in freeze["cases"]]
    if (manifest["tasks"] != expected_tasks or len(expected_tasks) != 56
            or manifest["resource_caps"]["maximum_mip_phases"] != 1120
            or manifest["resource_caps"]["retries"] != 0):
        raise ValueError("bound tasks or caps differ")
    allocation = load_allocation(
        root / "configs/proposed-v3-allocation.json",
        root / "configs/design/manifest.json",
        _approved_hash(root / "configs/owner-approvals/v3-allocation.json"),
    )
    rows = {x["case_id"]: x for x in allocation["candidates"][0]["cases"]}
    truth_result = _read(root / "results/v3_truthful_calibration_r4/result.json")
    truth_verify = _read(root / "results/v3_truthful_calibration_r4/verification.json")
    if (truth_result["status"] != "completed" or truth_result["failures"]
            or truth_result["selected_candidate"] != 0
            or truth_verify["status"] != "verified"
            or truth_verify["result_sha256"] != manifest["truthful_calibration_result_sha256"]):
        raise ValueError("truthful reference is not independently verified")
    truth_progress = {item["path_id"]: item for item in _lines(
        root / "results/v3_truthful_calibration_r4/progress.jsonl")}
    result = _read(result_path)
    progress_path = root / manifest["paths"]["progress"]
    if (result["execution_manifest_sha256"] != manifest_hash
            or result["progress_sha256"] != _digest(progress_path)
            or result["confirmation_private_access"] is not False):
        raise ValueError("saved result binding differs")
    progress = _lines(progress_path)
    if len(progress) > 56 or [item["path_id"] for item in progress] != [
            item["path_id"] for item in expected_tasks[:len(progress)]]:
        raise ValueError("saved path order or count differs")
    completed = 0
    summaries = []
    for saved, task in zip(progress, expected_tasks):
        case = build_public_case(rows[task["case_id"]])
        if sha256(canonical(asdict(case))).hexdigest() != task["content_sha256"]:
            raise ValueError("reconstructed public case differs")
        if saved["status"] not in ("completed", "failed"):
            raise ValueError("unclosed saved path")
        false_rows = _replay(case, saved, false_notice=True)
        if saved["status"] == "completed":
            truth_saved = truth_progress[task["truth_path_id"]]
            if truth_saved["status"] != "completed":
                raise ValueError("truthful reference not complete")
            truth_rows = _replay(case, truth_saved, false_notice=False)
            summary = _paired(task["case_id"], truth_rows, false_rows)
            if not _same_summary(summary, saved["paired_summary"]):
                raise ValueError("paired ledger totals differ")
            summaries.append(summary)
            completed += 1
        elif (saved.get("failure", {}).get("path_id") != task["path_id"]
              or not saved["failure"].get("full_error_text")):
            raise ValueError("failed path lacks full error text")
    caps = manifest["resource_caps"]
    saved_failures = [item["failure"] for item in progress if item["status"] == "failed"]
    if (completed != result["completed_paths"] or len(progress) != result["attempted_paths"]
            or result["mip_phases"] > caps["maximum_mip_phases"]
            or any(item not in result["failures"] for item in saved_failures)
            or any(not item.get("full_error_text") for item in result["failures"])
            or progress_path.stat().st_size + result_path.stat().st_size > caps["saved_bytes"]):
        raise ValueError("resource, attempt or completion accounting differs")
    if result["status"] == "completed":
        if (completed != 56 or result["mip_phases"] != 1120 or result["failures"]
                or result["peak_rss_mib"] > caps["peak_rss_mib"]
                or result["phase_elapsed_seconds_max"] > caps["per_mip_seconds"] + 0.01
                or not _same_summary(result["paired_summaries"], summaries)):
            raise ValueError("completed result lacks all paired paths")
    elif result["status"] == "stopped":
        if not result["failures"] or result["paired_summaries"] is not None:
            raise ValueError("stopped result asserts complete outcomes")
    else:
        raise ValueError("unknown result status")
    return {"status": "verified" if result["status"] == "completed" else "verified_stop",
            "execution_manifest_sha256": manifest_hash,
            "result_sha256": _digest(result_path), "progress_sha256": _digest(progress_path),
            "replayed_false_paths": len(progress), "paired_complete_paths": completed,
            "confirmation_private_access": False}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.verify_false_notice_v3_r1")
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    output = verify_result(root, args.manifest or root / MANIFEST_REL,
                           args.result or root / RESULT_REL)
    print(json.dumps(output, sort_keys=True))


if __name__ == "__main__":
    main()
