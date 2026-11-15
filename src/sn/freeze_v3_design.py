"""Freeze the selected public v3 design from saved truthful evidence only."""

from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

from .allocation_v3 import canonical
from .networks_v3 import _approved_hash, load_allocation


OUTPUT = "configs/v3-design/selection-freeze.json"
EXECUTION = "configs/proposed-v3-truthful-calibration-execution-r4.json"
RESULT = "results/v3_truthful_calibration_r4/result.json"
VERIFICATION = "results/v3_truthful_calibration_r4/verification.json"
DESIGN = "configs/v3-design/manifest.json"
ALLOCATION = "configs/proposed-v3-allocation.json"
BASE_PROTOCOL = "docs/V3_SINGLE_SOLVE_R3_PROTOCOL.md"
REPORTING_PROTOCOL = "docs/V3_PROTOCOL_R4_REPORTING_AMENDMENT.md"
RETIREMENT = "configs/v1-confirmation-retirement.json"


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict:
    raw = path.read_bytes()
    value = json.loads(raw)
    if canonical(value) != raw:
        raise ValueError(f"noncanonical freeze input: {path}")
    return value


def expected_freeze(root: Path) -> dict:
    """Derive the selection without solves or new case generation."""
    root = root.resolve()
    execution = _read(root / EXECUTION)
    result = _read(root / RESULT)
    verified = _read(root / VERIFICATION)
    design = _read(root / DESIGN)
    retirement = _read(root / RETIREMENT)
    if (result["status"] != "completed" or result["failures"]
            or result["completed_paths"] != 336
            or result["execution_manifest_sha256"] != _digest(root / EXECUTION)
            or verified["status"] != "verified"
            or verified["result_sha256"] != _digest(root / RESULT)
            or verified["execution_manifest_sha256"] != _digest(root / EXECUTION)
            or verified["selected_candidate"] != result["selected_candidate"]):
        raise ValueError("calibration result was not independently verified")
    if (execution["protocol_sha256"] != _digest(root / BASE_PROTOCOL)
            or execution["allocation_sha256"] != _digest(root / ALLOCATION)
            or execution["public_design_manifest_sha256"] != _digest(root / DESIGN)
            or execution["source_sha256"]["src/sn/planner_v3_single_r3.py"]
            != _digest(root / "src/sn/planner_v3_single_r3.py")):
        raise ValueError("frozen calibration input changed")
    for name, digest in execution["source_sha256"].items():
        if _digest(root / name) != digest:
            raise ValueError(f"calibration source changed: {name}")
    if (retirement != {
            "schema": 1, "kind": "v1_confirmation_retirement",
            "status": "retired_unopened", "case_count": 16,
            "public_metadata_path": "configs/confirmation-public.json",
            "public_metadata_sha256": "c71eff4852c91ade093cecd9741897e58e9eb73ea4890cfe8bec2428c30b7a7f",
            "private_cases_accessed": False, "private_cases_modified": False,
            "private_storage": "owner-controlled encrypted storage; unavailable to agent as reported by owner",
            "scope": "excluded from v3 and all future v1 confirmation analyses absent a new owner decision",
        } or _digest(root / retirement["public_metadata_path"])
            != retirement["public_metadata_sha256"]):
        raise ValueError("v1 retirement provenance changed")
    allocation = load_allocation(root / ALLOCATION,
                                 root / "configs/design/manifest.json",
                                 _approved_hash(root / "configs/owner-approvals/v3-allocation.json"))
    assessments = result["assessments"]
    if (len(assessments) != 3
            or [row["candidate_index"] for row in assessments] != [0, 1, 2]):
        raise ValueError("calibration candidate order changed")
    first = next((row["candidate_index"] for row in assessments if row["passed"]), None)
    if first is None or first != result["selected_candidate"]:
        raise ValueError("selection differs from first-passing rule")
    screen = []
    raw_short_flags = 0
    corrected_long_exceptions = 0
    for row in assessments:
        index = row["candidate_index"]
        long = row["r1_interior_by_cell"]["long/major"]
        short = row["r1_interior_by_cell"]["short/minor"]
        if (row["screen"] != {"selected": long["selected"], "total": 24,
                             "floor": 12, "no_upper_cap": True}
                or long["total"] != 24 or short["total"] != 24
                or row["passed"] != (long["selected"] >= 12)):
            raise ValueError("saved screen does not match declared floor")
        screen.append({"candidate_index": index, "passed": row["passed"],
                       "r1_interior_long_major_selected": long["selected"],
                       "r1_interior_short_minor_selected": short["selected"],
                       "r1_interior_total": 24, "floor": 12})
        for item in row["descriptive_prediction_exceptions"]:
            if item["reason"] == "R3 backup-use prediction exception":
                if item["path_id"].endswith("|short/minor"):
                    raw_short_flags += 1
                elif item["path_id"].endswith("|long/major"):
                    corrected_long_exceptions += 1
                else:
                    raise ValueError("R3 exception has unknown truthful cell")
    if raw_short_flags != 24 or corrected_long_exceptions != 0:
        raise ValueError("R3 reporting-error record differs from saved evidence")
    selected_rows = allocation["candidates"][first]["cases"]
    design_rows = {row["case_id"]: row for row in design["cases"]}
    if (len(selected_rows) != 56 or len(design_rows) != 168
            or set(design_rows) != set(execution["public_case_content_sha256"])):
        raise ValueError("public design case list changed")
    cases = []
    for row in selected_rows:
        case_id = row["case_id"]
        saved = design_rows[case_id]
        case_path = root / "configs/v3-design/cases" / f"candidate-{first}" / f"{case_id}.json"
        if (saved["content_sha256"] != _digest(case_path)
                or saved["content_sha256"] != execution["public_case_content_sha256"][case_id]
                or any(saved[key] != row[key] for key in
                       ("candidate_index", "case_id", "slot_id", "regime",
                        "category", "family", "size"))
                or saved["no_disruption_feasible"] is not True):
            raise ValueError(f"selected public case differs: {case_id}")
        cases.append({"case_id": case_id, "slot_id": row["slot_id"],
                      "regime": row["regime"], "category": row["category"],
                      "family": row["family"], "size": row["size"],
                      "allocation_row_sha256": sha256(canonical(row)).hexdigest(),
                      "content_sha256": saved["content_sha256"]})
    return {
        "schema": 1, "kind": "v3_selected_design_freeze",
        "status": "frozen_selected_design_no_confirmation_generated",
        "selected_candidate": first, "candidate_order": [0, 1, 2],
        "screen_outcomes": screen, "case_count": 56, "cases": cases,
        "regime_counts": dict(sorted(Counter(row["regime"] for row in cases).items())),
        "category_counts": dict(sorted(Counter(row["category"] for row in cases).items())),
        "family_counts": dict(sorted(Counter(row["family"] for row in cases).items())),
        "size_counts": dict(sorted(Counter(row["size"] for row in cases).items())),
        "allocation_sha256": _digest(root / ALLOCATION),
        "public_design_manifest_sha256": _digest(root / DESIGN),
        "base_protocol_sha256": _digest(root / BASE_PROTOCOL),
        "reporting_protocol_amendment_sha256": _digest(root / REPORTING_PROTOCOL),
        "r3_planner_sha256": _digest(root / "src/sn/planner_v3_single_r3.py"),
        "calibration_execution_manifest_sha256": _digest(root / EXECUTION),
        "calibration_result_sha256": _digest(root / RESULT),
        "calibration_verification_sha256": _digest(root / VERIFICATION),
        "v1_retirement_record_sha256": _digest(root / RETIREMENT),
        "v1_public_metadata_sha256": retirement["public_metadata_sha256"],
        "historical_raw_r3_short_minor_flags": raw_short_flags,
        "corrected_r3_long_major_exceptions": corrected_long_exceptions,
        "false_notice_paths": 0, "confirmation_cases_generated": 0,
    }


def verify_freeze(root: Path) -> str:
    path = root.resolve() / OUTPUT
    raw = path.read_bytes()
    if raw != canonical(json.loads(raw)) or json.loads(raw) != expected_freeze(root):
        raise ValueError("selected design freeze differs from saved evidence")
    return sha256(raw).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.freeze_v3_design")
    parser.add_argument("command", choices=("freeze", "verify"))
    parser.add_argument("--repository-root", required=True, type=Path)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    if args.command == "freeze":
        with (root / OUTPUT).open("xb") as out:
            out.write(canonical(expected_freeze(root)))
    print(json.dumps({"selection_freeze_sha256": verify_freeze(root),
                      "selected_candidate": 0, "confirmation_cases_generated": 0},
                     sort_keys=True))


if __name__ == "__main__":
    main()
