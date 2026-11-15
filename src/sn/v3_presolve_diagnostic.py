"""Bound, approval-gated presolve diagnostic on frozen v3 single-solve sources.

Proposal/review are zero-solve. Stage workers exchange physical states through
process pipes only; the persistent result contains solver quantities and
failure evidence, never supplier payoffs, H1 quantities, or physical ledgers.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback
from time import monotonic

import numpy as np

from . import planner_v3_single as planner, profile_v3
from .allocation_v3 import canonical
from .core import Notice, Order, Realization, State, Stock, Transit
from .core_v3 import initial_state_v3, step_v3
from .networks_v3 import _approved_hash, build_public_case, load_allocation


SCOPE_SHA256 = "74ffdab67df0f9625beff98a2ecddb456a0b11038e51b29839fe9abf9b4cb21b"
MANIFEST_REL = "configs/proposed-v3-single-presolve-diagnostic-execution.json"
DECISION = "owner_approved_v3_single_presolve_diagnostic"
SOURCE_FILES = (
    "src/sn/allocation_v3.py", "src/sn/core.py", "src/sn/core_v2.py",
    "src/sn/core_v3.py", "src/sn/handoff_v3_single.py",
    "src/sn/networks_v3.py", "src/sn/planner_v3_single.py",
    "src/sn/profile_v3.py", "src/sn/stability_v3_single.py",
    "src/sn/v3_presolve_diagnostic.py",
)


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _scope(root: Path) -> dict:
    path = root / "configs/proposed-v3-single-presolve-diagnostic.json"
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != SCOPE_SHA256 or canonical(json.loads(raw)) != raw:
        raise ValueError("approved diagnostic scope hash or canonical form changed")
    scope = json.loads(raw)
    if scope["resource_caps"]["maximum_total_mip_phases"] != 5840:
        raise ValueError("diagnostic scope solve cap changed")
    for path, field in (
        ("docs/V3_PRESOLVE_DIAGNOSTIC_PROPOSAL.md", "proposal_sha256"),
        ("configs/proposed-v3-allocation.json", "allocation_sha256"),
        ("configs/v3-design/manifest.json", "public_design_manifest_sha256"),
        ("configs/proposed-v3-single-stability-r2.json", "full_stability_manifest_sha256"),
    ):
        if _digest(root / path) != scope[field]:
            raise ValueError(f"approved diagnostic scope input changed: {path}")
    for name, expected in scope["source_sha256"].items():
        if _digest(root / name) != expected:
            raise ValueError(f"frozen source changed: {name}")
    for name, expected in scope["progress_sha256"].items():
        if _digest(root / "results/v3_single_full_r2/progress" / f"{name}.jsonl") != expected:
            raise ValueError(f"frozen progress changed: {name}")
    if (_digest(root / "results/v3_single_full_r2/stability.json") !=
            scope["full_stability_result_sha256"]):
        raise ValueError("frozen full stability result changed")
    return scope


def proposed_manifest(root: Path) -> dict:
    root = root.resolve()
    scope = _scope(root)
    caps = scope["resource_caps"]
    return {
        "schema": 1, "kind": "v3_single_presolve_diagnostic_execution",
        "status": "proposal_pending_owner_named_execution_hash",
        "scope_sha256": SCOPE_SHA256,
        "classification_note_sha256": _digest(
            root / "docs/V3_PRESOLVE_DIAGNOSTIC_IMPLEMENTATION.md"),
        "source_sha256": {name: _digest(root / name) for name in SOURCE_FILES},
        "allocation_sha256": _digest(root / "configs/proposed-v3-allocation.json"),
        "public_design_manifest_sha256": _digest(root / "configs/v3-design/manifest.json"),
        "selected_path_digest_pairs": scope["selection"]["path_digest_pairs"],
        "configurations": [{"name": "presolve-off", "presolve": False, "threads": 1},
                           {"name": "presolve-on", "presolve": True, "threads": 1}],
        "periods_per_path": 20,
        "classification": {
            "primary_equal": "abs(C_on-C_off) <= max(recompute_on+recompute_off,64*eps*max(1,abs(C_on),abs(C_off)))",
            "tie_equal": "abs(T_on-T_off) <= 64*eps*max(1,n_current_tie_terms)",
            "groups": ["equal_primary_worse_tie_on", "equal_primary_equal_tie",
                       "unequal_primary"],
            "unexpected_equal_primary_better_tie_on": "record taxonomy failure",
            "resolution_floor": "(abs(C_on-C_off)+tau_on+tau_off+gap_on+gap_off+recompute_on+recompute_off)/1e-5",
            "resolution_floor_interpretation": "minimum score separation above the declared combined numerical budget, not a causal solver threshold",
        },
        "resource_caps": caps,
        "paths": {
            "allocation": "configs/proposed-v3-allocation.json",
            "public_slots": "configs/design/manifest.json",
            "allocation_approval": "configs/owner-approvals/v3-allocation.json",
            "execution_approval": "configs/owner-approvals/v3-single-presolve-diagnostic-execution.json",
            "preflight_output": "results/v3_single_presolve_diagnostic/preflight.json",
            "attempt_marker": "results/v3_single_presolve_diagnostic/attempt.started",
            "first_solve_marker": "results/v3_single_presolve_diagnostic/first_solve.started",
            "output": "results/v3_single_presolve_diagnostic/diagnostic.json",
        },
        "saved_outputs": ["solver objectives, tie scores, reported gaps and shipment differences",
                          "classification counts by group, regime and family",
                          "full failure text and resource accounting"],
        "excluded_outputs": scope["excluded_outputs"],
        "scope": {"calibration": False, "sealing": False,
                  "confirmation_access": False, "m5_paused": True,
                  "retries": 0},
    }


def verify_manifest(root: Path, path: Path) -> tuple[dict, str]:
    root, path = root.resolve(), path.resolve()
    if path != root / MANIFEST_REL:
        raise ValueError("unexpected diagnostic execution manifest path")
    raw = path.read_bytes()
    if canonical(json.loads(raw)) != raw:
        raise ValueError("diagnostic execution manifest is noncanonical")
    manifest = json.loads(raw)
    if manifest != proposed_manifest(root):
        raise ValueError("diagnostic execution manifest differs from bound inputs")
    return manifest, sha256(raw).hexdigest()


def _state_payload(state: State) -> dict:
    return asdict(state)


def _state_from_payload(payload: dict) -> State:
    return State(
        int(payload["period"]),
        tuple(Stock(**item) for item in payload["inventory"]),
        tuple(Stock(**item) for item in payload["backlog"]),
        tuple(Order(**item) for item in payload["outstanding"]),
        tuple(Transit(**item) for item in payload["pipeline"]),
        payload["qualified_at"],
        (tuple(payload["revealed_schedule"])
         if payload["revealed_schedule"] is not None else None),
    )


def _case_rows(root: Path, manifest: dict) -> dict:
    paths = manifest["paths"]
    allocation = load_allocation(
        root / paths["allocation"], root / paths["public_slots"],
        _approved_hash(root / paths["allocation_approval"]),
    )
    return {row["case_id"]: row for candidate in allocation["candidates"]
            for row in candidate["cases"]}


def _problem(case, path_id: str, state: State, presolve: bool,
             time_limit: float) -> tuple[planner.PlanningProblem, Notice, tuple[float, ...]]:
    _, cell = path_id.split("|", 1)
    actual_type, notice_type = cell.split("/")
    if (actual_type, notice_type) not in (("short", "minor"), ("long", "major")):
        raise ValueError("diagnostic permits truthful cells only")
    capacity = case.short_capacity if actual_type == "short" else case.long_capacity
    notice = Notice(capacity, notice_type)
    t = state.period
    problem = planner.PlanningProblem(
        case.network, case.contract, notice, state,
        (planner.Scenario(actual_type, 1.0, capacity),),
        capacity[t], case.baseline_demand[t], case.baseline_demand,
        time_limit, presolve, 1,
    )
    return problem, notice, capacity


def _check_resources(caps: dict, started: float) -> None:
    if monotonic() - started > caps["global_wall_seconds"]:
        raise RuntimeError("diagnostic worker wall cap reached")
    if profile_v3._rss_mib() > caps["peak_rss_mib_per_worker"]:
        raise RuntimeError("diagnostic worker RSS cap reached")


def _failure(path_id: str, period: int | None, error: Exception) -> dict:
    row = {"path_id": path_id, "period": period,
           "type": type(error).__name__, "full_error_text": str(error),
           "traceback": traceback.format_exc()}
    if isinstance(error, planner.PlannerFailure):
        row["solver_certificate"] = asdict(error.certificate)
    return row


def _mark_first_solve(root: Path, manifest: dict) -> None:
    path = root / manifest["paths"]["first_solve_marker"]
    if not path.exists():
        with path.open("xb") as handle:
            handle.write(canonical({"execution_manifest_sha256":
                                    _digest(root / MANIFEST_REL)}))


def _stage1_worker(root: Path, manifest: dict, presolve: bool) -> dict:
    rows = _case_rows(root, manifest)
    caps = manifest["resource_caps"]
    started = monotonic()
    solved = 0
    paths = {}
    failures = []
    digest_key = ("presolve_on_action_digest" if presolve
                  else "presolve_off_action_digest")
    for selected in manifest["selected_path_digest_pairs"]:
        path_id = selected["path_id"]
        current_period = None
        try:
            case_id = path_id.split("|", 1)[0]
            case = build_public_case(rows[case_id])
            state = initial_state_v3(case.network)
            actions, states, state_digests = [], [], []
            for t in range(case.network.horizon):
                current_period = t
                if solved >= caps["maximum_stage1_mip_phases"] // 2:
                    raise RuntimeError("stage-1 per-setting MIP cap reached")
                _check_resources(caps, started)
                payload = _state_payload(state)
                states.append(payload if not presolve else None)
                state_digests.append(sha256(canonical(payload)).hexdigest())
                problem, notice, capacity = _problem(
                    case, path_id, state, presolve, caps["per_mip_seconds"])
                began = monotonic()
                _mark_first_solve(root, manifest)
                solved += 1
                choice = planner.plan(problem)
                if monotonic() - began > caps["per_mip_seconds"] + 0.01:
                    raise RuntimeError("stage-1 per-MIP cap reached")
                actions.append(profile_v3._action_row(choice.action, case))
                state, _ = step_v3(
                    case.network, case.contract, notice, state, choice.action,
                    Realization(capacity[t], case.baseline_demand[t]),
                    revealed_schedule=capacity if t == case.contract.revelation_period else None,
                )
                _check_resources(caps, started)
            if sha256(canonical(actions)).hexdigest() != selected[digest_key]:
                raise ValueError("replayed whole-path action digest differs from frozen run")
            paths[path_id] = {"actions": actions, "states": states,
                              "state_digests": state_digests}
        except Exception as error:
            failures.append(_failure(path_id, current_period, error))
    return {"configuration": "presolve-on" if presolve else "presolve-off",
            "stage": 1, "paths": paths, "failures": failures, "mip_solves": solved,
            "elapsed_seconds": monotonic() - started,
            "peak_rss_mib": profile_v3._rss_mib()}


def _solve_with_score(problem: planner.PlanningProblem) -> tuple[dict, dict]:
    capture = {}
    original_solve = planner._solve

    def measured_solve(model, objective, time_limit, phase, **kwargs):
        vector, certificate = original_solve(model, objective, time_limit, phase, **kwargs)
        tie = planner._tie_vector(model, problem.state.period)
        capture.update({
            "tie_score": float(tie @ vector),
            "tie_term_count": int(np.count_nonzero(tie)),
            "primary_variable_objective": float(np.asarray(model.cost) @ vector),
            "objective_recompute_error": abs(float(objective @ vector) - certificate.primal),
        })
        return vector, certificate

    planner._solve = measured_solve
    try:
        choice = planner.plan(problem)
    finally:
        planner._solve = original_solve
    cert = choice.certificate
    capture.update({
        "primary_objective": choice.expected_cost,
        "solver_primal": cert.primal, "solver_dual": cert.dual,
        "solver_absolute_gap": abs(cert.primal - cert.dual),
        "solver_relative_gap": cert.relative_gap,
        "numeric_closure": cert.checked_primal_dual_absolute_tolerance,
        "solver_status": cert.status, "solver_message": cert.message,
        "certificate_optimal": cert.optimal,
        "tie_score_crosscheck": (choice.combined_objective - choice.expected_cost)
                                / planner.TIE_EPSILON,
    })
    return capture, {"qualify": choice.action.qualify,
                     "order": choice.action.primary_order,
                     "spot": choice.action.spot_order,
                     "production": [(x.site, x.product, x.quantity)
                                    for x in choice.action.production],
                     "shipments": {x.lane_index: x.quantity
                                   for x in choice.action.shipments}}


def _stage2_worker(root: Path, manifest: dict, presolve: bool, tasks: list[dict]) -> dict:
    rows = _case_rows(root, manifest)
    caps = manifest["resource_caps"]
    started = monotonic()
    solved = 0
    results = []
    failures = []
    for task in tasks:
        try:
            if solved >= caps["maximum_stage2_mip_phases"] // 2:
                raise RuntimeError("stage-2 per-setting MIP cap reached")
            _check_resources(caps, started)
            case = build_public_case(rows[task["path_id"].split("|", 1)[0]])
            state = _state_from_payload(task["state"])
            if state.period != task["period"] or sha256(canonical(_state_payload(state))).hexdigest() != task["state_digest"]:
                raise ValueError("paired opening state changed in IPC")
            problem, _, _ = _problem(case, task["path_id"], state, presolve,
                                     caps["per_mip_seconds"])
            began = monotonic()
            _mark_first_solve(root, manifest)
            solved += 1
            quantities, action = _solve_with_score(problem)
            if monotonic() - began > caps["per_mip_seconds"] + 0.01:
                raise RuntimeError("stage-2 per-MIP cap reached")
            _check_resources(caps, started)
            results.append({"path_id": task["path_id"], "period": task["period"],
                            "state_digest": task["state_digest"],
                            "quantities": quantities, "action": action})
        except Exception as error:
            failures.append(_failure(task["path_id"], task["period"], error))
    return {"configuration": "presolve-on" if presolve else "presolve-off",
            "stage": 2, "results": results, "failures": failures,
            "mip_solves": solved, "elapsed_seconds": monotonic() - started,
            "peak_rss_mib": profile_v3._rss_mib()}


def _classify(off: dict, on: dict, *, regime: str, family: str,
              original_off: dict, original_on: dict,
              same_opening_state: bool) -> dict:
    a, b = off["quantities"], on["quantities"]
    cost_difference = b["primary_objective"] - a["primary_objective"]
    tie_difference = b["tie_score"] - a["tie_score"]
    primary_equal_limit = max(
        a["objective_recompute_error"] + b["objective_recompute_error"],
        64 * np.finfo(float).eps * max(
            1, abs(a["primary_objective"]), abs(b["primary_objective"])))
    n_terms = max(a["tie_term_count"], b["tie_term_count"])
    tie_equal_limit = 64 * np.finfo(float).eps * max(1, n_terms)
    if abs(cost_difference) > primary_equal_limit:
        group = "unequal_primary"
    elif tie_difference > tie_equal_limit:
        group = "equal_primary_worse_tie_on"
    elif abs(tie_difference) <= tie_equal_limit:
        group = "equal_primary_equal_tie"
    else:
        group = "unexpected_equal_primary_better_tie_on"
    gaps = (a["solver_absolute_gap"], b["solver_absolute_gap"])
    max_gap = max(gaps)
    uncertainty = (sum(gaps) + a["numeric_closure"] + b["numeric_closure"]
                   + a["objective_recompute_error"] + b["objective_recompute_error"])
    required = (abs(cost_difference) + uncertainty) / planner.TIE_EPSILON

    def shipment_difference(left: dict, right: dict) -> list[dict]:
        first = {int(k): v for k, v in left["shipments"].items()}
        second = {int(k): v for k, v in right["shipments"].items()}
        return [{"lane_index": index, "presolve_off": first.get(index, 0.0),
                 "presolve_on": second.get(index, 0.0),
                 "difference": second.get(index, 0.0) - first.get(index, 0.0)}
                for index in sorted(set(first) | set(second))
                if first.get(index, 0.0) != second.get(index, 0.0)]

    return {
        "path_id": off["path_id"], "period": off["period"],
        "regime": regime, "family": family,
        "group": group, "common_opening_state_digest": off["state_digest"],
        "original_on_opening_state_equal": same_opening_state,
        "primary_objective_difference_on_minus_off": cost_difference,
        "primary_equal_limit": primary_equal_limit,
        "tie_score_difference_on_minus_off": tie_difference,
        "tie_equal_limit": tie_equal_limit,
        "tie_objective_difference_absolute": planner.TIE_EPSILON * abs(tie_difference),
        "tie_difference_to_max_reported_gap": (
            planner.TIE_EPSILON * abs(tie_difference) / max_gap
            if max_gap > 0 else None),
        "reported_gap_zero": max_gap == 0,
        "certificate_resolution_score_floor": required,
        "additional_score_separation_to_floor": max(0.0, required - abs(tie_difference)),
        "floor_within_score_range": required <= 1.0,
        "presolve_off": a, "presolve_on": b,
        "original_shipment_differences": shipment_difference(original_off, original_on),
        "paired_same_state_shipment_differences": shipment_difference(off["action"], on["action"]),
        "other_action_difference_flags": {
            "qualification": off["action"]["qualify"] != on["action"]["qualify"],
            "order": off["action"]["order"] != on["action"]["order"],
            "spot": off["action"]["spot"] != on["action"]["spot"],
            "production": off["action"]["production"] != on["action"]["production"],
        },
    }


def _child(root: Path, manifest_path: Path, command: str, presolve: bool,
           remaining: float, payload: dict | None = None) -> dict:
    env = {**os.environ, "PYTHONPATH": str(root / "src")}
    args = [sys.executable, "-m", "sn.v3_presolve_diagnostic", command,
            "--repository-root", str(root), "--manifest", str(manifest_path),
            "--presolve", "on" if presolve else "off"]
    completed = subprocess.run(args, input=json.dumps(payload) if payload is not None else None,
                               cwd=root, env=env, text=True, capture_output=True,
                               timeout=remaining)
    if completed.returncode != 0:
        raise RuntimeError(f"{command} presolve={presolve} worker exit {completed.returncode}: {completed.stderr}")
    if len(completed.stdout.encode()) > 16777216:
        raise RuntimeError("diagnostic worker IPC byte cap reached")
    parsed, _ = _decode_payload(completed.stdout, "presolve-on" if presolve else "presolve-off")
    return parsed


def _decode_payload(raw: str, expected: str) -> tuple[dict, int]:
    payloads = []
    native_lines = 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        if line.startswith("HighsMipSolverData::"):
            native_lines += 1
            continue
        payloads.append(json.loads(line))
    if len(payloads) != 1 or payloads[0].get("configuration") != expected:
        raise ValueError("diagnostic worker IPC framing mismatch")
    return payloads[0], native_lines


def preflight(root: Path, manifest_path: Path) -> dict:
    manifest, digest = verify_manifest(root, manifest_path)
    output = root / manifest["paths"]["preflight_output"]
    output.parent.mkdir(parents=True, exist_ok=True)
    failures = []
    with tempfile.TemporaryDirectory(dir=output.parent, prefix="preflight-") as folder:
        for presolve in (False, True):
            try:
                payload = _child(root, manifest_path, "preflight-worker", presolve, 30,
                                 {"probe_directory": folder})
                probe = Path(folder) / ("on.json" if presolve else "off.json")
                if (probe.read_bytes() != canonical(payload)
                        or payload["mip_solves"] != 0
                        or not math.isfinite(payload["peak_rss_mib"])):
                    raise ValueError("preflight IPC/output mismatch")
            except Exception as error:
                failures.append({"presolve": presolve, "type": type(error).__name__,
                                 "full_error_text": str(error),
                                 "traceback": traceback.format_exc()})
    result = {"status": "passed" if not failures else "failed", "failures": failures,
              "execution_manifest_sha256": digest, "mip_solves": 0,
              "approval_consumed": False}
    output.write_bytes(canonical(result))
    return result


def run(root: Path, manifest_path: Path) -> dict:
    root = root.resolve()
    manifest, digest = verify_manifest(root, manifest_path)
    pre = preflight(root, manifest_path)
    if pre["status"] != "passed":
        return {"status": "preflight_failed", "mip_solves": 0,
                "execution_manifest_sha256": digest}
    profile_v3._receipt(root / manifest["paths"]["execution_approval"], DECISION, digest)
    output = root / manifest["paths"]["output"]
    marker = root / manifest["paths"]["attempt_marker"]
    first_solve = root / manifest["paths"]["first_solve_marker"]
    if any(path.exists() for path in (output, marker, first_solve)):
        raise FileExistsError("diagnostic attempt already exists; zero retries")
    marker.write_bytes(canonical({"execution_manifest_sha256": digest}))
    started = monotonic()
    caps = manifest["resource_caps"]
    phases = 0
    workers = []
    result = None
    try:
        stage1 = {}
        for presolve in (False, True):
            remaining = caps["global_wall_seconds"] - (monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("diagnostic global wall cap reached")
            child = _child(root, manifest_path, "stage1-worker", presolve, remaining)
            workers.append({k: child[k] for k in ("configuration", "stage", "mip_solves",
                                                 "elapsed_seconds", "peak_rss_mib")})
            phases += child["mip_solves"]
            stage1[child["configuration"]] = child
        failures = stage1["presolve-off"]["failures"] + stage1["presolve-on"]["failures"]
        selected = {item["path_id"] for item in manifest["selected_path_digest_pairs"]}
        common = sorted(set(stage1["presolve-off"]["paths"])
                        & set(stage1["presolve-on"]["paths"]))
        tasks = []
        original = {}
        for path_id in common:
            off = stage1["presolve-off"]["paths"][path_id]
            on = stage1["presolve-on"]["paths"][path_id]
            for period, (left, right) in enumerate(zip(off["actions"], on["actions"])):
                if left != right:
                    task = {"path_id": path_id, "period": period,
                            "state": off["states"][period],
                            "state_digest": off["state_digests"][period]}
                    tasks.append(task)
                    original[(path_id, period)] = (left, right,
                                                   off["state_digests"][period]
                                                   == on["state_digests"][period])
        if len(tasks) > caps["maximum_period_pairs"]:
            raise RuntimeError("diagnostic period-pair cap reached")
        stage2 = {}
        for presolve in (False, True):
            remaining = caps["global_wall_seconds"] - (monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("diagnostic global wall cap reached")
            child = _child(root, manifest_path, "stage2-worker", presolve, remaining,
                           {"tasks": tasks})
            workers.append({k: child[k] for k in ("configuration", "stage", "mip_solves",
                                                 "elapsed_seconds", "peak_rss_mib")})
            phases += child["mip_solves"]
            stage2[child["configuration"]] = child
            failures.extend(child["failures"])
        if phases > caps["maximum_total_mip_phases"]:
            raise RuntimeError("diagnostic total MIP cap reached")
        off_pairs = {(x["path_id"], x["period"]): x
                     for x in stage2["presolve-off"]["results"]}
        on_pairs = {(x["path_id"], x["period"]): x
                    for x in stage2["presolve-on"]["results"]}
        allocation_rows = _case_rows(root, manifest)
        classified = []
        for path_id, period in sorted(set(off_pairs) & set(on_pairs)):
            off_original, on_original, same_state = original[(path_id, period)]
            row = allocation_rows[path_id.split("|", 1)[0]]
            classified.append(_classify(
                off_pairs[(path_id, period)], on_pairs[(path_id, period)],
                regime=row["regime"], family=row["family"],
                original_off={"shipments": {int(k.split("/")[1]): v for k, v in off_original.items() if k.startswith("ship/")}},
                original_on={"shipments": {int(k.split("/")[1]): v for k, v in on_original.items() if k.startswith("ship/")}},
                same_opening_state=same_state,
            ))
        groups = Counter((row["group"], row["regime"], row["family"])
                         for row in classified)
        group_totals = Counter(row["group"] for row in classified)
        regime_totals = Counter((row["group"], row["regime"]) for row in classified)
        family_totals = Counter((row["group"], row["family"]) for row in classified)
        taxonomy_failed = any(row["group"].startswith("unexpected_") for row in classified)
        result = {
            "status": ("completed" if not failures and not taxonomy_failed
                       and len(common) == len(selected) and len(classified) == len(tasks)
                       else "completed_with_unresolved"),
            "execution_manifest_sha256": digest,
            "selected_paths": len(selected), "reproduced_paths": len(common),
            "differing_periods": len(tasks), "classified_periods": len(classified),
            "classification_counts": [
                {"group": group, "regime": regime, "family": family, "count": count}
                for (group, regime, family), count in sorted(groups.items())],
            "group_totals": dict(sorted(group_totals.items())),
            "regime_totals": [
                {"group": group, "regime": regime, "count": count}
                for (group, regime), count in sorted(regime_totals.items())],
            "family_totals": [
                {"group": group, "family": family, "count": count}
                for (group, family), count in sorted(family_totals.items())],
            "records": classified, "failures": failures,
            "mip_solves": phases, "workers": workers,
            "elapsed_seconds": monotonic() - started,
            "peak_worker_rss_mib": max(x["peak_rss_mib"] for x in workers),
            "supplier_payoffs_saved": False, "h1_quantities_saved": False,
        }
    except Exception as error:
        if not first_solve.exists():
            marker.unlink(missing_ok=True)
            zero = {"status": "stopped_zero_solve", "execution_manifest_sha256": digest,
                    "stop_type": type(error).__name__, "full_error_text": str(error),
                    "traceback": traceback.format_exc(), "mip_solves": 0,
                    "approval_consumed": False}
            (root / manifest["paths"]["preflight_output"]).write_bytes(canonical(zero))
            return {"status": zero["status"], "execution_manifest_sha256": digest,
                    "mip_solves": 0}
        result = {"status": "stopped", "execution_manifest_sha256": digest,
                  "stop_type": type(error).__name__, "full_error_text": str(error),
                  "traceback": traceback.format_exc(), "mip_solves_observed": phases,
                  "workers": workers, "elapsed_seconds": monotonic() - started,
                  "supplier_payoffs_saved": False, "h1_quantities_saved": False}
    raw = canonical(result)
    if len(raw) > caps["saved_bytes"]:
        raise RuntimeError("diagnostic saved-output cap reached")
    output.write_bytes(raw)
    return {"status": result["status"], "execution_manifest_sha256": digest,
            "output_sha256": sha256(raw).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.v3_presolve_diagnostic")
    parser.add_argument("command", choices=("propose", "review", "preflight", "run",
                                            "preflight-worker", "stage1-worker",
                                            "stage2-worker"))
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--presolve", choices=("off", "on"))
    args = parser.parse_args()
    root = args.repository_root.resolve()
    path = args.manifest or root / MANIFEST_REL
    if args.command == "propose":
        if path.exists():
            raise FileExistsError("diagnostic execution proposal already exists")
        path.write_bytes(canonical(proposed_manifest(root)))
        print(json.dumps({"manifest_sha256": _digest(path), "mip_solves": 0}))
        return
    manifest, digest = verify_manifest(root, path)
    if args.command == "review":
        print(json.dumps({"manifest_sha256": digest,
                          "selected_paths": len(manifest["selected_path_digest_pairs"]),
                          "maximum_mip_solves": manifest["resource_caps"]["maximum_total_mip_phases"],
                          "mip_solves": 0}))
    elif args.command == "preflight":
        print(json.dumps(preflight(root, path), sort_keys=True))
    elif args.command == "run":
        print(json.dumps(run(root, path), sort_keys=True))
    else:
        if args.presolve is None:
            raise ValueError("worker presolve setting required")
        presolve = args.presolve == "on"
        if args.command == "preflight-worker":
            request = json.loads(sys.stdin.read())
            probe_dir = Path(request["probe_directory"]).resolve()
            parent = (root / manifest["paths"]["preflight_output"]).parent.resolve()
            if not probe_dir.is_relative_to(parent):
                raise ValueError("preflight probe directory escapes output root")
            payload = {"configuration": "presolve-on" if presolve else "presolve-off",
                       "peak_rss_mib": profile_v3._rss_mib(), "mip_solves": 0}
            (probe_dir / ("on.json" if presolve else "off.json")).write_bytes(canonical(payload))
            print(canonical(payload).decode(), end="")
        else:
            profile_v3._receipt(root / manifest["paths"]["execution_approval"], DECISION, digest)
            marker = root / manifest["paths"]["attempt_marker"]
            if marker.read_bytes() != canonical({"execution_manifest_sha256": digest}):
                raise PermissionError("diagnostic worker lacks active bound attempt")
            if args.command == "stage1-worker":
                payload = _stage1_worker(root, manifest, presolve)
            else:
                request = json.loads(sys.stdin.read())
                payload = _stage2_worker(root, manifest, presolve, request["tasks"])
            print(canonical(payload).decode(), end="")


if __name__ == "__main__":
    main()
