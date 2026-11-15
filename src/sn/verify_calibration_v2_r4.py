"""Independent read-only audit of the v2 truthful calibration artifact.

This module does not import the calibration runner or solve any MIP. It
regenerates public inputs, replays saved actions through the physical core,
checks solver certificates, and recomputes all four candidate conditions.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from math import isclose
from pathlib import Path
from sys import float_info

from .core import Action, Notice, Production, Realization, Shipment, initial_state
from .core_v2 import step_v2
from .networks_v2 import build_public_design, load_allocation, verify_public_design
from .suite import canonical, validate_case


SECONDARY_FLOAT_CLOSED_GAP = 64 * float_info.epsilon

PROTOCOL_SHA256 = "3d45e6a1ca65f4fc2d132e3f4ad70b690c47fe44bb415a6b674383874764bf9d"
R7_PROTOCOL_SHA256 = "ca474ad9694a35d8fe0399e7fefd7fa06afe35c8033649b281c020debe513f74"
R6_PROTOCOL_SHA256 = "c1254791f0e4144ad9f5da4d8317d5e809ba42d64c37d53ed8d723513bf1ee4c"
INITIAL_PROTOCOL_SHA256 = "01c34c636e6732c270078e67186b820af2867093bef16c0f8973c240eba15ff0"
ROUND1_RESULT_SHA256 = "84b14f59361f447df77c647c8dff11ae82bc6e48b25277cf2516fe0e38f57781"
ROUND1_MANIFEST_SHA256 = "3fdd07495871ff9362c5fce3bec59fd5d69c05f995c20f3abc691509f4d44655"
ROUND2_RESULT_SHA256 = "295ccfff160bf9333847382c48b8903496b4c39cdf8116ce646b238860fa7e73"
ROUND2_MANIFEST_SHA256 = "7b3bb6e842e53c4821e28bba7104d836fa47bcf8bb8a831aef0922fbd03db38f"
ROUND3_RESULT_SHA256 = "72ef58e48d23d4ee91f75273a2eb15a54d8cc0bcaf964699a0673e90076bf523"
ROUND3_MANIFEST_SHA256 = "44d5b038c7a0ef3728eb83833c79b26b0146f69d5c5fa769a29bdb71c059ef18"
MEMORY_CAP_MIB = 1280
R5_MANIFEST_SHA256 = "e184137fb7fdcf1bae36d49c30cc028a5bff84b8f7402c4233336be33407f79c"
R5_RESULT_SHA256 = "1a35ac18a36688f462ded047a195f76023f6788f4701add2da6ab63c8a0d27c2"
ALLOCATION_SHA256 = "bed8cdc18ef9e5d6051789c8d482154baafd16ff984df656b9f4dc190174c919"
DESIGN_SHA256 = "7f799ed392a933a3b6d3045b0613d042d7a61b0a53289833d8fcbc966884f9e2"
H1_SHA256 = "ce53ed76774ad1cd003cd2925815ba327e0f1ab4e948092fdcc79d004b7308c0"
AUDIT_SHA256 = "8c4a86b2adec941a7b2b5aad7ffa70a0d5d730b13b48c1b707e86711b0941197"
AUDIT_SCRIPT_SHA256 = "6f24f96b00dda7c0531205ae62211c81569a604557c24f97c456d7de799bf7a2"
BASE_PROPOSAL_SHA256 = "b8db04237d51d43210e456bdcd4a1b2cc9b7c18eb4b633a4cd56163a4eeef582"


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def verify_inputs(root: Path, manifest_path: Path) -> tuple[dict, dict, str]:
    root, manifest_path = root.resolve(), manifest_path.resolve()
    if manifest_path != root / "configs/proposed-v2-truthful-calibration-last.json":
        raise ValueError("unexpected execution manifest path")
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    if raw != canonical(manifest):
        raise ValueError("execution manifest is not canonical")
    checks = (
        ("protocol_sha256", "docs/PROPOSED_V2_PROTOCOL_R8.md", PROTOCOL_SHA256),
        ("allocation_sha256", "configs/proposed-v2-factor-allocation.json", ALLOCATION_SHA256),
        ("public_design_manifest_sha256", "configs/v2-design/manifest.json", DESIGN_SHA256),
        ("resource_profile_manifest_sha256", "configs/proposed-v2-resource-profile-r5.json", R5_MANIFEST_SHA256),
        ("resource_profile_result_sha256", "results/v2_resource_profile_r5/profile.json", R5_RESULT_SHA256),
        ("approved_h1_amendment_sha256", "docs/PROPOSED_V2_H1_AMENDMENT.md", H1_SHA256),
        ("calibration_phase_audit_sha256", "docs/results/v2_calibration_phase_audit.json", AUDIT_SHA256),
        ("calibration_phase_audit_script_sha256", "scripts/audit_v2_calibration_phases.py", AUDIT_SCRIPT_SHA256),
    )
    for field, relative, expected in checks:
        if manifest.get(field) != expected or _digest(root / relative) != expected:
            raise ValueError(f"changed bound input: {field}")
    if (manifest.get("previous_protocol_sha256") != R7_PROTOCOL_SHA256
            or _digest(root / "docs/PROPOSED_V2_PROTOCOL_R7.md") != R7_PROTOCOL_SHA256
            or manifest.get("r6_protocol_sha256") != R6_PROTOCOL_SHA256
            or _digest(root / "docs/PROPOSED_V2_PROTOCOL_R6.md") != R6_PROTOCOL_SHA256
            or manifest.get("initial_protocol_sha256") != INITIAL_PROTOCOL_SHA256
            or _digest(root / "docs/PROPOSED_V2_PROTOCOL.md") != INITIAL_PROTOCOL_SHA256
            or manifest.get("initial_stopped_calibration_result_sha256") != ROUND1_RESULT_SHA256
            or _digest(root / "results/v2_truthful_calibration_execution/result.json") != ROUND1_RESULT_SHA256
            or manifest.get("initial_stopped_calibration_manifest_sha256") != ROUND1_MANIFEST_SHA256
            or _digest(root / "configs/proposed-v2-truthful-calibration-execution.json") !=
            ROUND1_MANIFEST_SHA256
            or manifest.get("round2_stopped_calibration_result_sha256") != ROUND2_RESULT_SHA256
            or _digest(root / "results/v2_truthful_calibration_round2/result.json") != ROUND2_RESULT_SHA256
            or manifest.get("round2_stopped_calibration_manifest_sha256") != ROUND2_MANIFEST_SHA256
            or _digest(root / "configs/proposed-v2-truthful-calibration-round2.json") !=
            ROUND2_MANIFEST_SHA256
            or manifest.get("previous_stopped_calibration_result_sha256") != ROUND3_RESULT_SHA256
            or _digest(root / "results/v2_truthful_calibration_final/result.json") != ROUND3_RESULT_SHA256
            or manifest.get("previous_stopped_calibration_manifest_sha256") != ROUND3_MANIFEST_SHA256
            or _digest(root / "configs/proposed-v2-truthful-calibration-final.json") !=
            ROUND3_MANIFEST_SHA256):
        raise ValueError("stopped calibration provenance changed")
    prior = json.loads((root / "configs/proposed-v2-truthful-calibration-final.json").read_bytes())
    for name, expected in prior["execution_source_sha256"].items():
        if _digest(root / "src/sn" / name) != expected:
            raise ValueError(f"R7 source changed: {name}")
    if _digest(root / "configs/proposed-v2-truthful-calibration-r5.json") != BASE_PROPOSAL_SHA256:
        raise ValueError("base calibration proposal changed")
    r5 = json.loads((root / "configs/proposed-v2-resource-profile-r5.json").read_bytes())
    if manifest.get("r5_source_sha256") != r5["source_sha256"]:
        raise ValueError("R5 source binding differs")
    for name, expected in r5["source_sha256"].items():
        if _digest(root / "src/sn" / name) != expected:
            raise ValueError(f"R5 source changed: {name}")
    for name, expected in manifest.get("execution_source_sha256", {}).items():
        if name not in ("planner_v2_r7.py", "calibration_v2_r4.py", "verify_calibration_v2_r4.py") or \
                _digest(root / "src/sn" / name) != expected:
            raise ValueError(f"execution source changed: {name}")
    if set(manifest["execution_source_sha256"]) != {
            "planner_v2_r7.py", "calibration_v2_r4.py", "verify_calibration_v2_r4.py"}:
        raise ValueError("execution source binding incomplete")
    if (manifest.get("status") != "proposed_execution_pending_exact_owner_approval"
            or manifest.get("runner_status") != "implemented_not_run"
            or manifest.get("execution_authorized") is not False
            or manifest.get("paths") != {
                "approval_receipt": "configs/owner-approvals/v2-truthful-calibration-last.json",
                "output_root": "results/v2_truthful_calibration_last",
            }
            or manifest.get("proposed_resource_caps") != {
                "wall_seconds": 900, "per_mip_seconds": 4,
                "process_peak_rss_mib": MEMORY_CAP_MIB,
                "serialized_output_bytes": 64 * 1024 * 1024, "retries": 0,
            }
            or manifest.get("task_count") != 144 or manifest.get("candidate_order") != [0, 1, 2]
            or manifest.get("maximum_mip_phases") != 13128
            or manifest.get("final_rerun") is not True
            or manifest.get("last_calibration_attempt") is not True
            or manifest.get("closing_rule") !=
            "completion_or_stop_closes_calibration_no_further_retry_under_this_protocol"
            or manifest.get("rerun_sequence") != 4
            or manifest.get("scope", {}).get("confirmation_case_access") is not False
            or manifest.get("scope", {}).get("false_notice_paths") is not False):
        raise ValueError("execution scope changed")
    audit = json.loads((root / "docs/results/v2_calibration_phase_audit.json").read_bytes())
    if (audit["new_mip_solves"] != 0 or audit["path_count"] != 144
            or audit["maximum_mip_phases"] != manifest["maximum_mip_phases"]
            or audit["phase_upper_counts"] != manifest["phase_upper_counts"]):
        raise ValueError("calibration phase cap differs from solve-free audit")
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
    expected_tasks = [(item.candidate_index, item.slot_id, item.case.identifier,
                       sha256(canonical(asdict(item))).hexdigest(), actual, notice)
                      for item in cases.values()
                      for actual, notice in (("long", "major"), ("short", "minor"))]
    actual_tasks = [(row["candidate_index"], row["slot_id"], row["case_id"],
                     row["case_sha256"], row["actual_type"], row["notice_schedule"])
                    for row in manifest["tasks"]]
    if actual_tasks != expected_tasks:
        raise ValueError("execution task list differs from regenerated public design")
    for row in manifest["tasks"]:
        if (row["benchmark"] != "literal" or row["demand_multiplier"] != 1.0
                or row["literal_severe_belief"] != (1.0 if row["actual_type"] == "long" else 0.0)):
            raise ValueError("execution task contains nontruthful receiver configuration")
    return manifest, cases, _digest(manifest_path)


def _action(row: dict) -> Action:
    return Action(
        qualify=row["qualify"], primary_order=row["primary_order"],
        spot_order=row["spot_order"],
        production=tuple(Production(**item) for item in row["production"]),
        shipments=tuple(Shipment(**item) for item in row["shipments"]),
    )


def _check_certificate(cert: dict, phase: str) -> None:
    if cert["phase"] != phase or cert["status"] != 0 or cert["optimal"] is not True:
        raise ValueError("uncertified solver phase in result")
    if (cert["requested_mip_absolute_gap"] != 0
            or cert["requested_relative_gap"] != 0
            or cert["requested_mip_feasibility_tolerance"] != 1e-8
            or cert.get("time_limit_seconds") != 4
            or cert.get("requested_threads") != 1
            or cert.get("requested_random_seed") != 0):
        raise ValueError("solver request differs from frozen R5 settings")
    primal, dual, relative = cert["primal"], cert["dual"], cert["relative_gap"]
    if primal is None or dual is None or relative is None:
        raise ValueError("missing solver bound")
    if abs(primal - dual) > (1e-8 if phase in ("tie_break", "current_tie") else 1e-6):
        raise ValueError("solver absolute bound gap exceeds postsolve limit")
    if relative < 0:
        raise ValueError("negative reported MIP relative gap")
    if (phase in ("tie_break", "current_tie")
            and abs(primal - dual) > SECONDARY_FLOAT_CLOSED_GAP
            and relative > 1e-6):
        raise ValueError("solver secondary relative bound gap exceeds limit")


def _qualification_check(row: dict) -> None:
    cls = row["qualification_classification"]
    branch = row["certificates"]["qualification_branch"]
    primary = row["certificates"]["primary"]
    qualifies = row["action"]["qualify"]
    if cls == "unavailable":
        if branch is not None or qualifies:
            raise ValueError("unavailable qualification row is inconsistent")
        return
    if branch is None:
        raise ValueError("qualification classification lacks branch certificate")
    if cls == "primary_tie":
        if (qualifies or abs(branch["primal"] - primary["primal"]) > 1e-8
                or abs(branch["dual"] - primary["dual"]) > 1e-8):
            raise ValueError("primary tie does not resolve to skip")
    elif cls == "strict_qualify":
        if not qualifies or branch["dual"] <= primary["primal"] + 1e-6:
            raise ValueError("strict qualification lacks bound separation")
    elif cls == "strict_skip":
        if qualifies or branch["dual"] <= primary["primal"] + 1e-6:
            raise ValueError("strict skip lacks bound separation")
    else:
        raise ValueError("unknown qualification classification")


def _expected_current_coordinates(item, state, period: int, schedule: tuple[float, ...]) -> int:
    contract, net = item.contract, item.case.network
    order_due = period + contract.order_lead
    order = int(order_due < net.horizon and
                schedule[order_due] - state.outstanding_for(order_due) > 0)
    spot = int(contract.spot_capacity > 0)
    production = sum(recipe.capacity > 0 for recipe in net.recipes)
    shipments = sum(lane.capacity > 0 and period + lane.lead < net.horizon
                    for lane in net.lanes)
    return order + spot + production + shipments


def _check_rounding_record(row: dict, item) -> None:
    record = row["rounding_certificate"]
    required = {"weight_sum", "analytic_bound", "floating_allowance",
                "unrounded_score", "rounded_score", "certified_band_upper", "effective_upper"}
    if set(record) != required or any(not isinstance(value, (int, float))
                                      for value in record.values()):
        raise ValueError("rounding record missing or malformed")
    recipes, lanes = len(item.case.network.recipes), len(item.case.network.lanes)
    # The secondary model creates one order and one spot variable even if
    # their upper bounds are zero; it also creates every recipe and lane.
    weight_sum = 0.8 + 0.25 * recipes + 0.15 * lanes
    count = 2 + recipes + lanes
    analytic = 0.5e-8 * weight_sum
    band = row["certificates"]["tie_break"]["primal"] + 1e-8
    floating = (2 * (count + 2) * float_info.epsilon
                * max(1.0, abs(band), abs(record["rounded_score"])))
    for key, expected in (("weight_sum", weight_sum), ("analytic_bound", analytic),
                          ("floating_allowance", floating), ("certified_band_upper", band),
                          ("effective_upper", band + analytic + floating)):
        tolerance = 1e-18 if key in ("analytic_bound", "floating_allowance") else 1e-15
        if not isclose(record[key], expected, rel_tol=0, abs_tol=tolerance):
            raise ValueError(f"rounding {key} differs from independently computed bound")
    if (record["unrounded_score"] > band
            or record["rounded_score"] > band + analytic + floating):
        raise ValueError("rounded score exceeds certified allowance")
    action = row["action"]
    action_score = 0.45 * action["primary_order"] + 0.35 * action["spot_order"]
    production = list(action["production"])
    for index, recipe in enumerate(item.case.network.recipes):
        matched = [part for part in production
                   if part["site"] == recipe.site and part["product"] == recipe.output]
        if len(matched) > 1:
            raise ValueError("ambiguous production in score replay")
        if matched:
            action_score += (0.2 + (index + 1) / (10 * (recipes + 1))) * matched[0]["quantity"]
            production.remove(matched[0])
    if production:
        raise ValueError("unmatched production in score replay")
    shipments = {part["lane_index"]: part["quantity"] for part in action["shipments"]}
    if len(shipments) != len(action["shipments"]):
        raise ValueError("duplicate lane in score replay")
    for index in range(lanes):
        action_score += (0.1 + (index + 1) / (10 * (lanes + 1))) * shipments.pop(index, 0.0)
    if shipments:
        raise ValueError("unmatched shipment in score replay")
    # The executed action drops current values below the planner's 1e-7
    # cleanup threshold, and physical reconciliation can reduce shipments.
    # Bound both changes without treating it as the exact pre-reconciliation
    # vector retained in the rounding certificate.
    maximum_ship_weight = 0.1 + lanes / (10 * (lanes + 1)) if lanes else 0.0
    allowed_difference = 1e-7 * weight_sum + maximum_ship_weight * row["correction_l1"] + 1e-12
    if abs(action_score - record["rounded_score"]) > allowed_difference:
        raise ValueError("rounded score differs from independently replayed action")


def _replay_path(path: dict, item, *, complete: bool = True) -> tuple[dict, int]:
    if (path["case_id"] != item.case.identifier
            or path["case_sha256"] != sha256(canonical(asdict(item))).hexdigest()
            or path["family"] != item.case.family
            or path["substitutability"] != 1 + len(item.case.alternative_sources)
            or path["alternate_stock_per_source"] != item.alternate_stock_per_source
            or path["demand_start"] != item.case.demand_start
            or path["long_outage_length"] != item.long_outage_length
            or path["no_disruption_feasible"] != validate_case(item.case)):
        raise ValueError("path public metadata or feasibility differs")
    actual = path["actual_type"]
    if (actual, path["notice_schedule"]) not in (("long", "major"), ("short", "minor")):
        raise ValueError("path is not a truthful cell")
    schedule = item.long_capacity if actual == "long" else item.short_capacity
    notice = Notice(schedule, f"truthful {path['notice_schedule']} structured notice")
    state = initial_state(item.case.network)
    phase_count = 0
    for period, row in enumerate(path["periods"]):
        if row["period"] != period:
            raise ValueError("nonsequential path period")
        certs = row["certificates"]
        for phase in ("primary", "tie_break"):
            _check_certificate(certs[phase], phase)
            phase_count += 1
        if certs["qualification_branch"] is not None:
            _check_certificate(certs["qualification_branch"], "qualification_branch")
            phase_count += 1
        expected_current = _expected_current_coordinates(item, state, period, schedule)
        if len(certs["current_tie"]) != expected_current:
            raise ValueError("current-coordinate certificate count differs from model")
        for cert in certs["current_tie"]:
            _check_certificate(cert, "current_tie")
            phase_count += 1
        _check_rounding_record(row, item)
        _qualification_check(row)
        if not 0 <= row["correction_l1"] <= 1e-7:
            raise ValueError("shipment correction exceeds planner postsolve limit")
        action = _action(row["action"])
        state, ledger = step_v2(
            item.case.network, item.contract, notice, state, action,
            Realization(schedule[period], item.baseline_demand[period]),
            revealed_schedule=(schedule if period == item.contract.revelation_period else None),
        )
        if canonical(row["ledger"]) != canonical(asdict(ledger)):
            raise ValueError("recorded period ledger differs from independent physical replay")
    if complete and len(path["periods"]) != item.case.network.horizon:
        raise ValueError("completed path has wrong horizon")
    if not complete and len(path["periods"]) > item.case.network.horizon:
        raise ValueError("partial path exceeds horizon")
    if not complete:
        expected_partial = []
        for row in path["periods"]:
            expected_partial.append({
                "candidate_index": path["candidate_index"], "case_id": path["case_id"],
                "actual_type": actual, "period": row["period"],
                "correction_l1": row["correction_l1"],
                "qualification_classification": row["qualification_classification"],
                "qualifies_now": row["action"]["qualify"],
                "future_primary_tie_periods": None,
                "future_qualification_periods": None,
                "correction_precedes_tie_or_qualification": None,
                "same_period_tie_or_qualification": bool(
                    row["correction_l1"] > 0 and
                    (row["qualification_classification"] == "primary_tie"
                     or row["action"]["qualify"])),
            })
        if canonical(path["corrections"]) != canonical(expected_partial):
            raise ValueError("partial correction log differs from dated rows")
        return {}, phase_count
    expected_corrections = []
    for row in path["periods"]:
        t = row["period"]
        future_ties = [other["period"] for other in path["periods"]
                       if other["period"] > t and other["qualification_classification"] == "primary_tie"]
        future_qualifications = [other["period"] for other in path["periods"]
                                 if other["period"] > t and other["action"]["qualify"]]
        correction = row["correction_l1"]
        expected_corrections.append({
            "candidate_index": path["candidate_index"], "case_id": path["case_id"],
            "actual_type": actual, "period": t, "correction_l1": correction,
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
    if canonical(path["corrections"]) != canonical(expected_corrections):
        raise ValueError("dated correction log differs from independent recomputation")
    onset = item.case.demand_start - 1
    overlap = sum(row["ledger"]["backup_delivered"] for row in path["periods"]
                  if onset <= row["period"] < onset + item.long_outage_length)
    summary = {
        "candidate_index": path["candidate_index"], "slot_id": path["slot_id"],
        "actual_type": actual, "family": item.case.family,
        "substitutability": 1 + len(item.case.alternative_sources),
        "alternate_stock_per_source": item.alternate_stock_per_source,
        "strict_dates": [row["period"] for row in path["periods"]
                         if row["action"]["qualify"] and row["qualification_classification"] == "strict_qualify"],
        "tie_dates": [row["period"] for row in path["periods"]
                      if row["qualification_classification"] == "primary_tie"],
        "backup_overlap": overlap,
    }
    return summary, phase_count


def _independent_assessment(index: int, rows: list[dict], cases: dict) -> dict:
    long = [row for row in rows if row["candidate_index"] == index and row["actual_type"] == "long"]
    short = [row for row in rows if row["candidate_index"] == index and row["actual_type"] == "short"]
    if len(long) != 24 or len(short) != 24:
        raise ValueError("candidate truthful path count differs")
    strict_long_all = [row for row in long if row["strict_dates"]]
    qualified = [row for row in long if row["strict_dates"] and row["backup_overlap"] > 1e-8]
    short_strict = [row for row in short if row["strict_dates"]]
    levels = {row["substitutability"] for row in qualified}
    families = {row["family"] for row in qualified}
    positive_alt = any(row["substitutability"] >= 2 and
                       (row["alternate_stock_per_source"] or 0) > 0 for row in qualified)
    feasible = {slot: validate_case(item.case) for (candidate, slot), item in cases.items()
                if candidate == index}
    conditions = {
        "1_long_strict_backup_band": {"pass": 8 <= len(qualified) <= 16,
                                      "count": len(qualified),
                                      "slots": [row["slot_id"] for row in qualified],
                                      "strict_qualification_count_before_backup_overlap": len(strict_long_all),
                                      "strict_without_outage_backup_slots": [row["slot_id"]
                                                                            for row in strict_long_all
                                                                            if row not in qualified]},
        "2_structural_coverage": {"pass": {1, 2, 3}.issubset(levels) and
                                  len(families) >= 3 and positive_alt,
                                  "substitutability_levels": sorted(levels),
                                  "families": sorted(families),
                                  "positive_alternate_stock_dual_or_multi": positive_alt},
        "3_short_strict_cap": {"pass": len(short_strict) <= 4,
                               "count": len(short_strict),
                               "slots": [row["slot_id"] for row in short_strict]},
        "4_no_disruption_feasibility_and_certificates": {
            "pass": len(feasible) == 24 and all(feasible.values()),
            "feasible_count": sum(feasible.values()),
            "infeasible_slots": sorted(slot for slot, value in feasible.items() if not value),
            "certificate_rule": "all completed path phases certified; otherwise runner stops before selection"},
    }
    ties = {cell: [{"slot_id": row["slot_id"], "periods": row["tie_dates"]}
                   for row in group if row["tie_dates"]]
            for cell, group in (("long_major", long), ("short_minor", short))}
    return {
        "candidate_index": index, "conditions": conditions,
        "passes": all(value["pass"] for value in conditions.values()),
        "strict_long_count": len(qualified),
        "strict_long_qualification_count": len(strict_long_all),
        "strict_short_count": len(short_strict),
        "skipped_ties": ties,
        "skipped_tie_network_counts": {key: len(value) for key, value in ties.items()},
        "skipped_tie_period_counts": {key: sum(len(item["periods"]) for item in group)
                                      for key, group in ties.items()},
    }


def verify_result(root: Path, manifest_path: Path) -> dict:
    manifest, cases, manifest_sha = verify_inputs(root, manifest_path)
    receipt = root / manifest["paths"]["approval_receipt"]
    expected_receipt = canonical({"schema": 1,
                                  "decision": "owner_approved_v2_truthful_calibration_last",
                                  "execution_manifest_sha256": manifest_sha})
    if receipt.read_bytes() != expected_receipt:
        raise ValueError("approval receipt differs from exact execution manifest")
    output = root / manifest["paths"]["output_root"] / "result.json"
    raw = output.read_bytes()
    result = json.loads(raw)
    if raw != canonical(result) or len(raw) > manifest["proposed_resource_caps"]["serialized_output_bytes"]:
        raise ValueError("result is noncanonical or exceeds output cap")
    if (result["manifest_sha256"] != manifest_sha
            or result["approval_receipt_sha256"] != _digest(receipt)
            or result["confirmation_case_access"] is not False
            or result["phase_count"] > manifest["maximum_mip_phases"]
            or result["elapsed_seconds"] > manifest["proposed_resource_caps"]["wall_seconds"]
            or result["peak_rss_mib"] > manifest["proposed_resource_caps"]["process_peak_rss_mib"]):
        raise ValueError("result provenance or resource cap differs")
    if result["completed_path_count"] != len(result["paths"]) or len(result["paths"]) > 144:
        raise ValueError("completed path count differs from saved path list")
    summaries = []
    phases = 0
    for task, path in zip(manifest["tasks"], result["paths"]):
        if any(path[key] != task[key] for key in ("candidate_index", "slot_id", "case_id",
                                                 "case_sha256", "actual_type", "notice_schedule")):
            raise ValueError("result path differs from task order")
        summary, used = _replay_path(path, cases[(task["candidate_index"], task["slot_id"])])
        summaries.append(summary)
        phases += used
    if result["status"] == "completed":
        if (len(result["paths"]) != 144 or result["partial_path"] is not None
                or result["error"] is not None or result["stop_certificate"] is not None
                or phases != result["phase_count"]):
            raise ValueError("completed result has missing paths or phases")
        assessments = [_independent_assessment(index, summaries, cases) for index in (0, 1, 2)]
        if canonical(assessments) != canonical(result["candidate_assessments"]):
            raise ValueError("candidate condition assessment differs on independent replay")
        selected = next((row["candidate_index"] for row in assessments if row["passes"]), None)
        if selected != result["selected_candidate"]:
            raise ValueError("first-passing-candidate selection differs")
    elif result["status"] == "stopped":
        if (result["error"] is None or result["candidate_assessments"] is not None
                or result["selected_candidate"] is not None):
            raise ValueError("stopped campaign cannot select a candidate")
        partial = result["partial_path"]
        if partial is not None:
            if len(result["paths"]) >= len(manifest["tasks"]):
                raise ValueError("partial path falls outside task list")
            task = manifest["tasks"][len(result["paths"])]
            if any(partial[key] != task[key] for key in ("candidate_index", "slot_id", "case_id",
                                                       "case_sha256", "actual_type", "notice_schedule")):
                raise ValueError("partial path differs from next task")
            _, used = _replay_path(partial, cases[(task["candidate_index"], task["slot_id"])],
                                   complete=False)
            phases += used
        if result["phase_count"] < phases:
            raise ValueError("recorded phase count smaller than verified certificates")
    else:
        raise ValueError("unknown calibration result status")
    return {"status": result["status"], "manifest_sha256": manifest_sha,
            "result_sha256": sha256(raw).hexdigest(),
            "verified_completed_paths": len(summaries),
            "verified_completed_phases": phases,
            "selected_candidate": result["selected_candidate"]}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.verify_calibration_v2_r4")
    parser.add_argument("command", choices=("verify-manifest", "verify-result"))
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "verify-manifest":
        manifest, _, digest = verify_inputs(args.repository_root, args.manifest)
        value = {"manifest_sha256": digest, "task_count": len(manifest["tasks"]),
                 "new_mip_solves": 0}
    else:
        value = verify_result(args.repository_root, args.manifest)
    print(json.dumps(value, sort_keys=True))


if __name__ == "__main__":
    main()
