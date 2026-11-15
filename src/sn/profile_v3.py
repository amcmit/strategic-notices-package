"""Four-configuration, approval-gated v3 resource/action-parity profile.

The proposal/review entry points never build cases or call a solver. The
execution entry point requires public generation and an exact-hash owner
receipt. Raw actions cross child-process pipes only; persisted records have
resource data, action-difference distributions and binary agreement.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
from time import monotonic

import numpy
import scipy
from scipy.optimize._highspy import _core as highs_core

from . import planner_v3
from .allocation_v3 import (
    V1_PUBLIC_MANIFEST_SHA256, canonical, validate_allocation,
)
from .core import Notice
from .networks_v3 import (
    V3PublicCase, _approved_hash, build_public_case, load_allocation,
    verify_public_design,
)
from .planner_v3 import NumericalFalseInfeasible, PlannerFailure, run_benchmark


SOURCE_FILES = (
    "core.py", "core_v2.py", "core_v3.py", "allocation_v3.py",
    "networks_v3.py", "planner_v3.py", "profile_v3.py",
)
CONFIGURATIONS = (
    {"name": "presolve-on-threads-1", "presolve": True, "threads": 1},
    {"name": "presolve-off-threads-1", "presolve": False, "threads": 1},
    {"name": "presolve-on-threads-2", "presolve": True, "threads": 2},
    {"name": "presolve-off-threads-2", "presolve": False, "threads": 2},
)
PREFLIGHT_WALL_SECONDS_PER_WORKER = 30
CAPS = {
    "per_mip_seconds": 30,
    "global_wall_seconds": 10800,
    "per_config_peak_rss_mib": 2048,
    "saved_bytes": 67108864,
    "maximum_paths": 240,
    "maximum_rolling_decisions": 4800,
    "maximum_mip_phases": 14400,
    "maximum_mip_phases_per_config": 3600,
    "retries": 0,
}
BINS = (0.0, 1e-10, 1e-9, 1e-8, 1e-7, 1e-6, 1e-5, 1e-4, 1e-3, 1.0)
PROFILE_TARGETS = (
    (("R1", "interior", "serial", "small"),
     ("R1", "interior", "assembly", "large"),
     ("R1", "inventory_closed", "serial", "small"),
     ("R1", "fee_closed", "planted", "small"),
     ("R2", "contrast", "serial", "small"),
     ("R2", "contrast", "assembly", "large"),
     ("R3", "contrast", "serial", "small"),
     ("R3", "contrast", "distribution", "large"),
     ("R4", "contrast", "serial", "small"),
     ("R4", "contrast", "assembly", "large")),
    (("R1", "interior", "distribution", "small"),
     ("R1", "interior", "layered_dual", "large"),
     ("R1", "inventory_closed", "assembly", "large"),
     ("R1", "fee_closed", "serial", "large"),
     ("R2", "contrast", "distribution", "small"),
     ("R2", "contrast", "layered_dual", "large"),
     ("R3", "contrast", "assembly", "small"),
     ("R3", "contrast", "layered_dual", "large"),
     ("R4", "contrast", "assembly", "small"),
     ("R4", "contrast", "distribution", "large")),
    (("R1", "interior", "planted", "small"),
     ("R1", "interior", "serial", "large"),
     ("R1", "inventory_closed", "distribution", "small"),
     ("R1", "fee_closed", "assembly", "small"),
     ("R2", "contrast", "assembly", "small"),
     ("R2", "contrast", "planted", "large"),
     ("R3", "contrast", "planted", "small"),
     ("R3", "contrast", "serial", "large"),
     ("R4", "contrast", "layered_dual", "small"),
     ("R4", "contrast", "planted", "large")),
)


class _ResourceCapError(RuntimeError):
    pass


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _rss_mib() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def source_hashes() -> dict[str, str]:
    root = Path(__file__).parent
    return {name: _digest(root / name) for name in SOURCE_FILES}


def selected_case_ids(allocation: dict) -> tuple[str, ...]:
    """Choose declared family/size targets across all regimes and rotations."""
    validate_allocation(allocation)
    result = []
    for candidate, targets in zip(allocation["candidates"], PROFILE_TARGETS):
        rows = candidate["cases"]
        for regime, category, family, size in targets:
            eligible = [row["case_id"] for row in rows
                        if (row["regime"], row["category"],
                            row["family"], row["size"]) ==
                        (regime, category, family, size)]
            if not eligible:
                raise ValueError("v3 profile target absent from allocation")
            result.append(min(eligible))
    if len(result) != 30 or len(set(result)) != 30:
        raise ValueError("v3 profile sample must contain 30 unique public case IDs")
    return tuple(result)


def proposed_manifest(root: Path) -> dict:
    allocation_path = root / "configs" / "proposed-v3-allocation.json"
    raw = allocation_path.read_bytes()
    if canonical(json.loads(raw)) != raw:
        raise ValueError("v3 allocation proposal must be canonical")
    allocation = json.loads(raw)
    validate_allocation(allocation)
    ids = selected_case_ids(allocation)
    design_path = root / "configs" / "v3-design" / "manifest.json"
    design_raw = design_path.read_bytes()
    if canonical(json.loads(design_raw)) != design_raw:
        raise ValueError("v3 public design manifest must be canonical")
    design = json.loads(design_raw)
    if design["allocation_sha256"] != sha256(raw).hexdigest():
        raise ValueError("v3 public design differs from approved allocation")
    hashes = {row["case_id"]: row["content_sha256"] for row in design["cases"]}
    if len(hashes) != design["case_count"] or set(ids) - set(hashes):
        raise ValueError("v3 profile public content binding is incomplete")
    return {
        "schema": 1,
        "status": "proposal_pending_owner_profile_approval",
        "protocol_sha256": _digest(root / "docs" / "PROPOSED_V3_PROTOCOL_RECONCILED.md"),
        "allocation_sha256": sha256(raw).hexdigest(),
        "public_slot_manifest_sha256": V1_PUBLIC_MANIFEST_SHA256,
        "public_design_manifest_sha256": sha256(design_raw).hexdigest(),
        "public_case_content_sha256": {case_id: hashes[case_id] for case_id in ids},
        "source_sha256": source_hashes(),
        "environment": {
            "python": sys.version.split()[0],
            "numpy": numpy.__version__,
            "scipy": scipy.__version__,
            "highs": (f"{highs_core.HIGHS_VERSION_MAJOR}."
                      f"{highs_core.HIGHS_VERSION_MINOR}."
                      f"{highs_core.HIGHS_VERSION_PATCH}"),
            "platform": sys.platform,
        },
        "paths": {
            "allocation": "configs/proposed-v3-allocation.json",
            "public_slots": "configs/design/manifest.json",
            "allocation_approval": "configs/owner-approvals/v3-allocation.json",
            "public_design": "configs/v3-design",
            "profile_approval": "configs/owner-approvals/v3-resource-profile-r4.json",
            "preflight_output": "results/v3_resource_profile_r4/preflight.json",
            "first_solve_marker": "results/v3_resource_profile_r4/first_solve.started",
            "output": "results/v3_resource_profile_r4/profile.json",
        },
        "public_case_ids": list(ids),
        "truthful_cells": ["short/minor", "long/major"],
        "configurations": list(CONFIGURATIONS),
        "configuration_process_rule": "launch one fresh Python process per configuration; never switch HiGHS thread count in a process",
        "solver_phase_presolve_rule": "primary and qualification branch follow the configuration; every secondary solve has presolve off",
        "preflight_rule": "before approval consumption, launch four zero-solve configuration workers to check in-process peak RSS, IPC, and output writing",
        "preflight_wall_seconds_per_worker": PREFLIGHT_WALL_SECONDS_PER_WORKER,
        "stop_policy": "a zero-solve preflight stop does not consume an attempt; entry into the first MIP solve does, even if it later stops",
        "public_content_binding": "before solving, regenerate and byte-verify every public case from the bound allocation, generator source, master seed and public slot manifest; check exact selected content hashes",
        "path_failure_rule": "collect every path-level failure by type and cause, continue within caps; stop on a global resource cap or worker failure",
        "resource_caps": CAPS,
        "saved_outputs": [
            "phase-type time, gap, certificate-byte and model-size summaries",
            "per-coordinate absolute-action-difference histogram and quantiles by solver configuration",
            "qualification binary agreement counts and all-coordinate equality counts",
            "peak memory, elapsed time, stops and exact input hashes",
            "per-path failure type and cause, with numerical false-infeasible events identified separately",
        ],
        "scope": {
            "strategic_quantities_saved": False,
            "payoffs_saved": False,
            "false_notice_paths": False,
            "calibration": False,
            "confirmation_case_access": False,
            "m5_paused": True,
        },
    }


def verify_manifest(root: Path, path: Path) -> tuple[dict, str]:
    root = root.resolve()
    path = path.resolve()
    if path != root / "configs" / "proposed-v3-resource-profile-r4.json":
        raise ValueError("unexpected v3 resource-profile manifest path")
    raw = path.read_bytes()
    if canonical(json.loads(raw)) != raw:
        raise ValueError("v3 resource-profile manifest is noncanonical")
    manifest = json.loads(raw)
    if manifest != proposed_manifest(root):
        raise ValueError("v3 resource-profile manifest differs from bound sources")
    return manifest, sha256(raw).hexdigest()


def _receipt(path: Path, decision: str, execution_sha256: str) -> None:
    if not path.is_file():
        raise PermissionError(f"owner receipt for {decision} is absent")
    raw = path.read_bytes()
    expected = {"schema": 1, "decision": decision,
                "execution_manifest_sha256": execution_sha256}
    if canonical(json.loads(raw)) != raw or json.loads(raw) != expected:
        raise PermissionError(f"owner receipt for {decision} does not bind exact hash")


def _quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (position - low) * (ordered[high] - ordered[low])


def _distribution(values: list[float]) -> dict:
    counts = [0] * (len(BINS) + 1)
    for value in values:
        placed = False
        for index, upper in enumerate(BINS):
            if value <= upper:
                counts[index] += 1
                placed = True
                break
        if not placed:
            counts[-1] += 1
    return {
        "count": len(values), "max": max(values, default=None),
        "median": _quantile(values, 0.5), "p95": _quantile(values, 0.95),
        "p99": _quantile(values, 0.99),
        "histogram_upper_edges": list(BINS) + [None],
        "histogram_counts": counts,
    }


def compare_actions(baseline: dict, variant: dict) -> dict:
    """Return distributions and decisions only; never return raw actions."""
    if set(baseline) != set(variant):
        raise ValueError("profile configurations have different path IDs")
    differences: dict[str, list[float]] = defaultdict(list)
    binary_total = binary_agreed = coordinate_total = coordinate_exact = 0
    for path_id in sorted(baseline):
        first, second = baseline[path_id], variant[path_id]
        if len(first) != len(second):
            raise ValueError("profile configurations have different path lengths")
        for left, right in zip(first, second):
            binary_total += 1
            binary_agreed += int(left["qualify"] == right["qualify"])
            for name in sorted((set(left) | set(right)) - {"qualify"}):
                delta = abs(left.get(name, 0.0) - right.get(name, 0.0))
                differences[name].append(delta)
                coordinate_total += 1
                coordinate_exact += int(delta == 0.0)
    return {
        "qualification_binary_agreed": binary_agreed,
        "qualification_binary_total": binary_total,
        "all_coordinate_exact": coordinate_exact,
        "all_coordinate_total": coordinate_total,
        "absolute_coordinate_differences": {
            kind: _distribution(values) for kind, values in sorted(differences.items())
        },
    }


def summarize_completed_paths(children: list[dict]) -> dict:
    """Compare only paths completed in every configuration; preserve failures."""
    if len(children) != len(CONFIGURATIONS):
        raise ValueError("v3 profile requires four configuration records")
    if sum(item["attempted_paths"] for item in children) != 240:
        raise RuntimeError("v3 profile attempted workload count mismatch")
    common_ids = set.intersection(*(set(item["actions"]) for item in children))
    baseline = {name: children[0]["actions"][name] for name in common_ids}
    comparisons = {
        item["configuration"]: compare_actions(
            baseline, {name: item["actions"][name] for name in common_ids})
        for item in children[1:]
    }
    failures = {item["configuration"]: item["path_failures"] for item in children}
    counts = {name: dict(sorted((kind, sum(row["type"] == kind for row in rows))
                                for kind in {row["type"] for row in rows}))
              for name, rows in failures.items()}
    return {
        "action_parity_common_path_count": len(common_ids),
        "action_parity_excluded_path_count": 60 - len(common_ids),
        "action_parity_vs_baseline": comparisons,
        "path_failure_count": sum(len(rows) for rows in failures.values()),
        "path_failure_counts_by_type": counts,
        "path_failures": failures,
    }


def _action_row(action, case: V3PublicCase) -> dict:
    row = {"qualify": action.qualify,
           "order": action.primary_order, "spot": action.spot_order}
    for recipe in case.network.recipes:
        key = f"produce/{recipe.site}/{recipe.output}"
        row[key] = sum(item.quantity for item in action.production
                       if (item.site, item.product) == (recipe.site, recipe.output))
    for index in range(len(case.network.lanes)):
        row[f"ship/{index}"] = sum(item.quantity for item in action.shipments
                                   if item.lane_index == index)
    return row


def _path_failure(path_id: str, error: Exception) -> dict:
    """Record a path stop without persisting objective or action levels."""
    row = {"path_id": path_id, "type": type(error).__name__}
    if isinstance(error, NumericalFalseInfeasible):
        event = error.event
        row.update({"cause": "secondary infeasible after certified primary",
                    "period": event.period,
                    "qualification_classification": event.qualification_classification,
                    "primary_status": event.primary_status,
                    "secondary_status": event.secondary_status,
                    "presolve": event.presolve,
                    "secondary_presolve": event.secondary_presolve,
                    "threads": event.threads})
    elif isinstance(error, PlannerFailure):
        row.update({"cause": "solver certificate did not close",
                    "phase": error.certificate.phase,
                    "solver_status": error.certificate.status,
                    "message_sha256": sha256(error.certificate.message.encode()).hexdigest()})
    else:
        message = str(error)
        row["cause"] = (
            "secondary primary-cost ceiling check" if "primary cost ceiling" in message
            else "executed-action feasibility" if "executed" in message
            else "planner/core parity or physical transition" if "core" in message
            else "path execution exception"
        )
        row["message_sha256"] = sha256(message.encode()).hexdigest()
    return row


def _resource_cap_check(caps: dict, started: float, certificate_bytes: int) -> None:
    if (monotonic() - started > caps["global_wall_seconds"]
            or _rss_mib() > caps["per_config_peak_rss_mib"]
            or certificate_bytes > caps["saved_bytes"]):
        raise _ResourceCapError("v3 profile worker resource cap reached")


def _mark_first_solve(root: Path, manifest: dict, configuration: str,
                      phase: str) -> None:
    marker = root / manifest["paths"]["first_solve_marker"]
    if not marker.exists():
        with marker.open("xb") as handle:
            handle.write(canonical({
                "execution_manifest_sha256": sha256(canonical(manifest)).hexdigest(),
                "first_configuration": configuration,
                "first_phase": phase,
            }))


def _worker(root: Path, manifest: dict, configuration_index: int) -> dict:
    paths = manifest["paths"]
    approval = root / paths["allocation_approval"]
    allocation = load_allocation(
        root / paths["allocation"], root / paths["public_slots"],
        _approved_hash(approval),
    )
    rows = {row["case_id"]: row for candidate in allocation["candidates"]
            for row in candidate["cases"]}
    ids = manifest["public_case_ids"]
    if set(ids) - set(rows):
        raise ValueError("profile references unknown public case ID")
    configuration = manifest["configurations"][configuration_index]
    caps = manifest["resource_caps"]
    phase_rows = []
    phase_count = 0
    certificate_bytes = 0
    started = monotonic()
    original_solve = planner_v3._solve

    def measured_solve(model, objective, time_limit, phase, **kwargs):
        nonlocal phase_count, certificate_bytes
        if phase_count >= caps["maximum_mip_phases_per_config"]:
            raise _ResourceCapError("v3 profile per-configuration MIP-phase cap reached")
        _mark_first_solve(root, manifest, configuration["name"], phase)
        phase_count += 1
        begun = monotonic()
        try:
            vector, certificate = original_solve(model, objective, time_limit,
                                                  phase, **kwargs)
        except PlannerFailure as error:
            certificate = error.certificate
            vector = None
        finally:
            _resource_cap_check(caps, started, certificate_bytes)
        elapsed = monotonic() - begun
        certificate_bytes += len(canonical(asdict(certificate)))
        phase_rows.append({
            "phase": phase, "status": certificate.status,
            "elapsed_seconds": elapsed,
            "absolute_gap": (abs(certificate.primal - certificate.dual)
                             if certificate.primal is not None and certificate.dual is not None
                             else None),
            "relative_gap": certificate.relative_gap,
            "certificate_bytes": len(canonical(asdict(certificate))),
            "variables": certificate.model_variables,
            "rows": certificate.model_constraints,
            "nonzeros": certificate.model_nonzeros,
        })
        _resource_cap_check(caps, started, certificate_bytes)
        if elapsed > caps["per_mip_seconds"] + 0.01:
            raise _ResourceCapError("v3 profile per-MIP time cap reached")
        if vector is None:
            raise PlannerFailure(certificate)
        return vector, certificate

    planner_v3._solve = measured_solve
    actions: dict[str, list[dict]] = {}
    failures: list[dict] = []
    try:
        for case_id in ids:
            try:
                case = build_public_case(rows[case_id])
            except Exception as error:
                for cell in manifest["truthful_cells"]:
                    failures.append(_path_failure(f"{case_id}|{cell}", error))
                _resource_cap_check(caps, started, certificate_bytes)
                continue
            for cell in manifest["truthful_cells"]:
                path_id = f"{case_id}|{cell}"
                actual_type, notice_type = cell.split("/")
                schedule = case.short_capacity if actual_type == "short" else case.long_capacity
                try:
                    rollout = run_benchmark(
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
                        presolve=configuration["presolve"],
                        threads=configuration["threads"],
                    )
                    if len(rollout.actions) != 20:
                        raise RuntimeError("v3 profile path ended before period 20")
                    actions[path_id] = [_action_row(action, case)
                                        for action in rollout.actions]
                except _ResourceCapError:
                    raise
                except Exception as error:
                    failures.append(_path_failure(path_id, error))
                _resource_cap_check(caps, started, certificate_bytes)
    finally:
        planner_v3._solve = original_solve
    if len(actions) + len(failures) != 60:
        raise RuntimeError("v3 profile worker did not attempt 60 truthful paths")
    return {"configuration": configuration["name"], "actions": actions,
            "path_failures": failures, "attempted_paths": len(actions) + len(failures),
            "phase_rows": phase_rows, "phase_count": phase_count,
            "peak_rss_mib": _rss_mib(), "elapsed_seconds": monotonic() - started}


def _phase_summary(rows: list[dict]) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[row["phase"]].append(row)
    return {kind: {
        "count": len(items),
        "statuses": dict(sorted((str(status), sum(x["status"] == status for x in items))
                                for status in {x["status"] for x in items})),
        "elapsed_seconds": _distribution([x["elapsed_seconds"] for x in items]),
        "absolute_gap": _distribution([x["absolute_gap"] for x in items
                                        if x["absolute_gap"] is not None]),
        "relative_gap": _distribution([x["relative_gap"] for x in items
                                        if x["relative_gap"] is not None]),
        "certificate_bytes": _distribution([x["certificate_bytes"] for x in items]),
        "maximum_variables": max(x["variables"] for x in items),
        "maximum_rows": max(x["rows"] for x in items),
        "maximum_nonzeros": max(x["nonzeros"] for x in items),
    } for kind, items in sorted(groups.items())}


def _run_worker_process(command: list[str], root: Path, environment: dict[str, str],
                        *, wall_seconds: float) -> subprocess.CompletedProcess:
    """Bound wall time; workers read and enforce their own peak RSS."""
    process = subprocess.Popen(command, cwd=root, env=environment,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True)
    started = monotonic()
    try:
        while True:
            remaining = wall_seconds - (monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("v3 profile worker wall cap reached")
            try:
                stdout, stderr = process.communicate(timeout=min(0.25, remaining))
                return subprocess.CompletedProcess(command, process.returncode,
                                                   stdout, stderr)
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.communicate()
        raise


def _preflight_worker(root: Path, manifest: dict, index: int,
                      probe_dir: Path) -> dict:
    """Exercise a fresh worker's RSS reading, file write, and stdout IPC."""
    parent = (root / manifest["paths"]["preflight_output"]).parent.resolve()
    if not probe_dir.resolve().is_relative_to(parent):
        raise ValueError("v3 preflight probe directory escapes output root")
    config = manifest["configurations"][index]
    peak = _rss_mib()
    if not math.isfinite(peak) or peak <= 0:
        raise RuntimeError("v3 preflight peak RSS reading is invalid")
    if peak > manifest["resource_caps"]["per_config_peak_rss_mib"]:
        raise _ResourceCapError("v3 preflight worker RSS cap reached")
    payload = {"configuration": config["name"], "index": index,
               "peak_rss_mib": peak, "mip_phases": 0,
               "probe": "v3-profile-preflight-output-v1"}
    probe = probe_dir / f"configuration-{index}.json"
    raw = canonical(payload)
    probe.write_bytes(raw)
    if probe.read_bytes() != raw:
        raise OSError("v3 preflight worker output read-back mismatch")
    return payload


def preflight(root: Path, manifest_path: Path) -> dict:
    """Run all four zero-solve probes; never read or consume an approval."""
    root = root.resolve()
    manifest, digest = verify_manifest(root, manifest_path)
    output = root / manifest["paths"]["preflight_output"]
    output.parent.mkdir(parents=True, exist_ok=True)
    started = monotonic()
    rows = []
    with tempfile.TemporaryDirectory(prefix="probe-", dir=output.parent) as folder:
        probe_dir = Path(folder)
        for index, config in enumerate(manifest["configurations"]):
            environment = {**os.environ, "PYTHONPATH": str(root / "src")}
            command = [
                sys.executable, "-m", "sn.profile_v3", "preflight-worker",
                "--repository-root", str(root), "--manifest", str(manifest_path),
                "--configuration-index", str(index),
                "--preflight-dir", str(probe_dir),
            ]
            try:
                completed = _run_worker_process(
                    command, root, environment,
                    wall_seconds=PREFLIGHT_WALL_SECONDS_PER_WORKER,
                )
                if completed.returncode != 0:
                    raise RuntimeError(f"worker exit {completed.returncode}: "
                                       f"{completed.stderr[-1000:]}")
                payload = json.loads(completed.stdout)
                probe = probe_dir / f"configuration-{index}.json"
                if (payload != json.loads(probe.read_bytes())
                        or probe.read_bytes() != canonical(payload)
                        or payload["configuration"] != config["name"]
                        or payload["index"] != index
                        or payload["mip_phases"] != 0
                        or not math.isfinite(payload["peak_rss_mib"])
                        or payload["peak_rss_mib"] <= 0):
                    raise ValueError("v3 preflight IPC/output mismatch")
                rows.append({"configuration": config["name"], "status": "passed",
                             "peak_rss_mib": payload["peak_rss_mib"]})
            except Exception as error:
                rows.append({"configuration": config["name"], "status": "failed",
                             "cause": type(error).__name__,
                             "message": str(error)[:1000]})
    result = {
        "status": ("passed" if all(row["status"] == "passed" for row in rows)
                   else "failed"),
        "execution_manifest_sha256": digest,
        "configuration_workers": rows,
        "elapsed_seconds": monotonic() - started,
        "mip_solves": 0,
        "approval_consumed": False,
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
                "preflight_output_sha256": preflight_result["output_sha256"],
                "execution_manifest_sha256": digest}
    paths = manifest["paths"]
    _receipt(root / paths["profile_approval"],
             "owner_approved_v3_resource_profile", digest)
    approval_path = root / paths["allocation_approval"]
    if not approval_path.is_file():
        raise PermissionError("v3 allocation approval is absent")
    if _approved_hash(approval_path) != manifest["allocation_sha256"]:
        raise PermissionError("v3 allocation receipt does not bind this allocation")
    verification = verify_public_design(
        root, root / paths["allocation"], root / paths["public_slots"],
        approval_path, root / paths["public_design"],
    )
    if verification["manifest_sha256"] != manifest["public_design_manifest_sha256"]:
        raise ValueError("v3 profile public design manifest hash mismatch")
    output = root / paths["output"]
    marker = output.parent / "attempt.started"
    first_solve = root / paths["first_solve_marker"]
    if output.exists() or marker.exists() or first_solve.exists():
        raise FileExistsError("v3 profile attempt already exists; zero retries")
    output.parent.mkdir(parents=True, exist_ok=True)
    marker.write_bytes(canonical({"execution_manifest_sha256": digest}))
    started = monotonic()
    children = []
    try:
        for index, configuration in enumerate(CONFIGURATIONS):
            remaining = manifest["resource_caps"]["global_wall_seconds"] - (monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("v3 profile global wall cap reached")
            env = {**os.environ, "PYTHONPATH": str(root / "src")}
            command = [
                sys.executable, "-m", "sn.profile_v3", "worker",
                "--repository-root", str(root), "--manifest", str(manifest_path),
                "--configuration-index", str(index),
            ]
            completed = _run_worker_process(
                command, root, env, wall_seconds=remaining,
            )
            if completed.returncode != 0:
                raise RuntimeError(f"{configuration['name']} stopped: "
                                   f"{completed.stderr[-2000:]}")
            if len(completed.stdout.encode()) > manifest["resource_caps"]["saved_bytes"]:
                raise RuntimeError("v3 profile worker IPC cap reached")
            child = json.loads(completed.stdout)
            if child["peak_rss_mib"] > manifest["resource_caps"]["per_config_peak_rss_mib"]:
                raise _ResourceCapError("v3 profile worker RSS cap reached")
            children.append(child)
        total_phases = sum(item["phase_count"] for item in children)
        if total_phases > manifest["resource_caps"]["maximum_mip_phases"]:
            raise RuntimeError("v3 profile global MIP-phase cap reached")
        path_summary = summarize_completed_paths(children)
        total_failures = path_summary["path_failure_count"]
        result = {
            "status": ("completed_with_path_failures" if total_failures else "completed"),
            "execution_manifest_sha256": digest,
            "public_design_manifest_sha256": verification["manifest_sha256"],
            "case_count": len(manifest["public_case_ids"]),
            "attempted_path_count": sum(item["attempted_paths"] for item in children),
            "path_count": sum(len(item["actions"]) for item in children),
            "rolling_decisions": sum(sum(len(path) for path in item["actions"].values())
                                     for item in children),
            **path_summary,
            "mip_phases": total_phases,
            "elapsed_seconds": monotonic() - started,
            "configuration_resource": {
                item["configuration"]: {
                    "mip_phases": item["phase_count"],
                    "peak_rss_mib": item["peak_rss_mib"],
                    "elapsed_seconds": item["elapsed_seconds"],
                    "phase_types": _phase_summary(item["phase_rows"]),
                } for item in children
            },
            "strategic_quantities_saved": False,
        }
    except Exception as error:
        if not first_solve.exists():
            marker.unlink(missing_ok=True)
            zero_solve = {
                "status": "stopped_zero_solve",
                "execution_manifest_sha256": digest,
                "stop_type": type(error).__name__, "stop_reason": str(error),
                "mip_solves": 0, "approval_consumed": False,
            }
            preflight_output = root / paths["preflight_output"]
            raw = canonical(zero_solve)
            preflight_output.write_bytes(raw)
            return {"status": zero_solve["status"],
                    "preflight_output_sha256": sha256(raw).hexdigest(),
                    "execution_manifest_sha256": digest,
                    "mip_solves": 0, "approval_consumed": False}
        result = {
            "status": "stopped", "execution_manifest_sha256": digest,
            "elapsed_seconds": monotonic() - started,
            "stop_type": type(error).__name__, "stop_reason": str(error),
            "strategic_quantities_saved": False,
        }
    raw = canonical(result)
    if len(raw) > manifest["resource_caps"]["saved_bytes"]:
        raise RuntimeError("v3 profile saved-output cap reached")
    output.write_bytes(raw)
    return {"status": result["status"], "output_sha256": sha256(raw).hexdigest(),
            "execution_manifest_sha256": digest}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.profile_v3")
    parser.add_argument("command", choices=("propose", "review", "preflight",
                                            "preflight-worker", "run", "worker"))
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--configuration-index", type=int)
    parser.add_argument("--preflight-dir", type=Path)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    path = args.manifest or root / "configs" / "proposed-v3-resource-profile-r4.json"
    if args.command == "propose":
        if path.exists():
            raise FileExistsError("v3 profile proposal already exists")
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
            raise ValueError("preflight worker needs configuration index and probe directory")
        manifest, _ = verify_manifest(root, path)
        print(json.dumps(_preflight_worker(root, manifest, args.configuration_index,
                                           args.preflight_dir), separators=(",", ":")))
    elif args.command == "worker":
        if args.configuration_index not in range(4):
            raise ValueError("worker configuration index must be 0..3")
        manifest, digest = verify_manifest(root, path)
        _receipt(root / manifest["paths"]["profile_approval"],
                 "owner_approved_v3_resource_profile", digest)
        marker = (root / manifest["paths"]["output"]).parent / "attempt.started"
        if not marker.is_file() or marker.read_bytes() != canonical({
            "execution_manifest_sha256": digest,
        }):
            raise PermissionError("v3 profile worker lacks active bound attempt")
        print(json.dumps(_worker(root, manifest, args.configuration_index),
                         separators=(",", ":")))
    else:
        print(json.dumps(run(root, path), sort_keys=True))


if __name__ == "__main__":
    main()
