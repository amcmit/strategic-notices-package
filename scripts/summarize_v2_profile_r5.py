"""Derive the resource-only R5 review table from the frozen raw profile."""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

from sn.suite import canonical


def summarize(root: Path) -> dict:
    path = root / "results/v2_resource_profile_r5/profile.json"
    raw = path.read_bytes()
    profile = json.loads(raw)
    if profile["manifest_sha256"] != "e184137fb7fdcf1bae36d49c30cc028a5bff84b8f7402c4233336be33407f79c":
        raise ValueError("unexpected R5 manifest digest")
    rows = profile["rows"]
    phases = {}
    for phase in sorted({row["phase"] for row in rows}):
        group = [row for row in rows if row["phase"] == phase]
        elapsed = [row["elapsed_seconds"] for row in group]
        sizes = [row["certificate_bytes"] for row in group]
        gaps = [row["primal_dual_gap"] for row in group]
        relative = [row["relative_gap"] for row in group]
        phases[phase] = {
            "count": len(group),
            "status_counts": dict(sorted(Counter(str(row["status"]) for row in group).items())),
            "certified_count": sum(row["optimal"] for row in group),
            "elapsed_sum_seconds": sum(elapsed),
            "elapsed_max_seconds": max(elapsed),
            "certificate_bytes_min": min(sizes),
            "certificate_bytes_max": max(sizes),
            "certificate_bytes_sum": sum(sizes),
            "absolute_gap_max": max(gaps),
            "relative_gap_max": max(relative),
            "absolute_gap_positive_count": sum(value > 0 for value in gaps),
            "relative_gap_positive_count": sum(value > 0 for value in relative),
        }
    positives = [row for row in profile["projection_rows"]
                 if row["correction_l1"] > 0]
    return {
        "schema": 1,
        "raw_sha256": sha256(raw).hexdigest(),
        "manifest_sha256": profile["manifest_sha256"],
        "receipt_sha256": profile["approval_receipt_sha256"],
        "status": profile["status"],
        "error": profile["error"],
        "tasks": profile["task_count"],
        "phase_count": profile["phase_count"],
        "elapsed_seconds": profile["elapsed_seconds"],
        "peak_rss_mib": profile["peak_rss_mib"],
        "raw_bytes": len(raw),
        "phases": phases,
        "dated_correction_rows": len(profile["projection_rows"]),
        "positive_correction_count": len(positives),
        "positive_corrections": positives,
        "maximum_correction_l1": profile["maximum_shipment_correction_l1"],
        "saved_actions_or_costs": profile["saved_actions_or_costs"],
        "confirmation_case_access": profile["confirmation_case_access"],
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "docs/results/v2_profile_r5/summary.json"
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(summarize(root)))
    print(path)


if __name__ == "__main__":
    main()
