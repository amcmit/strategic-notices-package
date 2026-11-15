"""R4 manifest-gated v3 single-solve regression and stability.

All public cases and both truthful cells run in four fresh solver processes.
Raw actions and physical ledgers cross IPC for comparison only. Persistent
progress rows contain hashes, certificate summaries, correction dates, and
full failure text, never action or payoff levels.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import traceback
from time import monotonic

from . import planner_v3_single_r3 as planner_v3, profile_v3
from .allocation_v3 import canonical
from .core import Notice
from .networks_v3 import (
    _approved_hash, build_public_case, load_allocation, verify_public_design,
)
from .planner_v3_single_r3 import PlannerFailure


CONFIGURATIONS = (
    {"name": "presolve-off-threads-1", "presolve": False, "threads": 1},
    {"name": "presolve-off-threads-2", "presolve": False, "threads": 2},
    {"name": "presolve-on-threads-1", "presolve": True, "threads": 1},
    {"name": "presolve-on-threads-2", "presolve": True, "threads": 2},
)
PREFLIGHT_WALL_SECONDS_PER_WORKER = 30
DELTA = 1e-6
LEDGER_BUDGET = 1e-3
M_LIMIT = 50
DIAGNOSTIC_RESULT_SHA256 = "d47f1def11699aac4d9c95e3aec58c100629257b004d68a19a88a1acae592805"
R3_PREFLIGHT_SHA256 = "bb33d879c2d197e95ed9be050e46cbc425f7c41d552d849f3d9723c3836e6ece"
R3_RECEIPT_SHA256 = "fc6d1bdf455afb7699dcc4a925ff34aa98a5a3db2d7d7c37b597669aa1dccb20"
CAPS_BY_KIND = {
    "regression": {
        "per_mip_seconds": 30, "global_wall_seconds": 300,
        "per_config_peak_rss_mib": 512, "saved_bytes": 16777216,
        "maximum_paths": 116, "maximum_rolling_decisions": 2320,
        "maximum_mip_phases": 2320, "maximum_mip_phases_per_config": 580,
        "retries": 0,
    },
    "full": {
        "per_mip_seconds": 30, "global_wall_seconds": 1800,
        "per_config_peak_rss_mib": 512, "saved_bytes": 67108864,
        "maximum_paths": 1344, "maximum_rolling_decisions": 26880,
        "maximum_mip_phases": 26880, "maximum_mip_phases_per_config": 6720,
        "retries": 0,
    },
}
MANIFESTS = {
    "regression": "configs/proposed-v3-single-regression-r4.json",
    "full": "configs/proposed-v3-single-stability-r4.json",
}


def _receipt_decision(kind: str) -> str:
    """One decision identifier for parent and worker receipt checks."""
    if kind not in MANIFESTS:
        raise ValueError("unknown v3 single-solve workload")
    return f"owner_approved_v3_single_{kind}_r4"


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def canonical_action_rows(rows: list[dict]) -> bytes:
    """Normalize zero and numeric types; retain every nonzero float exactly.

    Signed zero is physically identical but serialized differently by JSON.
    No rounding is applied, so a nonzero continuous difference remains visible.
    """
    normalized = []
    for row in rows:
        if type(row.get("qualify")) is not bool:
            raise ValueError("action digest requires a Boolean qualification")
        clean = {"qualify": row["qualify"]}
        for key, value in row.items():
            if key == "qualify":
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"action digest has nonnumeric control: {key}")
            number = float(value)
            if not math.isfinite(number):
                raise ValueError(f"action digest has nonfinite control: {key}")
            clean[key] = 0.0 if number == 0.0 else number
        normalized.append(clean)
    return canonical(normalized)


def coefficient_l1_upper(case) -> float:
    """Conservative sum over all current-action row coefficients."""
    network = case.network
    return (2.0  # accepted primary order and immediate spot control
            + sum(1.0 + sum(coefficient for _, coefficient in recipe.inputs)
                  for recipe in network.recipes)
            + 2.0 * len(network.lanes))


def proposed_manifest(root: Path, kind: str) -> dict:
    if kind not in CAPS_BY_KIND:
        raise ValueError("unknown v3 single-solve workload")
    root = root.resolve()
    r3_preflight_path = root / "results/v3_single_regression_r3/preflight.json"
    r3_receipt_path = root / "configs/owner-approvals/v3-single-regression-r3.json"
    if (_digest(r3_preflight_path) != R3_PREFLIGHT_SHA256
            or _digest(r3_receipt_path) != R3_RECEIPT_SHA256):
        raise ValueError("R3 zero-solve stop or receipt changed")
    r3_preflight = json.loads(r3_preflight_path.read_bytes())
    if (r3_preflight.get("status") != "stopped_zero_solve"
            or r3_preflight.get("mip_solves") != 0
            or r3_preflight.get("approval_consumed") is not False):
        raise ValueError("R3 preflight is not a zero-solve unconsumed stop")
    allocation_path = root / "configs/proposed-v3-allocation.json"
    slot_path = root / "configs/design/manifest.json"
    approval_path = root / "configs/owner-approvals/v3-allocation.json"
    allocation = load_allocation(allocation_path, slot_path,
                                 _approved_hash(approval_path))
    rows = [row for candidate in allocation["candidates"]
            for row in candidate["cases"]]
    ids = [row["case_id"] for row in rows]
    if len(ids) != len(set(ids)) or len(ids) != 168:
        raise ValueError("single-solve study requires 168 distinct public cases")
    design_path = root / "configs/v3-design/manifest.json"
    design_raw = design_path.read_bytes()
    design = json.loads(design_raw)
    if canonical(design) != design_raw or design["allocation_sha256"] != _digest(allocation_path):
        raise ValueError("public design differs from approved allocation")
    hashes = {row["case_id"]: row["content_sha256"] for row in design["cases"]}
    if set(ids) != set(hashes):
        raise ValueError("public case hashes incomplete")
    maximum_m = max(coefficient_l1_upper(build_public_case(row)) for row in rows)
    if maximum_m > M_LIMIT:
        raise ValueError("public topology exceeds action accuracy budget")
    r2_path = root / "results/v3_tie_stability_r2/stability.json"
    r2 = json.loads(r2_path.read_bytes())
    failed_ids = sorted({row["path_id"] for group in r2["path_failures"].values()
                         for row in group})
    if len(failed_ids) != 21:
        raise ValueError("R2 failure regression requires 21 distinct path IDs")
    diagnostic_path = root / "results/v3_single_presolve_diagnostic/diagnostic.json"
    if _digest(diagnostic_path) != DIAGNOSTIC_RESULT_SHA256:
        raise ValueError("accepted presolve diagnostic result changed")
    diagnostic = json.loads(diagnostic_path.read_bytes())
    if (diagnostic.get("status") != "completed"
            or diagnostic.get("reproduced_paths") != 73
            or diagnostic.get("differing_periods") != 20):
        raise ValueError("presolve diagnostic is not the accepted complete result")
    period0_ids = sorted({item["path_id"] for item in diagnostic["records"]
                          if item["period"] == 0})
    if len(period0_ids) != 8 or set(period0_ids) & set(failed_ids):
        raise ValueError("eight period-zero fixtures must be distinct from R2 failures")
    path_ids = (sorted(set(failed_ids) | set(period0_ids)) if kind == "regression" else
                [f"{case_id}|{cell}" for case_id in ids
                 for cell in ("short/minor", "long/major")])
    if len(path_ids) != (29 if kind == "regression" else 336):
        raise ValueError("single-solve workload path count differs")
    source_names = (
        "allocation_v3.py", "core.py", "core_v2.py", "core_v3.py",
        "networks_v3.py", "handoff_v3_single.py", "planner_v3_single_r3.py",
        "profile_v3.py", "stability_v3_single_r4.py",
    )
    source = {name: _digest(root / "src/sn" / name) for name in source_names}
    output_root = f"results/v3_single_{kind}_r4"
    manifest = {
        "schema": 1, "kind": kind, "engineering_revision": "r4",
        "status": "proposal_pending_owner_named_hash",
        "protocol_sha256": _digest(root / "docs/V3_SINGLE_SOLVE_R3_PROTOCOL.md"),
        "postsolve_audit_sha256": _digest(root / "docs/V3_SINGLE_SOLVE_R3_POSTSOLVE_AUDIT.md"),
        "preflight_amendment_sha256": _digest(
            root / "docs/V3_SINGLE_SOLVE_R4_PREFLIGHT_AMENDMENT.md"),
        "prior_r3_zero_solve_preflight_sha256": R3_PREFLIGHT_SHA256,
        "prior_r3_receipt_sha256": R3_RECEIPT_SHA256,
        "allocation_sha256": _digest(allocation_path),
        "public_design_manifest_sha256": _digest(design_path),
        "public_case_content_sha256": {case_id: hashes[case_id] for case_id in ids},
        "r2_result_sha256": _digest(r2_path),
        "diagnostic_result_sha256": DIAGNOSTIC_RESULT_SHA256,
        "diagnostic_period0_path_ids": period0_ids,
        "prior_single_regression_result_sha256": _digest(
            root / "results/v3_single_regression/stability.json"),
        "prior_single_full_result_sha256": _digest(
            root / "results/v3_single_full_r2/stability.json"),
        "source_sha256": source,
        "period0_fixture_test_sha256": _digest(
            root / "tests/test_v3_single_r3_engineering.py"),
        "g0_parity_test_sha256": _digest(
            root / "tests/test_v3_single_r3_degenerate_sample.py"),
        "receipt_test_sha256": _digest(
            root / "tests/test_v3_single_r4_receipt.py"),
        "path_ids": path_ids,
        "configurations": list(CONFIGURATIONS),
        "paths": {
            "allocation": "configs/proposed-v3-allocation.json",
            "public_slots": "configs/design/manifest.json",
            "allocation_approval": "configs/owner-approvals/v3-allocation.json",
            "public_design": "configs/v3-design",
            "execution_approval": f"configs/owner-approvals/v3-single-{kind}-r4.json",
            "preflight_output": f"{output_root}/preflight.json",
            "first_solve_marker": f"{output_root}/first_solve.started",
            "progress_directory": f"{output_root}/progress",
            "output": f"{output_root}/stability.json",
        },
        "pinned_gate_configurations": ["presolve-off-threads-1", "presolve-off-threads-2"],
        "sensitivity_configurations": ["presolve-on-threads-1", "presolve-on-threads-2"],
        "accuracy": {
            "continuous_action_delta": DELTA,
            "cumulative_action_contribution_max": LEDGER_BUDGET,
            "demand_stock_and_cumulative_ledger_max": LEDGER_BUDGET,
            "topology_coefficient_l1_limit": M_LIMIT,
            "observed_topology_coefficient_l1_upper_bound": maximum_m,
            "qualification_binary_agreement_required": True,
            "pinned_paths_and_certificates_required": True,
            "presolve_on_solver_faults_excluded_from_pinned_gate": True,
            "optimal_sensitivity_actions_must_agree": True,
            "action_digest_zero_canonicalization": "int and float zero to 0.0; nonzero floats exact",
        },
        "resource_caps": CAPS_BY_KIND[kind],
        "preflight_wall_seconds_per_worker": PREFLIGHT_WALL_SECONDS_PER_WORKER,
        "saved_outputs": ["full path failure text and tracebacks",
                          "certificate/resource summaries and correction dates/sizes",
                          "binary agreement and continuous/cumulative difference distributions"],
        "scope": {"strategic_quantities_saved": False,
                  "false_notice_paths": False, "calibration": False,
                  "confirmation_case_access": False, "m5_paused": True,
                  "regression_rerun_after_reviewed_fix": kind == "regression",
                  "full_stability_reruns_remaining_after_initial": 0,
                  "full_stability_failure_closes_g1": True},
    }
    if kind == "full":
        manifest["prerequisite_regression_manifest_sha256"] = _digest(
            root / MANIFESTS["regression"])
        manifest["prerequisite_regression_result_path"] = (
            "results/v3_single_regression_r4/stability.json")
    return manifest


def verify_full_prerequisite(root: Path, manifest: dict) -> None:
    """Require the owner-reviewed regression to pass before the full run."""
    if manifest["kind"] != "full":
        return
    path = root / manifest["prerequisite_regression_result_path"]
    if not path.is_file():
        raise PermissionError("full stability requires the completed r4 regression")
    raw = path.read_bytes()
    result = json.loads(raw)
    if (canonical(result) != raw
            or result.get("execution_manifest_sha256") !=
            manifest["prerequisite_regression_manifest_sha256"]
            or result.get("status") != "completed"
            or result.get("stability_acceptance") is not True
            or result.get("pinned_gate_acceptance") is not True
            or result.get("sensitivity_optimal_agreement") is not True):
        raise PermissionError("full stability prerequisite regression did not pass")


def verify_manifest(root: Path, path: Path, kind: str) -> tuple[dict, str]:
    root, path = root.resolve(), path.resolve()
    if kind not in MANIFESTS or path != root / MANIFESTS[kind]:
        raise ValueError("unexpected v3 single-solve manifest path")
    raw = path.read_bytes()
    if canonical(json.loads(raw)) != raw:
        raise ValueError("v3 single-solve manifest is noncanonical")
    manifest = json.loads(raw)
    if manifest != proposed_manifest(root, kind):
        raise ValueError("v3 single-solve manifest differs from bound inputs")
    return manifest, sha256(raw).hexdigest()


LEDGER_SCALARS = (
    "primary_due", "primary_delivered", "primary_shortfall",
    "pre_horizon_order_shortfall", "accepted_new", "backup_delivered",
    "spot_delivered",
)
LEDGER_STOCKS = (
    "new_demand", "arrivals", "produced", "components_consumed",
    "departures", "served", "backlog", "lost_sales",
)


def _ledger_row(ledger, case, period: int) -> dict:
    """Physical values for IPC comparison only; never persist this row."""
    quantities = {name: float(getattr(ledger, name)) for name in LEDGER_SCALARS}
    for name in LEDGER_STOCKS:
        for item in getattr(ledger, name):
            key = f"{name}/{item.site}/{item.product}"
            quantities[key] = quantities.get(key, 0.0) + item.quantity
    stock = {(item.site, item.product): item.quantity
             for item in ledger.closing_stock}
    demand_stock = {
        f"{item.site}/{item.product}": stock.get((item.site, item.product), 0.0)
        for item in case.baseline_demand[period]
    }
    return {"quantities": quantities, "demand_stock": demand_stock,
            "accepted_due": ledger.accepted_due}


def _action_weights(case) -> dict[str, float]:
    result = {"order": 1.0, "spot": 1.0}
    for recipe in case.network.recipes:
        result[f"produce/{recipe.site}/{recipe.output}"] = (
            1.0 + sum(coefficient for _, coefficient in recipe.inputs))
    for index in range(len(case.network.lanes)):
        result[f"ship/{index}"] = 2.0
    return result


def compare_configurations(baseline: dict, variant: dict,
                           cases_by_id: dict, expected_paths: int,
                           *, require_all: bool) -> dict:
    """Compare full physical paths and return differences only."""
    common = sorted(set(baseline) & set(variant))
    differences: dict[str, list[float]] = defaultdict(list)
    binary_total = binary_agreed = coordinate_total = coordinate_exact = 0
    accepted_due_total = accepted_due_agreed = 0
    max_action_contribution = max_stock_difference = max_ledger_difference = 0.0
    for path_id in common:
        case_id = path_id.split("|", 1)[0]
        weights = _action_weights(cases_by_id[case_id])
        first, second = baseline[path_id], variant[path_id]
        if (len(first["actions"]) != 20 or len(second["actions"]) != 20
                or len(first["ledgers"]) != 20 or len(second["ledgers"]) != 20):
            raise ValueError("stability comparison requires complete 20-period paths")
        cumulative_action = 0.0
        cumulative_ledger: dict[str, float] = defaultdict(float)
        for left, right, left_ledger, right_ledger in zip(
            first["actions"], second["actions"],
            first["ledgers"], second["ledgers"],
        ):
            binary_total += 1
            binary_agreed += int(left["qualify"] == right["qualify"])
            for name in sorted((set(left) | set(right)) - {"qualify"}):
                delta = abs(left.get(name, 0.0) - right.get(name, 0.0))
                differences[name].append(delta)
                coordinate_total += 1
                coordinate_exact += int(delta == 0.0)
                cumulative_action += weights[name] * delta
            max_action_contribution = max(max_action_contribution,
                                          cumulative_action)
            accepted_due_total += 1
            accepted_due_agreed += int(left_ledger["accepted_due"] ==
                                       right_ledger["accepted_due"])
            for name in set(left_ledger["demand_stock"]) | set(right_ledger["demand_stock"]):
                max_stock_difference = max(
                    max_stock_difference,
                    abs(left_ledger["demand_stock"].get(name, 0.0)
                        - right_ledger["demand_stock"].get(name, 0.0)),
                )
            for name in set(left_ledger["quantities"]) | set(right_ledger["quantities"]):
                cumulative_ledger[name] += (
                    left_ledger["quantities"].get(name, 0.0)
                    - right_ledger["quantities"].get(name, 0.0)
                )
                max_ledger_difference = max(max_ledger_difference,
                                            abs(cumulative_ledger[name]))
    max_coordinate = max((max(values) for values in differences.values()), default=0.0)
    return {
        "common_paths": len(common),
        "excluded_paths": expected_paths - len(common),
        "qualification_binary_agreed": binary_agreed,
        "qualification_binary_total": binary_total,
        "accepted_due_agreed": accepted_due_agreed,
        "accepted_due_total": accepted_due_total,
        "all_coordinate_exact": coordinate_exact,
        "all_coordinate_total": coordinate_total,
        "max_continuous_action_difference": max_coordinate,
        "max_cumulative_action_contribution": max_action_contribution,
        "max_demand_stock_difference": max_stock_difference,
        "max_cumulative_ledger_difference": max_ledger_difference,
        "absolute_coordinate_differences": {
            name: profile_v3._distribution(values)
            for name, values in sorted(differences.items())
        },
        "passed": ((not require_all or len(common) == expected_paths)
                   and binary_agreed == binary_total
                   and accepted_due_agreed == accepted_due_total
                   and max_coordinate <= DELTA
                   and max_action_contribution <= LEDGER_BUDGET
                   and max_stock_difference <= LEDGER_BUDGET
                   and max_ledger_difference <= LEDGER_BUDGET),
    }


def _full_failure(path_id: str, error: Exception, period: int | None) -> dict:
    row = {"path_id": path_id, "period": period,
           "type": type(error).__name__, "full_error_text": str(error),
           "traceback": traceback.format_exc(), "solver_fault": False}
    if isinstance(error, PlannerFailure):
        certificate = error.certificate
        row.update({"phase": certificate.phase, "solver_status": certificate.status,
                    "full_solver_message": certificate.message,
                    "solver_fault": certificate.status == 4})
    return row


def _worker(root: Path, manifest: dict, configuration_index: int) -> dict:
    paths = manifest["paths"]
    allocation = load_allocation(
        root / paths["allocation"], root / paths["public_slots"],
        _approved_hash(root / paths["allocation_approval"]),
    )
    rows = {row["case_id"]: row for candidate in allocation["candidates"]
            for row in candidate["cases"]}
    config = manifest["configurations"][configuration_index]
    caps = manifest["resource_caps"]
    progress_dir = root / paths["progress_directory"]
    progress_dir.mkdir(parents=True, exist_ok=True)
    progress_path = progress_dir / f"{config['name']}.jsonl"
    started = monotonic()
    phase_rows: list[dict] = []
    phase_count = certificate_bytes = 0
    current_period: int | None = None
    corrections: list[dict] = []
    completed: dict[str, dict] = {}
    failures: list[dict] = []
    original_solve, original_plan = planner_v3._solve, planner_v3.plan

    def measured_solve(model, objective, time_limit, phase, **kwargs):
        nonlocal phase_count, certificate_bytes
        if phase_count >= caps["maximum_mip_phases_per_config"]:
            raise profile_v3._ResourceCapError("per-config MIP cap reached")
        profile_v3._mark_first_solve(root, manifest, config["name"], phase)
        phase_count += 1
        begun = monotonic()
        try:
            vector, certificate = original_solve(model, objective, time_limit,
                                                  phase, **kwargs)
        except PlannerFailure as error:
            certificate, vector = error.certificate, None
        finally:
            profile_v3._resource_cap_check(caps, started, certificate_bytes)
        elapsed = monotonic() - begun
        size = len(canonical(asdict(certificate)))
        certificate_bytes += size
        phase_rows.append({
            "phase": phase, "status": certificate.status,
            "elapsed_seconds": elapsed,
            "absolute_gap": (abs(certificate.primal - certificate.dual)
                             if certificate.primal is not None and certificate.dual is not None
                             else None),
            "relative_gap": certificate.relative_gap,
            "certificate_bytes": size,
            "variables": certificate.model_variables,
            "rows": certificate.model_constraints,
            "nonzeros": certificate.model_nonzeros,
        })
        profile_v3._resource_cap_check(caps, started, certificate_bytes)
        if elapsed > caps["per_mip_seconds"] + 0.01:
            raise profile_v3._ResourceCapError("per-MIP time cap reached")
        if vector is None:
            raise PlannerFailure(certificate)
        return vector, certificate

    def measured_plan(problem):
        nonlocal current_period
        current_period = problem.state.period
        choice = original_plan(problem)
        if choice.handoff.corrections:
            corrections.append({
                "period": current_period,
                "maximum_size": choice.handoff.max_correction,
                "l1_size": choice.handoff.correction_l1,
                "coordinates": [x.coordinate for x in choice.handoff.corrections],
            })
        return choice

    planner_v3._solve, planner_v3.plan = measured_solve, measured_plan
    try:
        with progress_path.open("xb") as progress:
            for path_id in manifest["path_ids"]:
                case_id, cell = path_id.split("|", 1)
                corrections.clear()
                current_period = None
                before = len(phase_rows)
                path_started = monotonic()
                cap_failure = None
                try:
                    case = build_public_case(rows[case_id])
                    if coefficient_l1_upper(case) > M_LIMIT:
                        raise ValueError("topology coefficient limit exceeded")
                    actual_type, notice_type = cell.split("/")
                    schedule = (case.short_capacity if actual_type == "short"
                                else case.long_capacity)
                    rollout = planner_v3.run_benchmark(
                        network=case.network, contract=case.contract,
                        notice=Notice(schedule, notice_type),
                        short_capacity=case.short_capacity,
                        long_capacity=case.long_capacity,
                        actual_type=actual_type,
                        realized_demand=case.baseline_demand,
                        mean_demand=case.baseline_demand,
                        benchmark="literal", prior_severe=0.5,
                        literal_severe=0 if actual_type == "short" else 1,
                        time_limit=caps["per_mip_seconds"],
                        presolve=config["presolve"], threads=config["threads"],
                    )
                    if len(rollout.actions) != 20 or len(rollout.ledgers) != 20:
                        raise RuntimeError("path ended before period 20")
                    action_rows = [profile_v3._action_row(action, case)
                                   for action in rollout.actions]
                    completed[path_id] = {
                        "actions": action_rows,
                        "ledgers": [_ledger_row(ledger, case, t)
                                    for t, ledger in enumerate(rollout.ledgers)],
                    }
                    record = {"path_id": path_id, "status": "completed",
                              "phase_count": len(phase_rows) - before,
                              "action_digest": sha256(canonical_action_rows(action_rows)).hexdigest()}
                except profile_v3._ResourceCapError as error:
                    failure = _full_failure(path_id, error, current_period)
                    failures.append(failure)
                    record = {"path_id": path_id, "status": "failed",
                              "phase_count": len(phase_rows) - before, **failure}
                    cap_failure = error
                except Exception as error:
                    failure = _full_failure(path_id, error, current_period)
                    failures.append(failure)
                    record = {"path_id": path_id, "status": "failed",
                              "phase_count": len(phase_rows) - before, **failure}
                new_phases = phase_rows[before:]
                record.update({
                    "corrections": list(corrections),
                    "elapsed_seconds": monotonic() - path_started,
                    "peak_rss_mib": profile_v3._rss_mib(),
                    "phase_statuses": dict(sorted(Counter(
                        f"{item['phase']}:{item['status']}" for item in new_phases
                    ).items())),
                })
                progress.write(canonical(record))
                progress.flush()
                if cap_failure is not None:
                    raise cap_failure
                profile_v3._resource_cap_check(caps, started, certificate_bytes)
    finally:
        planner_v3._solve, planner_v3.plan = original_solve, original_plan
    if len(completed) + len(failures) != len(manifest["path_ids"]):
        raise RuntimeError("worker did not attempt every declared path")
    return {
        "configuration": config["name"], "paths": completed,
        "path_failures": failures, "attempted_paths": len(completed) + len(failures),
        "phase_rows": phase_rows, "phase_count": phase_count,
        "peak_rss_mib": profile_v3._rss_mib(),
        "elapsed_seconds": monotonic() - started,
        "progress_sha256": _digest(progress_path),
    }


def _preflight_worker(root: Path, manifest: dict, index: int,
                      probe_dir: Path) -> dict:
    parent = (root / manifest["paths"]["preflight_output"]).parent.resolve()
    if not probe_dir.resolve().is_relative_to(parent):
        raise ValueError("stability preflight probe directory escapes output root")
    config = manifest["configurations"][index]
    peak = profile_v3._rss_mib()
    if (not math.isfinite(peak) or peak <= 0
            or peak > manifest["resource_caps"]["per_config_peak_rss_mib"]):
        raise RuntimeError("stability preflight peak RSS failed")
    payload = {"configuration": config["name"], "index": index,
               "peak_rss_mib": peak, "mip_phases": 0,
               "probe": "v3-tie-stability-preflight-v1"}
    probe = probe_dir / f"configuration-{index}.json"
    raw = canonical(payload)
    probe.write_bytes(raw)
    if probe.read_bytes() != raw:
        raise OSError("stability preflight file read-back mismatch")
    return payload


def _decode_worker_stdout(raw: str, expected_configuration: str) -> tuple[dict, int]:
    """Accept one framed JSON payload and known native HiGHS diagnostic lines.

    HiGHS can write directly to process stdout after Python emits its JSON.
    These lines are diagnostics, not path records or scientific output.
    """
    payloads: list[dict] = []
    diagnostic_count = 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        if line.startswith("HighsMipSolverData::"):
            diagnostic_count += 1
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"unrecognized worker stdout line: {line}") from error
        if not isinstance(item, dict):
            raise ValueError("worker stdout payload is not an object")
        payloads.append(item)
    if len(payloads) != 1 or payloads[0].get("configuration") != expected_configuration:
        raise ValueError("worker stdout must contain one matching payload")
    return payloads[0], diagnostic_count


def preflight(root: Path, manifest_path: Path, kind: str) -> dict:
    root = root.resolve()
    manifest, digest = verify_manifest(root, manifest_path, kind)
    output = root / manifest["paths"]["preflight_output"]
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    started = monotonic()
    with tempfile.TemporaryDirectory(prefix="probe-", dir=output.parent) as folder:
        probe_dir = Path(folder)
        for index, config in enumerate(CONFIGURATIONS):
            env = {**os.environ, "PYTHONPATH": str(root / "src")}
            command = [sys.executable, "-m", "sn.stability_v3_single_r4", "preflight-worker",
                       "--repository-root", str(root), "--manifest", str(manifest_path),
                       "--kind", kind,
                       "--configuration-index", str(index),
                       "--preflight-dir", str(probe_dir)]
            try:
                completed = profile_v3._run_worker_process(
                    command, root, env, wall_seconds=PREFLIGHT_WALL_SECONDS_PER_WORKER,
                )
                if completed.returncode != 0:
                    raise RuntimeError(f"worker exit {completed.returncode}: "
                                       f"{completed.stderr}")
                payload = json.loads(completed.stdout)
                probe = probe_dir / f"configuration-{index}.json"
                if (probe.read_bytes() != canonical(payload)
                        or payload["configuration"] != config["name"]
                        or payload["index"] != index
                        or payload["mip_phases"] != 0
                        or not math.isfinite(payload["peak_rss_mib"])
                        or payload["peak_rss_mib"] <= 0):
                    raise ValueError("stability preflight IPC/output mismatch")
                rows.append({"configuration": config["name"], "status": "passed",
                             "peak_rss_mib": payload["peak_rss_mib"]})
            except Exception as error:
                rows.append({"configuration": config["name"], "status": "failed",
                             "cause": type(error).__name__,
                             "message": str(error),
                             "traceback": traceback.format_exc()})
    result = {
        "status": "passed" if all(row["status"] == "passed" for row in rows) else "failed",
        "execution_manifest_sha256": digest,
        "configuration_workers": rows,
        "elapsed_seconds": monotonic() - started,
        "mip_solves": 0, "approval_consumed": False,
    }
    raw = canonical(result)
    temporary = output.with_suffix(".tmp")
    temporary.write_bytes(raw)
    temporary.replace(output)
    return {**result, "output_sha256": sha256(raw).hexdigest()}


def run(root: Path, manifest_path: Path, kind: str) -> dict:
    root = root.resolve()
    manifest, digest = verify_manifest(root, manifest_path, kind)
    verify_full_prerequisite(root, manifest)
    preflight_result = preflight(root, manifest_path, kind)
    if preflight_result["status"] != "passed":
        return {"status": "preflight_failed", "mip_solves": 0,
                "approval_consumed": False,
                "execution_manifest_sha256": digest,
                "preflight_output_sha256": preflight_result["output_sha256"]}
    paths, caps = manifest["paths"], manifest["resource_caps"]
    profile_v3._receipt(root / paths["execution_approval"],
                        _receipt_decision(kind), digest)
    approval = root / paths["allocation_approval"]
    verification = verify_public_design(
        root, root / paths["allocation"], root / paths["public_slots"],
        approval, root / paths["public_design"],
    )
    if verification["manifest_sha256"] != manifest["public_design_manifest_sha256"]:
        raise ValueError("public design hash mismatch")
    output = root / paths["output"]
    marker = output.parent / "attempt.started"
    first_solve = root / paths["first_solve_marker"]
    progress_dir = root / paths["progress_directory"]
    if output.exists() or marker.exists() or first_solve.exists() or progress_dir.exists():
        raise FileExistsError("single-solve attempt already exists; zero retries")
    output.parent.mkdir(parents=True, exist_ok=True)
    marker.write_bytes(canonical({"execution_manifest_sha256": digest}))
    started = monotonic()
    children = []
    try:
        for index, config in enumerate(CONFIGURATIONS):
            remaining = caps["global_wall_seconds"] - (monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("global wall cap reached")
            env = {**os.environ, "PYTHONPATH": str(root / "src")}
            command = [sys.executable, "-m", "sn.stability_v3_single_r4", "worker",
                       "--repository-root", str(root), "--manifest", str(manifest_path),
                       "--kind", kind, "--configuration-index", str(index)]
            completed = profile_v3._run_worker_process(
                command, root, env, wall_seconds=remaining)
            if completed.returncode != 0:
                raise RuntimeError(f"{config['name']} stopped: {completed.stderr}")
            if len(completed.stdout.encode()) > caps["saved_bytes"]:
                raise RuntimeError("worker IPC cap reached")
            child, diagnostics = _decode_worker_stdout(completed.stdout, config["name"])
            child["native_stdout_diagnostic_count"] = diagnostics
            if child["peak_rss_mib"] > caps["per_config_peak_rss_mib"]:
                raise profile_v3._ResourceCapError("worker RSS cap reached")
            children.append(child)
        total_phases = sum(child["phase_count"] for child in children)
        if total_phases > caps["maximum_mip_phases"]:
            raise RuntimeError("global MIP cap reached")
        expected = len(manifest["path_ids"])
        if sum(child["attempted_paths"] for child in children) != 4 * expected:
            raise RuntimeError("attempted path count mismatch")
        allocation = load_allocation(
            root / paths["allocation"], root / paths["public_slots"],
            _approved_hash(approval),
        )
        cases = {row["case_id"]: build_public_case(row)
                 for candidate in allocation["candidates"]
                 for row in candidate["cases"]}
        by_name = {child["configuration"]: child for child in children}
        pinned_first = by_name["presolve-off-threads-1"]
        pinned_second = by_name["presolve-off-threads-2"]
        pinned_comparison = compare_configurations(
            pinned_first["paths"], pinned_second["paths"], cases,
            expected, require_all=True)
        sensitivity = {}
        for threads in (1, 2):
            off = by_name[f"presolve-off-threads-{threads}"]
            on = by_name[f"presolve-on-threads-{threads}"]
            comparison = compare_configurations(
                off["paths"], on["paths"], cases, expected,
                require_all=False)
            solver_faults = [x for x in on["path_failures"] if x["solver_fault"]]
            other_failures = [x for x in on["path_failures"] if not x["solver_fault"]]
            sensitivity[on["configuration"]] = {
                "comparison": comparison,
                "solver_fault_count": len(solver_faults),
                "other_failure_count": len(other_failures),
                "passed": comparison["passed"] and not other_failures,
            }
        failures = {child["configuration"]: child["path_failures"]
                    for child in children}
        pinned_acceptance = (
            not pinned_first["path_failures"] and not pinned_second["path_failures"]
            and len(pinned_first["paths"]) == expected
            and len(pinned_second["paths"]) == expected
            and pinned_comparison["passed"])
        sensitivity_acceptance = all(x["passed"] for x in sensitivity.values())
        acceptance = pinned_acceptance and sensitivity_acceptance
        complete_paths = sum(len(child["paths"]) for child in children)
        result = {
            "status": "completed" if acceptance else "completed_with_failures",
            "stability_acceptance": acceptance,
            "pinned_gate_acceptance": pinned_acceptance,
            "sensitivity_optimal_agreement": sensitivity_acceptance,
            "execution_manifest_sha256": digest,
            "public_design_manifest_sha256": verification["manifest_sha256"],
            "attempted_paths": 4 * expected,
            "completed_paths": complete_paths,
            "path_failure_count": sum(len(rows) for rows in failures.values()),
            "path_failures": failures,
            "completed_rolling_decisions": 20 * complete_paths,
            "mip_phases": total_phases,
            "elapsed_seconds": monotonic() - started,
            "configuration_resource": {
                child["configuration"]: {
                    "mip_phases": child["phase_count"],
                    "peak_rss_mib": child["peak_rss_mib"],
                    "elapsed_seconds": child["elapsed_seconds"],
                    "phase_types": profile_v3._phase_summary(child["phase_rows"]),
                    "progress_sha256": child["progress_sha256"],
                    "native_stdout_diagnostic_count": child["native_stdout_diagnostic_count"],
                } for child in children
            },
            "pinned_action_parity": pinned_comparison,
            "presolve_on_sensitivity": sensitivity,
            "strategic_quantities_saved": False,
        }
    except Exception as error:
        if not first_solve.exists():
            marker.unlink(missing_ok=True)
            zero = {"status": "stopped_zero_solve", "execution_manifest_sha256": digest,
                    "stop_type": type(error).__name__, "full_error_text": str(error),
                    "traceback": traceback.format_exc(), "mip_solves": 0,
                    "approval_consumed": False}
            raw = canonical(zero)
            (root / paths["preflight_output"]).write_bytes(raw)
            return {"status": zero["status"], "execution_manifest_sha256": digest,
                    "preflight_output_sha256": sha256(raw).hexdigest(),
                    "mip_solves": 0, "approval_consumed": False}
        result = {"status": "stopped", "stability_acceptance": False,
                  "execution_manifest_sha256": digest,
                  "elapsed_seconds": monotonic() - started,
                  "stop_type": type(error).__name__, "full_error_text": str(error),
                  "traceback": traceback.format_exc(),
                  "strategic_quantities_saved": False}
    raw = canonical(result)
    progress_bytes = sum(path.stat().st_size for path in progress_dir.glob("*.jsonl"))
    if len(raw) + progress_bytes > caps["saved_bytes"]:
        raise RuntimeError("saved-output cap reached")
    output.write_bytes(raw)
    return {"status": result["status"], "output_sha256": sha256(raw).hexdigest(),
            "execution_manifest_sha256": digest}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.stability_v3_single_r4")
    parser.add_argument("command", choices=("propose", "review", "preflight",
                                            "preflight-worker", "run", "worker"))
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--kind", choices=("regression", "full"), required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--configuration-index", type=int)
    parser.add_argument("--preflight-dir", type=Path)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    path = args.manifest or root / MANIFESTS[args.kind]
    if args.command == "propose":
        if path.exists():
            raise FileExistsError("single-solve proposal already exists")
        path.write_bytes(canonical(proposed_manifest(root, args.kind)))
        print(json.dumps({"manifest_sha256": _digest(path), "mip_solves": 0}))
    elif args.command == "review":
        manifest, digest = verify_manifest(root, path, args.kind)
        print(json.dumps({"manifest_sha256": digest,
                          "path_ids": len(manifest["path_ids"]), "mip_solves": 0}))
    elif args.command == "preflight":
        result = preflight(root, path, args.kind)
        print(json.dumps(result, sort_keys=True))
        if result["status"] != "passed":
            raise SystemExit(1)
    elif args.command == "preflight-worker":
        if args.configuration_index not in range(4) or args.preflight_dir is None:
            raise ValueError("preflight worker requires configuration and probe directory")
        manifest, _ = verify_manifest(root, path, args.kind)
        print(json.dumps(_preflight_worker(root, manifest, args.configuration_index,
                                           args.preflight_dir), separators=(",", ":")))
    elif args.command == "worker":
        if args.configuration_index not in range(4):
            raise ValueError("worker configuration must be 0..3")
        manifest, digest = verify_manifest(root, path, args.kind)
        profile_v3._receipt(root / manifest["paths"]["execution_approval"],
                            _receipt_decision(args.kind), digest)
        marker = (root / manifest["paths"]["output"]).parent / "attempt.started"
        if not marker.is_file() or marker.read_bytes() != canonical({
            "execution_manifest_sha256": digest,
        }):
            raise PermissionError("worker lacks active bound attempt")
        print(json.dumps(_worker(root, manifest, args.configuration_index),
                         separators=(",", ":")))
    else:
        print(json.dumps(run(root, path, args.kind), sort_keys=True))


if __name__ == "__main__":
    main()
