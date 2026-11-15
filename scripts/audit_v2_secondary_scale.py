"""Inspect the declared tie objective in public profile models without solving.

This constructs each period-zero model in the proposed workload and aborts at
the first solver boundary. It does not produce decisions or realized scores.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from sn import planner_v2, profile_v2
from sn.core import initial_state
from sn.networks_v2 import build_public_design, load_allocation
from sn.suite import canonical


class _ModelCaptured(Exception):
    pass


def audit(root: Path) -> dict:
    allocation, design = load_allocation(
        root / "configs/proposed-v2-factor-allocation.json",
        root / "configs/design/manifest.json",
    )
    cases = {item.slot_id: item for item in build_public_design(allocation, design)
             if item.candidate_index == 0}
    design_manifest = json.loads((root / "configs/v2-design/manifest.json").read_bytes())
    tasks = profile_v2.expected_tasks(design_manifest["cases"])
    captured: list[dict] = []

    def intercept(model, _objective, _time_limit, phase, **_kwargs):
        if phase != "primary":
            raise AssertionError("audit reached a later solver phase")
        weights = planner_v2._secondary_objective(model, 0)
        positive = [float(value) for value in weights if value > 0]
        current = [float(value) for index, value in enumerate(weights)
                   if value > 0 and model.names[index][1:3] == (0, 0)]
        other = [float(value) for index, value in enumerate(weights)
                 if value > 0 and model.names[index][1:3] != (0, 0)]
        if not positive or not current or not other:
            raise AssertionError("missing expected tie-objective coefficients")
        captured.append({
            "task_index": len(captured), "model_variables": len(model.names),
            "positive_coefficients": len(positive),
            "min_coefficient": min(positive), "max_coefficient": max(positive),
            "min_current_coefficient": min(current),
            "max_current_coefficient": max(current),
            "min_other_coefficient": min(other),
            "max_other_coefficient": max(other),
        })
        raise _ModelCaptured

    original = planner_v2._solve
    planner_v2._solve = intercept
    try:
        for task in tasks:
            item = cases[task["slot_id"]]
            problem = profile_v2._problem(
                item, task["configuration"], initial_state(item.case.network), 0, 4,
            )
            try:
                planner_v2.plan(problem)
            except _ModelCaptured:
                pass
            else:
                raise AssertionError("audit did not intercept solver boundary")
    finally:
        planner_v2._solve = original
    if len(captured) != 58:
        raise AssertionError("unexpected profile-task count")
    fields = ("min_coefficient", "max_coefficient", "min_current_coefficient",
              "max_current_coefficient", "min_other_coefficient", "max_other_coefficient",
              "positive_coefficients", "model_variables")
    return {
        "schema": 1, "scope": "period-zero models for all 58 proposed profile tasks",
        "new_mip_solves": 0, "realized_secondary_scores_available": False,
        "task_count": len(captured),
        "ranges": {field: [min(row[field] for row in captured),
                           max(row[field] for row in captured)] for field in fields},
        "rows": captured,
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    output = root / "docs/results/v2_secondary_scale_r4.json"
    if output.exists():
        raise FileExistsError(output)
    raw = canonical(audit(root))
    output.write_bytes(raw)
    print(json.dumps({"path": str(output), "sha256": sha256(raw).hexdigest(),
                      "new_mip_solves": 0}, sort_keys=True))


if __name__ == "__main__":
    main()
