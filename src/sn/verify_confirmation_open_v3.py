"""Independent physical and analysis replay of a completed v3 opening."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
import math
from pathlib import Path

from .allocation_v3 import canonical
from .analysis_v3_frozen import analyze
from .confirmation_open_v3 import DECISION, MANIFEST_REL, verify_manifest
from .core import Notice, Realization
from .core_v3 import initial_state_v3, step_v3
from .opening_audit_v3 import audit_private_set
from .profile_v3 import _receipt
from .verify_false_notice_v3_r3 import _action, _case_row, _same_summary


RESULT_REL = "results/v3_confirmation_opening/result.json"


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict:
    raw = path.read_bytes()
    value = json.loads(raw)
    if raw != canonical(value):
        raise ValueError(f"noncanonical confirmation evidence: {path}")
    return value


def _records(path: Path) -> list[dict]:
    records = []
    for raw in path.read_bytes().splitlines(keepends=True):
        value = json.loads(raw)
        if raw != canonical(value):
            raise ValueError("noncanonical confirmation path record")
        records.append(value)
    return records


def _replay(case, saved: dict) -> tuple:
    cell = saved["cell"]
    if cell not in ("C00", "C10", "C01", "C11"):
        raise ValueError("unknown confirmation cell")
    minor = cell in ("C10", "C11")
    notice = Notice(case.short_capacity if minor else case.long_capacity,
                    "minor" if minor else "major")
    state = initial_state_v3(case.network)
    ledgers = []
    if saved["status"] == "completed" and len(saved["periods"]) != case.network.horizon:
        raise ValueError("completed confirmation path lacks 20 periods")
    for t, period in enumerate(saved["periods"]):
        certificate = period["certificate"]
        if (period["period"] != t or certificate["status"] != 0
                or certificate["optimal"] is not True
                or not math.isfinite(certificate["primal"])
                or not math.isfinite(certificate["dual"])
                or abs(certificate["primal"] - certificate["dual"])
                > 1e-7 * max(1.0, abs(certificate["primal"]), abs(certificate["dual"]))):
            raise ValueError(f"confirmation optimal certificate differs at {t}")
        state, ledger = step_v3(
            case.network, case.contract, notice, state, _action(period["action"]),
            Realization(case.long_capacity[t], case.baseline_demand[t]),
            revealed_schedule=(case.long_capacity if t == case.contract.revelation_period
                               else None))
        if canonical(asdict(ledger)) != canonical(period["ledger"]):
            raise ValueError(f"confirmation ledger replay differs at {t}")
        ledgers.append(ledger)
    if saved["status"] == "completed" and (
            sha256(canonical(asdict(state))).hexdigest() != saved["final_state_sha256"]):
        raise ValueError("confirmation final state replay differs")
    return tuple(ledgers)


def verify_result(root: Path, manifest_path: Path, result_path: Path,
                  private_root: Path) -> dict:
    """Re-audit mounted cases, replay every action, recompute every report."""
    root = root.resolve()
    private_root = private_root.expanduser().resolve()
    if result_path.resolve() != root / RESULT_REL:
        raise ValueError("unexpected confirmation result path")
    manifest, manifest_sha = verify_manifest(root, manifest_path)
    _receipt(root / manifest["paths"]["approval_receipt"], DECISION, manifest_sha)
    metadata = _read(root / "configs/v3-confirmation-public.json")
    cases = audit_private_set(root, private_root, metadata)
    audit_path = root / manifest["paths"]["audit"]
    audit = _read(audit_path)
    if (audit["status"] != "passed" or audit["count"] != 56
            or audit["mip_phases_before_audit"] != 0):
        raise ValueError("all-case audit was not passed before planning")
    result = _read(result_path)
    progress_path = root / manifest["paths"]["progress"]
    progress = _records(progress_path)
    if (result["execution_manifest_sha256"] != manifest_sha
            or result["audit_sha256"] != _digest(audit_path)
            or result["progress_sha256"] != _digest(progress_path)
            or result["private_seed_read"] is not False
            or len(progress) > 224
            or [x["path_id"] for x in progress] !=
               [x["path_id"] for x in manifest["tasks"][:len(progress)]]) :
        raise ValueError("confirmation evidence binding or task order differs")
    completed = 0
    rows = []
    case_paths: dict[str, dict] = {}
    for task, saved in zip(manifest["tasks"], progress):
        if saved["cell"] != task["cell"] or saved["status"] not in ("completed", "failed"):
            raise ValueError("confirmation path label or status differs")
        case = cases[task["case_id"]]
        if (_digest(private_root / (task["case_id"] + ".json")) != task["content_sha256"]
                or not saved["path_id"].startswith(case.case_id + "|")):
            raise ValueError("confirmation path/private-case binding differs")
        ledgers = _replay(case, saved)
        if saved["status"] == "completed":
            completed += 1
            dates = [x["period"] for x in saved["periods"] if x["action"]["qualify"]]
            case_paths.setdefault(case.case_id, {})[task["cell"]] = (ledgers, dates)
            if task["cell"] == "C11":
                rows.append(_case_row({"case_id": case.case_id, "regime": case.regime,
                                       "category": case.category, "family": case.family,
                                       "size": case.size}, case_paths[case.case_id]))
        elif (saved.get("failure", {}).get("path_id") != task["path_id"]
              or not saved["failure"].get("full_error_text")):
            raise ValueError("failed confirmation path lacks full error text")
    caps = manifest["resource_caps"]
    if (completed != result["completed_paths"]
            or result["mip_phases"] > caps["maximum_mip_phases"]
            or result["peak_rss_mib"] > caps["peak_rss_mib"]
            or result["phase_elapsed_seconds_max"] > caps["per_mip_seconds"] + 0.01
            or progress_path.stat().st_size + result_path.stat().st_size > caps["saved_bytes"]):
        raise ValueError("confirmation resource or completion accounting differs")
    if result["status"] == "completed":
        if (completed != 224 or len(rows) != 56 or result["failures"]
                or result["mip_phases"] != 4480
                or result["partial_outcomes"] is not None
                or not _same_summary(result["case_rows"], rows)):
            raise ValueError("completed confirmation result lacks all cases")
        analysis = analyze(rows, purpose="confirmation")
        if not _same_summary(result["analysis_confirmation"], analysis):
            raise ValueError("independent frozen confirmation analysis differs")
    elif result["status"] == "stopped":
        partial = result["partial_outcomes"]
        if (not result["failures"] or result["analysis_confirmation"] is not None
                or result["case_rows"] is not None or not isinstance(partial, dict)
                or partial["status"] != "incomplete_descriptive_no_H1_decision"
                or partial["completed_paths"] != completed
                or partial["fully_crossed_cases"] != len(rows)
                or partial["path_evidence"] != manifest["paths"]["progress"]
                or not _same_summary(partial["fully_crossed_case_rows"], rows)):
            raise ValueError("stopped confirmation result claims scientific analysis")
        analysis = None
    else:
        raise ValueError("unknown confirmation result status")
    return {"status": "verified" if analysis is not None else "verified_stop",
            "execution_manifest_sha256": manifest_sha,
            "result_sha256": _digest(result_path), "audit_sha256": _digest(audit_path),
            "progress_sha256": _digest(progress_path), "replayed_paths": len(progress),
            "completed_paths": completed,
            "frozen_analysis_sha256": sha256(canonical(analysis)).hexdigest()
            if analysis is not None else None, "private_seed_read": False}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.verify_confirmation_open_v3")
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--private-root", required=True, type=Path)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    manifest = args.manifest or root / MANIFEST_REL
    result = args.result or root / RESULT_REL
    report = verify_result(root, manifest, result, args.private_root)
    value = _read(manifest)
    with (root / value["paths"]["verification"]).open("xb") as stream:
        stream.write(canonical(report))
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
