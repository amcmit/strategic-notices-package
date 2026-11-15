"""Read-only preflight for the proposed forced-qualification planner check.

This module has no scientific execution command and never calls a MIP solver.
"""

from __future__ import annotations

import argparse
from collections import Counter
import gzip
from hashlib import sha256
import json
from pathlib import Path

from .m3 import Task, _key, _read_cached, load_approved, scientific_input, source_hashes
from .m3 import _verify_physical
from .suite import canonical


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def inspect(root: Path, proposal_path: Path, backup_archive: Path,
            m3_path: Path, m3_run: Path) -> dict:
    """Bind every proposed solve to one exact, existing, physically valid cell."""
    proposal_raw = proposal_path.read_bytes()
    proposal = json.loads(proposal_raw)
    if canonical(proposal) != proposal_raw:
        raise ValueError("proposed check manifest must use canonical JSON")
    expected_comparison = {
        "state": "replay_exact_cached_prefix_to_each_qualification_date",
        "forecast": "cached_conditional_mean_future_demand_and_current_observed_demand",
        "force": "qualify_at_current_date_equals_1",
        "objective": "same_primary_expected_operating_cost",
        "strict_exclusion": "forced_dual_gt_cached_unrestricted_primal_plus_1e-6",
        "contradiction": "forced_primal_lt_cached_unrestricted_dual_minus_1e-6",
        "overlap": "tie_or_unresolved",
        "new_primary_mip_solves": 26, "new_tie_break_solves": 0,
        "new_physical_rollouts": 0,
    }
    if (proposal["schema"] != 1 or proposal["stage"] != "post-backup-forced-qualification-planner-check"
            or proposal["status"] != "proposal_only_requires_owner_approval"
            or not proposal["m5_paused"] or proposal["path_count"] != 8
            or proposal["uncertified_path_date_count"] != 26
            or proposal["comparison"] != expected_comparison
            or proposal["resource_caps"] != {
                "wall_seconds": 120, "per_mip_seconds": 2,
                "process_rss_mib": 256, "disk_mib": 10, "auto_retries": 0}
            or any(proposal[key] for key in (
                "confirmation_case_access", "local_model_calls", "paid_api_calls",
                "generator_changes", "parameter_changes", "equilibrium_selection_work"))):
        raise ValueError("proposed forced-check scope or caps changed")
    m3, cases, m3_digest = load_approved(m3_path, root)
    by_id = {case.identifier: case for case in cases}
    codes = source_hashes()
    run_state = json.loads((m3_run / "run.json").read_bytes())
    raw = gzip.decompress(backup_archive.read_bytes()) if backup_archive.suffix == ".gz" else backup_archive.read_bytes()
    result = json.loads(raw)
    if (canonical(result) != raw
            or proposal["backup_result_sha256"] != sha256(raw).hexdigest()
            or proposal["design_manifest_sha256"] != m3["design_manifest_sha256"]
            or proposal["m3_manifest_sha256"] != m3_digest
            or proposal["m3_source_sha256"] != codes
            or proposal["m3_trajectory_tree_sha256"] != run_state["trajectory_tree_sha256"]
            or run_state["manifest_sha256"] != m3_digest
            or run_state["source_sha256"] != codes or run_state["status"] != "complete"
            or result["m3_trajectory_tree_sha256"] != run_state["trajectory_tree_sha256"]
            or proposal["cached_cell"] != {
                "actual_type": "long", "structured_notice": "major",
                "severe_belief": 1.0, "benchmark": "literal"}):
        raise ValueError("proposed forced-check provenance mismatch")
    expected: dict[tuple[str, float], list[int]] = {}
    for row in result["path_date_rows"]:
        if not row["certified_date_exclusion_reasons"]:
            expected.setdefault((row["case_id"], float(row["demand_multiplier"])), []).append(
                row["qualification_date"])
    if (len(expected) != 8 or sum(len(dates) for dates in expected.values()) != 26
            or len(proposal["paths"]) != 8):
        raise ValueError("proposed forced-check paths differ from archived unresolved set")
    seen: set[tuple[str, float]] = set()
    for row in proposal["paths"]:
        key = (row["case_id"], float(row["demand_multiplier"]))
        if key in seen or key not in expected or row["qualification_dates"] != sorted(expected[key]):
            raise ValueError("proposed forced-check path or dates mismatch")
        seen.add(key)
        task = Task(key[0], "long", "major", key[1], "literal", 1.0)
        payload = scientific_input(task, by_id[key[0]], m3, m3_digest, codes)
        trajectory_key = _key(payload)
        path = m3_run / "trajectories" / f"{trajectory_key}.json"
        if row["cached_trajectory_key"] != trajectory_key or row["cached_trajectory_sha256"] != _digest(path):
            raise ValueError("proposed forced-check exact trajectory key or hash mismatch")
        record = _read_cached(path, payload)
        if record is None:
            raise ValueError("proposed forced-check cached trajectory absent")
        _verify_physical(record, by_id[key[0]])
        for q in row["qualification_dates"]:
            certificate = record["rollout"]["certificates"][q][0]
            if (certificate["phase"] != "primary" or not certificate["optimal"]
                    or certificate["primal"] is None or certificate["dual"] is None):
                raise ValueError("proposed forced-check cached certificate invalid")
    if seen != set(expected):
        raise ValueError("proposed forced-check path set incomplete")
    return {
        "status": "proposal_only_no_solves", "manifest_sha256": sha256(proposal_raw).hexdigest(),
        "path_count": len(seen), "proposed_primary_mip_solves": sum(map(len, expected.values())),
        "qualification_date_counts": dict(sorted(Counter(q for dates in expected.values()
                                                         for q in dates).items())),
        "verified_exact_cached_trajectories": len(seen),
        "new_mip_solves": 0, "new_physical_trajectories": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.forced_check_plan")
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--backup-archive", type=Path, required=True)
    parser.add_argument("--m3-manifest", type=Path, required=True)
    parser.add_argument("--m3-run", type=Path, required=True)
    args = parser.parse_args()
    result = inspect(args.repository_root.resolve(), args.manifest.resolve(),
                     args.backup_archive.resolve(), args.m3_manifest.resolve(),
                     args.m3_run.resolve())
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
