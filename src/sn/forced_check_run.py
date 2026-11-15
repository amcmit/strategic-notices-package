"""Approval-gated, bounded forced-qualification objective comparison.

This module is prepared for review. It requires an owner approval receipt
for the frozen execution manifest; none is created by this module.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import gzip
from hashlib import sha256
import json
from pathlib import Path
import resource
import signal
import sys
from time import monotonic

from . import forced_check_engine, planner_forced_v1
from .forced_check_plan import inspect
from .m3 import Task, _key, _read_cached, load_approved, scientific_input, source_hashes
from .planner_forced_v1 import PlannerFailure, SolverCertificate
from .suite import canonical


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _rss_mib() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def _alarm(_number, _frame) -> None:
    raise RuntimeError("forced qualification check wall cap reached")


def classify(cached: dict, forced: SolverCertificate, tolerance: float) -> str:
    if (not forced.optimal or forced.phase != "primary" or forced.primal is None
            or forced.dual is None or cached["primal"] is None or cached["dual"] is None):
        return "solver_unresolved"
    if (forced.model_variables != cached["model_variables"]
            or forced.model_integer_variables != cached["model_integer_variables"]
            or forced.model_constraints != cached["model_constraints"] + 1
            or forced.model_nonzeros != cached["model_nonzeros"] + 1):
        return "formulation_mismatch"
    if forced.primal < cached["dual"] - tolerance:
        return "planner_model_inconsistency"
    if forced.dual > cached["primal"] + tolerance:
        return "strictly_excluded"
    return "overlapping_bounds"


def _receipt(path: Path, execution_sha: str) -> dict:
    if not path.is_file():
        raise PermissionError("owner approval receipt for exact execution manifest is absent")
    raw = path.read_bytes()
    receipt = json.loads(raw)
    if (canonical(receipt) != raw or receipt != {
        "schema": 1,
        "decision": "owner_approved_26_forced_primary_mips",
        "execution_manifest_sha256": execution_sha,
    }):
        raise PermissionError("owner approval receipt does not bind exact execution manifest")
    return receipt


def _load_inputs(root: Path, execution_path: Path, selection_path: Path,
                 precondition_archive: Path, backup_archive: Path,
                 m3_path: Path, m3_run: Path, approval_receipt: Path) -> tuple[dict, dict, dict, dict]:
    execution_raw = execution_path.read_bytes()
    execution = json.loads(execution_raw)
    execution_sha = sha256(execution_raw).hexdigest()
    if canonical(execution) != execution_raw:
        raise ValueError("execution manifest must be canonical JSON")
    _receipt(approval_receipt, execution_sha)
    selection = json.loads(selection_path.read_bytes())
    preflight = inspect(root, selection_path, backup_archive, m3_path, m3_run)
    packed = precondition_archive.read_bytes()
    precondition_raw = gzip.decompress(packed) if precondition_archive.suffix == ".gz" else packed
    precondition = json.loads(precondition_raw)
    expected_sources = {
        "planner_forced_v1.py": _digest(Path(planner_forced_v1.__file__)),
        "forced_check_engine.py": _digest(Path(forced_check_engine.__file__)),
        "forced_check_run.py": _digest(Path(__file__)),
    }
    if (canonical(precondition) != precondition_raw
            or execution["schema"] != 1
            or execution["status"] != "approved_manifest_pending_owner_receipt"
            or execution["selection_manifest_sha256"] != preflight["manifest_sha256"]
            or execution["precondition_archive_sha256"] != sha256(packed).hexdigest()
            or execution["precondition_result_sha256"] != sha256(precondition_raw).hexdigest()
            or execution["source_sha256"] != expected_sources
            or execution["m3_trajectory_tree_sha256"] != selection["m3_trajectory_tree_sha256"]
            or precondition["planner_forced_v1_sha256"] != expected_sources["planner_forced_v1.py"]
            or precondition["unforced_engine_sha256"] != expected_sources["forced_check_engine.py"]
            or precondition["manifest_sha256"] != preflight["manifest_sha256"]
            or precondition["m3_trajectory_tree_sha256"] != selection["m3_trajectory_tree_sha256"]
            or precondition["compared_state_count"] != 26
            or precondition["status"] != "unforced_versioned_planner_matches_cache"
            or precondition["new_unforced_primary_mip_solves"] != 26
            or len(precondition["rows"]) != 26
            or precondition["new_forced_mip_solves"] != 0
            or precondition["max_abs_primal_delta"] > 1e-8
            or precondition["max_abs_dual_delta"] > 1e-8
            or execution["planned_forced_primary_mip_solves"] != 26
            or execution["planned_tie_break_solves"] != 0
            or execution["planned_new_physical_trajectories"] != 0
            or execution["resource_caps"] != selection["resource_caps"]
            or any(execution[key] for key in (
                "confirmation_case_access", "local_model_calls", "paid_api_calls",
                "generator_changes", "parameter_changes", "equilibrium_selection_work"))
            or not execution["m5_paused"]):
        raise ValueError("forced-check execution provenance or scope mismatch")
    return execution, selection, precondition, {"execution_sha256": execution_sha,
                                                "receipt_sha256": _digest(approval_receipt)}


def run(root: Path, execution_path: Path, selection_path: Path,
        precondition_archive: Path, backup_archive: Path,
        m3_path: Path, m3_run: Path, approval_receipt: Path,
        output: Path) -> dict:
    if output.exists() or not output.resolve().is_relative_to(root):
        raise ValueError("forced-check output must be new and inside repository")
    started = monotonic()
    preliminary = json.loads(execution_path.read_bytes())
    actual_paths = {
        "execution_manifest": execution_path.relative_to(root).as_posix(),
        "selection_manifest": selection_path.relative_to(root).as_posix(),
        "precondition_archive": precondition_archive.relative_to(root).as_posix(),
        "backup_archive": backup_archive.relative_to(root).as_posix(),
        "m3_manifest": m3_path.relative_to(root).as_posix(),
        "m3_run": m3_run.relative_to(root).as_posix(),
        "approval_receipt": approval_receipt.relative_to(root).as_posix(),
        "output": output.relative_to(root).as_posix(),
    }
    if preliminary["paths"] != actual_paths:
        raise ValueError("forced-check execution paths differ from frozen manifest")
    signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, preliminary["resource_caps"]["wall_seconds"])
    try:
        execution, selection, _, receipt = _load_inputs(
            root, execution_path, selection_path, precondition_archive,
            backup_archive, m3_path, m3_run, approval_receipt)
        m3, cases, m3_sha = load_approved(m3_path, root)
        by_id = {case.identifier: case for case in cases}
        codes = source_hashes()
        output.mkdir(parents=True, exist_ok=False)
        rows = []
        status = "running"
        error: dict | None = None
        try:
            for path_row in selection["paths"]:
                task = Task(path_row["case_id"], "long", "major",
                            float(path_row["demand_multiplier"]), "literal", 1.0)
                case = by_id[task.case_id]
                payload = scientific_input(task, case, m3, m3_sha, codes)
                record = _read_cached(m3_run / "trajectories" / f"{_key(payload)}.json", payload)
                if record is None:
                    raise ValueError("cached forced-check cell missing")
                for q in path_row["qualification_dates"]:
                    problem = forced_check_engine.cached_opening_problem(case, record, q)
                    if problem.time_limit != execution["resource_caps"]["per_mip_seconds"]:
                        raise ValueError("cached solve time limit differs from approved cap")
                    forced = planner_forced_v1.plan(
                        problem, force_current_qualification=True, primary_only=True)
                    if not isinstance(forced, SolverCertificate):
                        raise ValueError("forced planner returned no primary certificate")
                    cached = record["rollout"]["certificates"][q][0]
                    classification = classify(cached, forced, 1e-6)
                    row = {
                        "case_id": task.case_id,
                        "demand_multiplier": task.demand_multiplier,
                        "qualification_date": q,
                        "cached_unforced_primary": cached,
                        "forced_primary": asdict(forced),
                        "classification": classification,
                    }
                    row_raw = canonical(row)
                    row_path = output / f"row-{len(rows):02d}.json"
                    row_path.write_bytes(row_raw)
                    rows.append(row)
                    if classification in ("solver_unresolved", "formulation_mismatch",
                                          "planner_model_inconsistency"):
                        raise ValueError(f"forced-check row unresolved: {classification}")
                    if (_rss_mib() > execution["resource_caps"]["process_rss_mib"]
                            or monotonic() - started >= execution["resource_caps"]["wall_seconds"]
                            or sum(path.stat().st_size for path in output.glob("*.json")) >
                            execution["resource_caps"]["disk_mib"] * 1024 * 1024):
                        raise RuntimeError("forced-check resource cap reached")
            if len(rows) != execution["planned_forced_primary_mip_solves"]:
                raise ValueError("forced-check solve count mismatch")
            status = "complete"
        except Exception as exc:
            status = "stopped"
            error = {"type": type(exc).__name__, "message": str(exc)}
            if isinstance(exc, PlannerFailure):
                error["solver_certificate"] = asdict(exc.certificate)
        result = {
            "schema": 1, "status": status, "execution_manifest_sha256": receipt["execution_sha256"],
            "approval_receipt_sha256": receipt["receipt_sha256"],
            "selection_manifest_sha256": _digest(selection_path),
            "precondition_result_sha256": execution["precondition_result_sha256"],
            "completed_forced_primary_mip_solves": len(rows),
            "classification_counts": dict(Counter(row["classification"] for row in rows)),
            "row_sha256": [_digest(output / f"row-{i:02d}.json") for i in range(len(rows))],
            "error": error,
            "resources": {"active_wall_seconds": monotonic() - started,
                          "peak_process_rss_mib": _rss_mib()},
        }
        raw = canonical(result)
        (output / "run.json").write_bytes(raw)
        return {"status": status, "result_sha256": sha256(raw).hexdigest(),
                "completed_forced_primary_mip_solves": len(rows), "error": error}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.forced_check_run")
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--execution-manifest", type=Path, required=True)
    parser.add_argument("--selection-manifest", type=Path, required=True)
    parser.add_argument("--precondition-archive", type=Path, required=True)
    parser.add_argument("--backup-archive", type=Path, required=True)
    parser.add_argument("--m3-manifest", type=Path, required=True)
    parser.add_argument("--m3-run", type=Path, required=True)
    parser.add_argument("--approval-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(
        args.repository_root.resolve(), args.execution_manifest.resolve(),
        args.selection_manifest.resolve(), args.precondition_archive.resolve(),
        args.backup_archive.resolve(), args.m3_manifest.resolve(),
        args.m3_run.resolve(), args.approval_receipt.resolve(),
        args.output.resolve()), sort_keys=True))


if __name__ == "__main__":
    main()
