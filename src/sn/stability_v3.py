"""Approval-gated full-design v3 action-stability check; no strategic output saved.

All public cases and both truthful cells run in four fresh solver processes.
Raw actions and physical ledgers cross IPC for comparison only. Persistent
progress rows contain hashes, certificate summaries, correction dates, and
failure categories, never action or payoff levels.
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
from time import monotonic

from . import planner_v3, profile_v3
from .allocation_v3 import canonical
from .core import Notice
from .networks_v3 import (
    _approved_hash, build_public_case, load_allocation, verify_public_design,
)
from .planner_v3 import PlannerFailure


CONFIGURATIONS = profile_v3.CONFIGURATIONS
PREFLIGHT_WALL_SECONDS_PER_WORKER = 30
DELTA = 1e-7
LEDGER_BUDGET = 1e-4
M_LIMIT = 50
CAPS = {
    "per_mip_seconds": 30,
    "global_wall_seconds": 1800,
    "per_config_peak_rss_mib": 512,
    "saved_bytes": 67108864,
    "maximum_paths": 1344,
    "maximum_rolling_decisions": 26880,
    "maximum_mip_phases": 80640,
    "maximum_mip_phases_per_config": 20160,
    "retries": 0,
}
MANIFEST = "configs/proposed-v3-tie-stability.json"


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def coefficient_l1_upper(case) -> float:
    """Conservative sum over all current-action row coefficients."""
    network = case.network
    return (2.0  # accepted primary order and immediate spot control
            + sum(1.0 + sum(coefficient for _, coefficient in recipe.inputs)
                  for recipe in network.recipes)
            + 2.0 * len(network.lanes))


def proposed_manifest(root: Path) -> dict:
    root = root.resolve()
    allocation_path = root / "configs/proposed-v3-allocation.json"
    slot_path = root / "configs/design/manifest.json"
    approval_path = root / "configs/owner-approvals/v3-allocation.json"
    allocation = load_allocation(allocation_path, slot_path,
                                 _approved_hash(approval_path))
    rows = [row for candidate in allocation["candidates"]
            for row in candidate["cases"]]
    ids = [row["case_id"] for row in rows]
    if len(ids) != len(set(ids)) or len(ids) != 168:
        raise ValueError("full stability requires 168 distinct public cases")
    design_path = root / "configs/v3-design/manifest.json"
    design_raw = design_path.read_bytes()
    design = json.loads(design_raw)
    if canonical(design) != design_raw:
        raise ValueError("public design manifest is noncanonical")
    if design["allocation_sha256"] != _digest(allocation_path):
        raise ValueError("public design differs from approved allocation")
    hashes = {row["case_id"]: row["content_sha256"] for row in design["cases"]}
    if set(ids) != set(hashes):
        raise ValueError("public stability case hashes incomplete")
    maximum_m = max(coefficient_l1_upper(build_public_case(row)) for row in rows)
    if maximum_m > M_LIMIT:
        raise ValueError("public topology exceeds frozen action accuracy budget")
    freeze_path = root / "configs/v3-action-parity-freeze.json"
    freeze = json.loads(freeze_path.read_bytes())
    if (canonical(freeze) != freeze_path.read_bytes()
            or freeze["delta"] != DELTA
            or freeze["protocol_sha256"] != _digest(
                root / "docs/PROPOSED_V3_PROTOCOL_RECONCILED.md")
            or freeze["profile_manifest_sha256"] != _digest(
                root / "configs/proposed-v3-resource-profile-r4.json")
            or freeze["profile_result_sha256"] != _digest(
                root / "results/v3_resource_profile_r4/profile.json")
            or freeze["max_topology_coefficient_l1_upper_bound"] != maximum_m
            or freeze["completed_paths"] != 240
            or freeze["path_failures"] != 0):
        raise ValueError("v3 parity freeze differs from completed R4 evidence")
    profile_result = json.loads((root / "results/v3_resource_profile_r4/profile.json").read_bytes())
    if (profile_result["status"] != "completed"
            or profile_result["path_count"] != 240
            or profile_result["path_failure_count"] != 0):
        raise ValueError("full stability needs completed R4 profile")
    sources = profile_v3.source_hashes()
    sources["stability_v3.py"] = _digest(Path(__file__))
    return {
        "schema": 1,
        "status": "proposal_pending_owner_tie_stability_approval",
        "protocol_sha256": _digest(root / "docs/PROPOSED_V3_PROTOCOL_RECONCILED.md"),
        "allocation_sha256": _digest(allocation_path),
        "public_design_manifest_sha256": sha256(design_raw).hexdigest(),
        "public_case_content_sha256": {case_id: hashes[case_id] for case_id in ids},
        "parity_freeze_sha256": _digest(freeze_path),
        "r4_profile_manifest_sha256": _digest(root / "configs/proposed-v3-resource-profile-r4.json"),
        "r4_profile_result_sha256": _digest(root / "results/v3_resource_profile_r4/profile.json"),
        "source_sha256": sources,
        "environment": profile_v3.proposed_manifest(root)["environment"],
        "paths": {
            "allocation": "configs/proposed-v3-allocation.json",
            "public_slots": "configs/design/manifest.json",
            "allocation_approval": "configs/owner-approvals/v3-allocation.json",
            "public_design": "configs/v3-design",
            "stability_approval": "configs/owner-approvals/v3-tie-stability.json",
            "preflight_output": "results/v3_tie_stability/preflight.json",
            "first_solve_marker": "results/v3_tie_stability/first_solve.started",
            "progress_directory": "results/v3_tie_stability/progress",
            "output": "results/v3_tie_stability/stability.json",
        },
        "public_case_ids": ids,
        "truthful_cells": ["short/minor", "long/major"],
        "configurations": list(CONFIGURATIONS),
        "solver_phase_presolve_rule": "primary and qualification branch follow configuration; secondary always off",
        "configuration_process_rule": "one fresh process per configuration; never switch HiGHS thread count in one process",
        "preflight_wall_seconds_per_worker": PREFLIGHT_WALL_SECONDS_PER_WORKER,
        "accuracy": {
            "continuous_action_delta": DELTA,
            "cumulative_action_contribution_max": LEDGER_BUDGET,
            "demand_stock_and_cumulative_ledger_max": LEDGER_BUDGET,
            "topology_coefficient_l1_limit": M_LIMIT,
            "observed_topology_coefficient_l1_upper_bound": maximum_m,
            "qualification_binary_agreement_required": True,
            "all_paths_and_certificates_required": True,
        },
        "resource_basis": {
            "r4_paths": 240, "r4_mip_phases": profile_result["mip_phases"],
            "r4_wall_seconds": profile_result["elapsed_seconds"],
            "r4_max_worker_rss_mib": max(x["peak_rss_mib"] for x in
                                          profile_result["configuration_resource"].values()),
            "scaling_factor": 168 / 30,
        },
        "resource_caps": CAPS,
        "saved_outputs": [
            "per-path certificate and resource progress, action digest and correction dates",
            "configuration phase summaries, path failures and parity difference distributions",
            "maximum cumulative action, demand-stock and ledger differences",
        ],
        "scope": {"strategic_quantities_saved": False,
                  "false_notice_paths": False, "calibration": False,
                  "confirmation_case_access": False, "m5_paused": True},
    }


def verify_manifest(root: Path, path: Path) -> tuple[dict, str]:
    root, path = root.resolve(), path.resolve()
    if path != root / MANIFEST:
        raise ValueError("unexpected v3 stability manifest path")
    raw = path.read_bytes()
    if canonical(json.loads(raw)) != raw:
        raise ValueError("v3 stability manifest is noncanonical")
    manifest = json.loads(raw)
    if manifest != proposed_manifest(root):
        raise ValueError("v3 stability manifest differs from bound inputs")
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
                           cases_by_id: dict) -> dict:
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
        "excluded_paths": 336 - len(common),
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
        "passed": (len(common) == 336
                   and binary_agreed == binary_total
                   and accepted_due_agreed == accepted_due_total
                   and max_coordinate <= DELTA
                   and max_action_contribution <= LEDGER_BUDGET
                   and max_stock_difference <= LEDGER_BUDGET
                   and max_ledger_difference <= LEDGER_BUDGET),
    }


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
    corrections: list[int] = []
    completed: dict[str, dict] = {}
    failures: list[dict] = []
    original_solve, original_plan = planner_v3._solve, planner_v3.plan

    def measured_solve(model, objective, time_limit, phase, **kwargs):
        nonlocal phase_count, certificate_bytes
        if phase_count >= caps["maximum_mip_phases_per_config"]:
            raise profile_v3._ResourceCapError("stability per-config MIP cap reached")
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
        certificate_size = len(canonical(asdict(certificate)))
        certificate_bytes += certificate_size
        phase_rows.append({
            "phase": phase, "status": certificate.status,
            "elapsed_seconds": elapsed,
            "absolute_gap": (abs(certificate.primal - certificate.dual)
                             if certificate.primal is not None and certificate.dual is not None
                             else None),
            "relative_gap": certificate.relative_gap,
            "certificate_bytes": certificate_size,
            "variables": certificate.model_variables,
            "rows": certificate.model_constraints,
            "nonzeros": certificate.model_nonzeros,
        })
        profile_v3._resource_cap_check(caps, started, certificate_bytes)
        if elapsed > caps["per_mip_seconds"] + 0.01:
            raise profile_v3._ResourceCapError("stability per-MIP time cap reached")
        if vector is None:
            raise PlannerFailure(certificate)
        return vector, certificate

    def measured_plan(problem):
        choice = original_plan(problem)
        if choice.execution_shipment_correction_l1 > 0:
            corrections.append(problem.state.period)
        return choice

    planner_v3._solve, planner_v3.plan = measured_solve, measured_plan
    try:
        with progress_path.open("xb") as progress:
            for case_id in manifest["public_case_ids"]:
                try:
                    case = build_public_case(rows[case_id])
                    if coefficient_l1_upper(case) > M_LIMIT:
                        raise ValueError("stability topology coefficient limit exceeded")
                except Exception as error:
                    for cell in manifest["truthful_cells"]:
                        path_id = f"{case_id}|{cell}"
                        failure = profile_v3._path_failure(path_id, error)
                        failures.append(failure)
                        progress.write(canonical({"path_id": path_id,
                                                  "status": "failed", **failure}) + b"\n")
                    profile_v3._resource_cap_check(caps, started, certificate_bytes)
                    continue
                for cell in manifest["truthful_cells"]:
                    path_id = f"{case_id}|{cell}"
                    actual_type, notice_type = cell.split("/")
                    schedule = (case.short_capacity if actual_type == "short"
                                else case.long_capacity)
                    corrections.clear()
                    before = len(phase_rows)
                    path_started = monotonic()
                    try:
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
                            presolve=config["presolve"],
                            threads=config["threads"],
                        )
                        if len(rollout.actions) != 20 or len(rollout.ledgers) != 20:
                            raise RuntimeError("stability path ended before period 20")
                        action_rows = [profile_v3._action_row(action, case)
                                       for action in rollout.actions]
                        completed[path_id] = {
                            "actions": action_rows,
                            "ledgers": [_ledger_row(ledger, case, t)
                                        for t, ledger in enumerate(rollout.ledgers)],
                        }
                        record = {
                            "path_id": path_id, "status": "completed",
                            "phase_count": len(phase_rows) - before,
                            "action_digest": sha256(canonical(action_rows)).hexdigest(),
                            "correction_dates": sorted(set(corrections)),
                        }
                    except profile_v3._ResourceCapError:
                        raise
                    except Exception as error:
                        failure = profile_v3._path_failure(path_id, error)
                        failures.append(failure)
                        record = {"path_id": path_id, "status": "failed",
                                  "phase_count": len(phase_rows) - before,
                                  "correction_dates": sorted(set(corrections)),
                                  **failure}
                    new_phases = phase_rows[before:]
                    record.update({
                        "elapsed_seconds": monotonic() - path_started,
                        "peak_rss_mib": profile_v3._rss_mib(),
                        "phase_statuses": dict(sorted(Counter(
                            f"{item['phase']}:{item['status']}" for item in new_phases
                        ).items())),
                        "maximum_absolute_gap": max(
                            (item["absolute_gap"] for item in new_phases
                             if item["absolute_gap"] is not None), default=None),
                        "maximum_relative_gap": max(
                            (item["relative_gap"] for item in new_phases
                             if item["relative_gap"] is not None), default=None),
                        "maximum_certificate_bytes": max(
                            (item["certificate_bytes"] for item in new_phases),
                            default=None),
                    })
                    progress.write(canonical(record) + b"\n")
                    progress.flush()
                    profile_v3._resource_cap_check(caps, started, certificate_bytes)
    finally:
        planner_v3._solve, planner_v3.plan = original_solve, original_plan
    if len(completed) + len(failures) != 336:
        raise RuntimeError("stability worker did not attempt 336 paths")
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
    if not math.isfinite(peak) or peak <= 0 or peak > CAPS["per_config_peak_rss_mib"]:
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


def preflight(root: Path, manifest_path: Path) -> dict:
    root = root.resolve()
    manifest, digest = verify_manifest(root, manifest_path)
    output = root / manifest["paths"]["preflight_output"]
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    started = monotonic()
    with tempfile.TemporaryDirectory(prefix="probe-", dir=output.parent) as folder:
        probe_dir = Path(folder)
        for index, config in enumerate(CONFIGURATIONS):
            env = {**os.environ, "PYTHONPATH": str(root / "src")}
            command = [sys.executable, "-m", "sn.stability_v3", "preflight-worker",
                       "--repository-root", str(root), "--manifest", str(manifest_path),
                       "--configuration-index", str(index),
                       "--preflight-dir", str(probe_dir)]
            try:
                completed = profile_v3._run_worker_process(
                    command, root, env, wall_seconds=PREFLIGHT_WALL_SECONDS_PER_WORKER,
                )
                if completed.returncode != 0:
                    raise RuntimeError(f"worker exit {completed.returncode}: "
                                       f"{completed.stderr[-1000:]}")
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
                             "message": str(error)[:1000]})
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


def run(root: Path, manifest_path: Path) -> dict:
    root = root.resolve()
    manifest, digest = verify_manifest(root, manifest_path)
    preflight_result = preflight(root, manifest_path)
    if preflight_result["status"] != "passed":
        return {"status": "preflight_failed", "mip_solves": 0,
                "approval_consumed": False,
                "execution_manifest_sha256": digest,
                "preflight_output_sha256": preflight_result["output_sha256"]}
    paths = manifest["paths"]
    profile_v3._receipt(root / paths["stability_approval"],
                        "owner_approved_v3_tie_stability", digest)
    approval = root / paths["allocation_approval"]
    verification = verify_public_design(
        root, root / paths["allocation"], root / paths["public_slots"],
        approval, root / paths["public_design"],
    )
    if verification["manifest_sha256"] != manifest["public_design_manifest_sha256"]:
        raise ValueError("stability public design hash mismatch")
    output = root / paths["output"]
    marker = output.parent / "attempt.started"
    first_solve = root / paths["first_solve_marker"]
    progress_dir = root / paths["progress_directory"]
    if output.exists() or marker.exists() or first_solve.exists() or progress_dir.exists():
        raise FileExistsError("v3 stability attempt already exists; zero retries")
    output.parent.mkdir(parents=True, exist_ok=True)
    marker.write_bytes(canonical({"execution_manifest_sha256": digest}))
    started = monotonic()
    children = []
    try:
        for index, config in enumerate(CONFIGURATIONS):
            remaining = CAPS["global_wall_seconds"] - (monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("stability global wall cap reached")
            env = {**os.environ, "PYTHONPATH": str(root / "src")}
            command = [sys.executable, "-m", "sn.stability_v3", "worker",
                       "--repository-root", str(root), "--manifest", str(manifest_path),
                       "--configuration-index", str(index)]
            completed = profile_v3._run_worker_process(
                command, root, env, wall_seconds=remaining,
            )
            if completed.returncode != 0:
                raise RuntimeError(f"{config['name']} stopped: {completed.stderr[-2000:]}")
            if len(completed.stdout.encode()) > CAPS["saved_bytes"]:
                raise RuntimeError("stability worker IPC cap reached")
            child = json.loads(completed.stdout)
            if child["peak_rss_mib"] > CAPS["per_config_peak_rss_mib"]:
                raise profile_v3._ResourceCapError("stability worker RSS cap reached")
            children.append(child)
        total_phases = sum(child["phase_count"] for child in children)
        if total_phases > CAPS["maximum_mip_phases"]:
            raise RuntimeError("stability global MIP cap reached")
        if sum(child["attempted_paths"] for child in children) != 1344:
            raise RuntimeError("stability attempted path count mismatch")
        allocation = load_allocation(
            root / paths["allocation"], root / paths["public_slots"],
            _approved_hash(approval),
        )
        cases = {row["case_id"]: build_public_case(row)
                 for candidate in allocation["candidates"]
                 for row in candidate["cases"]}
        baseline = children[0]["paths"]
        comparisons = {child["configuration"]: compare_configurations(
            baseline, child["paths"], cases) for child in children[1:]}
        failures = {child["configuration"]: child["path_failures"]
                    for child in children}
        failure_count = sum(len(rows) for rows in failures.values())
        complete_paths = sum(len(child["paths"]) for child in children)
        acceptance = (failure_count == 0 and complete_paths == 1344
                      and all(item["passed"] for item in comparisons.values()))
        result = {
            "status": "completed" if acceptance else "completed_with_failures",
            "stability_acceptance": acceptance,
            "execution_manifest_sha256": digest,
            "public_design_manifest_sha256": verification["manifest_sha256"],
            "attempted_paths": 1344,
            "completed_paths": complete_paths,
            "path_failure_count": failure_count,
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
                } for child in children
            },
            "action_parity_vs_baseline": comparisons,
            "strategic_quantities_saved": False,
        }
    except Exception as error:
        if not first_solve.exists():
            marker.unlink(missing_ok=True)
            zero = {"status": "stopped_zero_solve",
                    "execution_manifest_sha256": digest,
                    "stop_type": type(error).__name__, "stop_reason": str(error),
                    "mip_solves": 0, "approval_consumed": False}
            raw = canonical(zero)
            (root / paths["preflight_output"]).write_bytes(raw)
            return {"status": zero["status"],
                    "execution_manifest_sha256": digest,
                    "preflight_output_sha256": sha256(raw).hexdigest(),
                    "mip_solves": 0, "approval_consumed": False}
        result = {"status": "stopped", "stability_acceptance": False,
                  "execution_manifest_sha256": digest,
                  "elapsed_seconds": monotonic() - started,
                  "stop_type": type(error).__name__, "stop_reason": str(error),
                  "strategic_quantities_saved": False}
    raw = canonical(result)
    progress_bytes = sum(path.stat().st_size for path in progress_dir.glob("*.jsonl"))
    if len(raw) + progress_bytes > CAPS["saved_bytes"]:
        raise RuntimeError("stability saved-output cap reached")
    output.write_bytes(raw)
    return {"status": result["status"],
            "output_sha256": sha256(raw).hexdigest(),
            "execution_manifest_sha256": digest}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.stability_v3")
    parser.add_argument("command", choices=("propose", "review", "preflight",
                                            "preflight-worker", "run", "worker"))
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--configuration-index", type=int)
    parser.add_argument("--preflight-dir", type=Path)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    path = args.manifest or root / MANIFEST
    if args.command == "propose":
        if path.exists():
            raise FileExistsError("v3 stability proposal already exists")
        path.write_bytes(canonical(proposed_manifest(root)))
        print(json.dumps({"manifest_sha256": _digest(path), "mip_solves": 0}))
    elif args.command == "review":
        manifest, digest = verify_manifest(root, path)
        print(json.dumps({"manifest_sha256": digest,
                          "public_case_ids": len(manifest["public_case_ids"]),
                          "mip_solves": 0}))
    elif args.command == "preflight":
        result = preflight(root, path)
        print(json.dumps(result, sort_keys=True))
        if result["status"] != "passed":
            raise SystemExit(1)
    elif args.command == "preflight-worker":
        if args.configuration_index not in range(4) or args.preflight_dir is None:
            raise ValueError("preflight worker requires configuration and probe directory")
        manifest, _ = verify_manifest(root, path)
        print(json.dumps(_preflight_worker(root, manifest, args.configuration_index,
                                           args.preflight_dir), separators=(",", ":")))
    elif args.command == "worker":
        if args.configuration_index not in range(4):
            raise ValueError("stability worker configuration must be 0..3")
        manifest, digest = verify_manifest(root, path)
        profile_v3._receipt(root / manifest["paths"]["stability_approval"],
                            "owner_approved_v3_tie_stability", digest)
        marker = (root / manifest["paths"]["output"]).parent / "attempt.started"
        if not marker.is_file() or marker.read_bytes() != canonical({
            "execution_manifest_sha256": digest,
        }):
            raise PermissionError("stability worker lacks active bound attempt")
        print(json.dumps(_worker(root, manifest, args.configuration_index),
                         separators=(",", ":")))
    else:
        print(json.dumps(run(root, path), sort_keys=True))


if __name__ == "__main__":
    main()
