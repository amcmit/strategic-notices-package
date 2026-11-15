"""Approval-gated truthful-only v2 calibration; never opens confirmation cases."""

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

from . import planner_v2_r6 as planner_v2
from .core import Notice, Realization, initial_state
from .core_v2 import step_v2
from .networks_v2 import build_public_design, load_allocation, verify_public_design
from .planner_v2_r6 import PlanningProblem, PlannerFailure, Scenario
from .suite import canonical, validate_case


ACCEPTED_PROTOCOL_SHA256 = "c1254791f0e4144ad9f5da4d8317d5e809ba42d64c37d53ed8d723513bf1ee4c"
PREVIOUS_PROTOCOL_SHA256 = "01c34c636e6732c270078e67186b820af2867093bef16c0f8973c240eba15ff0"
STOPPED_RESULT_SHA256 = "84b14f59361f447df77c647c8dff11ae82bc6e48b25277cf2516fe0e38f57781"
PREVIOUS_EXECUTION_MANIFEST_SHA256 = "3fdd07495871ff9362c5fce3bec59fd5d69c05f995c20f3abc691509f4d44655"
BASE_PROPOSAL_SHA256 = "b8db04237d51d43210e456bdcd4a1b2cc9b7c18eb4b633a4cd56163a4eeef582"
R5_MANIFEST_SHA256 = "e184137fb7fdcf1bae36d49c30cc028a5bff84b8f7402c4233336be33407f79c"
R5_RESULT_SHA256 = "1a35ac18a36688f462ded047a195f76023f6788f4701add2da6ab63c8a0d27c2"
MANIFEST_REL = "configs/proposed-v2-truthful-calibration-round2.json"


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _rss_mib() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def expected_manifest(root: Path) -> dict:
    base_path = root / "configs/proposed-v2-truthful-calibration-r5.json"
    if digest(base_path) != BASE_PROPOSAL_SHA256:
        raise ValueError("calibration scope proposal changed")
    base = json.loads(base_path.read_bytes())
    r5_path = root / "configs/proposed-v2-resource-profile-r5.json"
    if digest(r5_path) != R5_MANIFEST_SHA256:
        raise ValueError("R5 execution manifest changed")
    r5 = json.loads(r5_path.read_bytes())
    if (digest(root / "results/v2_resource_profile_r5/profile.json") != R5_RESULT_SHA256
            or digest(root / "docs/PROPOSED_V2_PROTOCOL.md") != PREVIOUS_PROTOCOL_SHA256
            or digest(root / "docs/PROPOSED_V2_PROTOCOL_R6.md") != ACCEPTED_PROTOCOL_SHA256
            or digest(root / "results/v2_truthful_calibration_execution/result.json") != STOPPED_RESULT_SHA256
            or digest(root / "configs/proposed-v2-truthful-calibration-execution.json") !=
            PREVIOUS_EXECUTION_MANIFEST_SHA256):
        raise ValueError("accepted profile or protocol changed")
    for name, expected in r5["source_sha256"].items():
        if digest(root / "src/sn" / name) != expected:
            raise ValueError(f"R5 source changed: {name}")
    if base["protocol_sha256"] != PREVIOUS_PROTOCOL_SHA256:
        raise ValueError("base proposal protocol changed")
    result = dict(base)
    result.update({
        "status": "proposed_execution_pending_exact_owner_approval",
        "runner_status": "implemented_not_run",
        "protocol_sha256": ACCEPTED_PROTOCOL_SHA256,
        "previous_protocol_sha256": PREVIOUS_PROTOCOL_SHA256,
        "previous_stopped_calibration_result_sha256": STOPPED_RESULT_SHA256,
        "previous_stopped_calibration_manifest_sha256": PREVIOUS_EXECUTION_MANIFEST_SHA256,
        "runner_command": (
            "PYTHONPATH=src python3 -m sn.calibration_v2_r2 run "
            "--repository-root \"$PWD\" "
            "--manifest configs/proposed-v2-truthful-calibration-round2.json"
        ),
        "r5_source_sha256": r5["source_sha256"],
        "execution_source_sha256": {
            name: digest(root / "src/sn" / name)
            for name in ("planner_v2_r6.py", "calibration_v2_r2.py",
                         "verify_calibration_v2_r2.py")
        },
        "preconditions_before_execution": [
            "owner approval receipt naming the exact SHA-256 of this manifest",
            "all bound public inputs, source files and R5 evidence verify",
            "output root absent; zero retries",
        ],
        "paths": {
            "approval_receipt": "configs/owner-approvals/v2-truthful-calibration-round2.json",
            "output_root": "results/v2_truthful_calibration_round2",
        },
    })
    return result


def verify_manifest(root: Path, path: Path) -> tuple[dict, dict[tuple[int, str], object], str]:
    root, path = root.resolve(), path.resolve()
    if path != root / MANIFEST_REL:
        raise ValueError("unexpected calibration manifest path")
    raw = path.read_bytes()
    if canonical(json.loads(raw)) != raw:
        raise ValueError("calibration manifest is not canonical JSON")
    supplied = json.loads(raw)
    if supplied != expected_manifest(root):
        raise ValueError("calibration manifest differs from bound inputs or sources")
    if (supplied["execution_authorized"] is not False
            or supplied["task_count"] != 144
            or supplied["maximum_mip_phases"] != 13128
            or supplied["candidate_order"] != [0, 1, 2]):
        raise ValueError("calibration scope is malformed")
    verify_public_design(
        root, root / "configs/proposed-v2-factor-allocation.json",
        root / "configs/design/manifest.json", root / "configs/v2-design",
    )
    allocation, design = load_allocation(
        root / "configs/proposed-v2-factor-allocation.json",
        root / "configs/design/manifest.json",
    )
    cases = {(item.candidate_index, item.slot_id): item
             for item in build_public_design(allocation, design)}
    for task in supplied["tasks"]:
        item = cases[(task["candidate_index"], task["slot_id"])]
        if (task["case_id"] != item.case.identifier
                or task["case_sha256"] != sha256(canonical(asdict(item))).hexdigest()
                or (task["actual_type"], task["notice_schedule"]) not in
                (("long", "major"), ("short", "minor"))
                or task["demand_multiplier"] != 1.0
                or task["benchmark"] != "literal"
                or task["literal_severe_belief"] != (1.0 if task["actual_type"] == "long" else 0.0)):
            raise ValueError("calibration task differs from frozen public case")
    return supplied, cases, digest(path)


def _receipt(path: Path, manifest_sha256: str) -> None:
    if not path.is_file():
        raise PermissionError("exact owner calibration approval receipt is absent")
    raw = path.read_bytes()
    required = {
        "schema": 1,
        "decision": "owner_approved_v2_truthful_calibration_round2",
        "execution_manifest_sha256": manifest_sha256,
    }
    if raw != canonical(required):
        raise PermissionError("owner calibration receipt does not bind exact manifest")


def _alarm(_number, _frame) -> None:
    raise TimeoutError("calibration wall cap reached")


def _path_summary(path: dict) -> dict:
    periods = path["periods"]
    strict_dates = [row["period"] for row in periods
                    if row["action"]["qualify"] and row["qualification_classification"] == "strict_qualify"]
    tie_dates = [row["period"] for row in periods
                 if row["qualification_classification"] == "primary_tie"]
    onset = path["demand_start"] - 1
    backup_overlap = sum(row["ledger"]["backup_delivered"] for row in periods
                         if onset <= row["period"] < onset + path["long_outage_length"])
    return {
        "candidate_index": path["candidate_index"], "slot_id": path["slot_id"],
        "actual_type": path["actual_type"], "family": path["family"],
        "substitutability": path["substitutability"],
        "alternate_stock_per_source": path["alternate_stock_per_source"],
        "strict_qualification_dates": strict_dates,
        "skipped_tie_dates": tie_dates,
        "backup_units_during_long_outage": backup_overlap,
    }


def assess_candidate(index: int, summaries: list[dict], feasibility: dict[str, bool]) -> dict:
    """Pure truthful-only screening; all four conditions are reported."""
    selected = [row for row in summaries if row["candidate_index"] == index]
    long = [row for row in selected if row["actual_type"] == "long"]
    short = [row for row in selected if row["actual_type"] == "short"]
    if (len(long) != 24 or len(short) != 24
            or len({row["slot_id"] for row in long}) != 24
            or {row["slot_id"] for row in long} != {row["slot_id"] for row in short}):
        raise ValueError("candidate lacks its complete truthful pair set")
    strict_long_all = [row for row in long if row["strict_qualification_dates"]]
    qualified = [row for row in long
                 if row["strict_qualification_dates"] and row["backup_units_during_long_outage"] > 1e-8]
    strict_short = [row for row in short if row["strict_qualification_dates"]]
    topologies = {row["substitutability"] for row in qualified}
    families = {row["family"] for row in qualified}
    alternate = any(row["substitutability"] in (2, 3)
                    and (row["alternate_stock_per_source"] or 0) > 0
                    for row in qualified)
    feasible = [slot for slot, value in sorted(feasibility.items()) if value]
    if len(feasibility) != 24 or set(feasibility) != {row["slot_id"] for row in long}:
        raise ValueError("candidate feasibility coverage is incomplete")
    conditions = {
        "1_long_strict_backup_band": {
            "pass": 8 <= len(qualified) <= 16, "count": len(qualified),
            "slots": [row["slot_id"] for row in qualified],
            "strict_qualification_count_before_backup_overlap": len(strict_long_all),
            "strict_without_outage_backup_slots": [row["slot_id"] for row in strict_long_all
                                                    if row not in qualified],
        },
        "2_structural_coverage": {
            "pass": {1, 2, 3}.issubset(topologies) and len(families) >= 3 and alternate,
            "substitutability_levels": sorted(topologies),
            "families": sorted(families), "positive_alternate_stock_dual_or_multi": alternate,
        },
        "3_short_strict_cap": {
            "pass": len(strict_short) <= 4, "count": len(strict_short),
            "slots": [row["slot_id"] for row in strict_short],
        },
        "4_no_disruption_feasibility_and_certificates": {
            "pass": len(feasible) == 24, "feasible_count": len(feasible),
            "infeasible_slots": sorted(set(feasibility) - set(feasible)),
            "certificate_rule": "all completed path phases certified; otherwise runner stops before selection",
        },
    }
    ties = {
        cell: [{"slot_id": row["slot_id"], "periods": row["skipped_tie_dates"]}
               for row in cell_rows if row["skipped_tie_dates"]]
        for cell, cell_rows in (("long_major", long), ("short_minor", short))
    }
    return {
        "candidate_index": index, "conditions": conditions,
        "passes": all(row["pass"] for row in conditions.values()),
        "strict_long_count": len(qualified),
        "strict_long_qualification_count": len(strict_long_all),
        "strict_short_count": len(strict_short),
        "skipped_ties": ties,
        "skipped_tie_network_counts": {key: len(value) for key, value in ties.items()},
        "skipped_tie_period_counts": {key: sum(len(row["periods"]) for row in value)
                                      for key, value in ties.items()},
    }


def select_first_passing(assessments: list[dict]) -> int | None:
    if [row["candidate_index"] for row in assessments] != [0, 1, 2]:
        raise ValueError("candidate assessments must be in frozen order 0, 1, 2")
    return next((row["candidate_index"] for row in assessments if row["passes"]), None)


def _correction_log(path: dict) -> list[dict]:
    rows = path["periods"]
    result = []
    for row in rows:
        t = row["period"]
        future_ties = [x["period"] for x in rows if x["period"] > t
                       and x["qualification_classification"] == "primary_tie"]
        future_qualifications = [x["period"] for x in rows if x["period"] > t
                                 and x["action"]["qualify"]]
        correction = row["correction_l1"]
        result.append({
            "candidate_index": path["candidate_index"], "case_id": path["case_id"],
            "actual_type": path["actual_type"], "period": t,
            "correction_l1": correction,
            "qualification_classification": row["qualification_classification"],
            "qualifies_now": row["action"]["qualify"],
            "future_primary_tie_periods": future_ties,
            "future_qualification_periods": future_qualifications,
            "correction_precedes_tie_or_qualification": bool(correction > 0 and
                                                              (future_ties or future_qualifications)),
            "same_period_tie_or_qualification": bool(correction > 0 and
                                                      (row["qualification_classification"] == "primary_tie"
                                                       or row["action"]["qualify"])),
        })
    return result


def run(root: Path, manifest_path: Path) -> dict:
    root = root.resolve()
    manifest, cases, manifest_sha = verify_manifest(root, manifest_path)
    _receipt(root / manifest["paths"]["approval_receipt"], manifest_sha)
    output_root = root / manifest["paths"]["output_root"]
    if output_root.exists():
        raise FileExistsError("calibration output exists; zero-retry rule forbids rerun")
    caps = manifest["proposed_resource_caps"]
    started = monotonic()
    paths: list[dict] = []
    phase_count = 0
    status, error, stop_certificate = "running", None, None
    original_solve = planner_v2._solve

    def measured_solve(model, objective, time_limit, phase, **kwargs):
        nonlocal phase_count
        if phase_count >= manifest["maximum_mip_phases"]:
            raise RuntimeError("calibration MIP-phase cap reached")
        phase_count += 1
        solution, certificate = original_solve(model, objective, time_limit, phase, **kwargs)
        if _rss_mib() > caps["process_peak_rss_mib"]:
            raise MemoryError("calibration process RSS cap reached")
        return solution, certificate

    old_alarm = signal.getsignal(signal.SIGALRM)
    planner_v2._solve = measured_solve
    signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, caps["wall_seconds"])
    active_path: dict | None = None
    try:
        feasibility = {
            (item.candidate_index, item.slot_id): validate_case(item.case)
            for item in cases.values()
        }
        for task in manifest["tasks"]:
            item = cases[(task["candidate_index"], task["slot_id"])]
            schedule = item.long_capacity if task["actual_type"] == "long" else item.short_capacity
            notice = Notice(schedule, f"truthful {task['notice_schedule']} structured notice")
            state = initial_state(item.case.network)
            active_path = {
                "candidate_index": item.candidate_index, "slot_id": item.slot_id,
                "case_id": item.case.identifier, "case_sha256": task["case_sha256"],
                "actual_type": task["actual_type"], "notice_schedule": task["notice_schedule"],
                "family": item.case.family,
                "substitutability": 1 + len(item.case.alternative_sources),
                "alternate_stock_per_source": item.alternate_stock_per_source,
                "demand_start": item.case.demand_start,
                "long_outage_length": item.long_outage_length,
                "no_disruption_feasible": feasibility[(item.candidate_index, item.slot_id)],
                "periods": [],
            }
            for period in range(item.case.network.horizon):
                problem = PlanningProblem(
                    item.case.network, item.contract, notice, state,
                    (Scenario(task["actual_type"], 1.0, schedule),),
                    schedule[period], item.baseline_demand[period],
                    item.baseline_demand, caps["per_mip_seconds"],
                )
                choice = planner_v2.plan(problem)
                state, ledger = step_v2(
                    item.case.network, item.contract, notice, state, choice.action,
                    Realization(schedule[period], item.baseline_demand[period]),
                    revealed_schedule=(schedule if period == item.contract.revelation_period else None),
                )
                active_path["periods"].append({
                    "period": period, "action": asdict(choice.action),
                    "ledger": asdict(ledger),
                    "qualification_classification": choice.qualification_classification,
                    "correction_l1": choice.execution_shipment_correction_l1,
                    "rounding_certificate": {
                        "weight_sum": choice.rounding_weight_sum,
                        "analytic_bound": choice.rounding_analytic_bound,
                        "floating_allowance": choice.rounding_fp_allowance,
                        "unrounded_score": choice.unrounded_tie_score,
                        "rounded_score": choice.rounded_tie_score,
                        "certified_band_upper": choice.certified_tie_band_upper,
                        "effective_upper": (choice.certified_tie_band_upper
                                            + choice.rounding_analytic_bound
                                            + choice.rounding_fp_allowance),
                    },
                    "certificates": {
                        "primary": asdict(choice.primary_certificate),
                        "qualification_branch": (asdict(choice.qualification_branch_certificate)
                                                 if choice.qualification_branch_certificate else None),
                        "tie_break": asdict(choice.tie_certificate),
                        "current_tie": [asdict(cert) for cert in choice.current_tie_certificates],
                    },
                })
            active_path["corrections"] = _correction_log(active_path)
            if len(canonical({"paths": [*paths, active_path]})) > caps["serialized_output_bytes"] - 1_000_000:
                raise RuntimeError("calibration serialized output cap reserve reached")
            paths.append(active_path)
            active_path = None
        summaries = [_path_summary(path) for path in paths]
        assessments = [assess_candidate(
            index, summaries,
            {item.slot_id: feasibility[(index, item.slot_id)]
             for item in cases.values() if item.candidate_index == index},
        ) for index in manifest["candidate_order"]]
        selected = select_first_passing(assessments)
        status = "completed"
    except Exception as exc:
        status = "stopped"
        error = {"type": type(exc).__name__, "message": str(exc)}
        if isinstance(exc, PlannerFailure):
            stop_certificate = asdict(exc.certificate)
        if active_path is not None:
            # A truncated path cannot certify the absence of future ties or
            # qualifications. Preserve dated corrections with unknown flags.
            active_path["corrections"] = [{
                "candidate_index": active_path["candidate_index"],
                "case_id": active_path["case_id"],
                "actual_type": active_path["actual_type"],
                "period": row["period"], "correction_l1": row["correction_l1"],
                "qualification_classification": row["qualification_classification"],
                "qualifies_now": row["action"]["qualify"],
                "future_primary_tie_periods": None,
                "future_qualification_periods": None,
                "correction_precedes_tie_or_qualification": None,
                "same_period_tie_or_qualification": bool(
                    row["correction_l1"] > 0 and
                    (row["qualification_classification"] == "primary_tie"
                     or row["action"]["qualify"])),
            } for row in active_path["periods"]]
        assessments, selected = None, None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_alarm)
        planner_v2._solve = original_solve
    result = {
        "schema": 1, "status": status, "error": error,
        "stop_certificate": stop_certificate,
        "manifest_sha256": manifest_sha,
        "approval_receipt_sha256": digest(root / manifest["paths"]["approval_receipt"]),
        "elapsed_seconds": monotonic() - started, "peak_rss_mib": _rss_mib(),
        "phase_count": phase_count, "completed_path_count": len(paths),
        "paths": paths, "partial_path": active_path,
        "candidate_assessments": assessments, "selected_candidate": selected,
        "confirmation_case_access": False,
    }
    raw = canonical(result)
    if len(raw) > caps["serialized_output_bytes"]:
        # Preserve a bounded stop record without any oversized path payload.
        result.update(status="stopped", error={"type": "OutputCap",
                      "message": "calibration serialized output cap reached"},
                      partial_path=None, candidate_assessments=None,
                      selected_candidate=None)
        while len(canonical(result)) > caps["serialized_output_bytes"] and result["paths"]:
            result["paths"].pop()
        result["completed_path_count"] = len(result["paths"])
        raw = canonical(result)
    output_root.mkdir(parents=True, exist_ok=False)
    (output_root / "result.json").write_bytes(raw)
    return {"status": result["status"], "manifest_sha256": manifest_sha,
            "phase_count": phase_count, "completed_path_count": result["completed_path_count"],
            "output": str(output_root / "result.json")}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.calibration_v2_r2")
    parser.add_argument("command", choices=("verify-manifest", "run"))
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "verify-manifest":
        manifest, _, digest_value = verify_manifest(args.repository_root, args.manifest)
        result = {"manifest_sha256": digest_value, "task_count": len(manifest["tasks"]),
                  "maximum_mip_phases": manifest["maximum_mip_phases"],
                  "new_mip_solves": 0,
                  "approval_receipt_present": (args.repository_root /
                                               manifest["paths"]["approval_receipt"]).exists()}
    else:
        result = run(args.repository_root, args.manifest)
    print(json.dumps(result, sort_keys=True))
    if args.command == "run" and result["status"] != "completed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
