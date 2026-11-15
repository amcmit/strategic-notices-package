"""Solve-free audit of the reconditioned v2 tie objective and profile phase cap."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path

from sn import planner_v2, profile_v2
from sn.core import initial_state
from sn.networks_v2 import build_public_design, load_allocation
from sn.suite import canonical


class _Captured(Exception):
    pass


def audit(root: Path) -> dict:
    allocation, design = load_allocation(
        root / "configs/proposed-v2-factor-allocation.json",
        root / "configs/design/manifest.json",
    )
    cases = {item.slot_id: item for item in build_public_design(allocation, design)
             if item.candidate_index == 0}
    public = json.loads((root / "configs/v2-design/manifest.json").read_bytes())
    tasks = profile_v2.expected_tasks(public["cases"])
    rows: list[dict] = []

    def capture(model, _objective, _time_limit, phase, **_kwargs):
        if phase != "primary":
            raise AssertionError("audit reached a later solver phase")
        weights = planner_v2._secondary_objective(model, period)
        positive = sorted(float(value) for value in weights if value > 0)
        if not positive:
            raise AssertionError("missing current-action tie objective")
        current_count = len(planner_v2._current_action_indices(model, period))
        if any(weights[index] != 0 for index, name in enumerate(model.names)
               if name[2] != period or name[1] != 0 or name[0] == "qualify"):
            raise AssertionError("tie objective depends on future or pinned action")
        rows.append({
            "task_index": task_index, "slot_id": task["slot_id"],
            "kind": task["kind"], "period": period,
            "horizon": cases[task["slot_id"]].case.network.horizon,
            "minimum_coefficient": min(positive),
            "maximum_coefficient": max(positive),
            "minimum_spacing": min(b - a for a, b in zip(positive, positive[1:])),
            "current_action_coordinates": current_count,
            "maximum_phases_per_plan": 3 + current_count,
        })
        raise _Captured

    original = planner_v2._solve
    planner_v2._solve = capture
    try:
        for task_index, task in enumerate(tasks):
            item = cases[task["slot_id"]]
            periods = range(1 if task["kind"] == "initial" else item.case.network.horizon)
            for period in periods:
                # An empty outstanding-order state maximizes current order
                # availability. Other current coordinate bounds are fixed
                # by the network, contract and horizon.
                state = replace(initial_state(item.case.network), period=period)
                problem = profile_v2._problem(
                    item, task["configuration"], state, period, 4,
                )
                try:
                    planner_v2.plan(problem)
                except _Captured:
                    pass
                else:
                    raise AssertionError("audit did not intercept solver boundary")
    finally:
        planner_v2._solve = original
    if len(rows) != 148:
        raise AssertionError("unexpected profile task-period count")
    initial = sum(row["maximum_phases_per_plan"] for row in rows
                  if row["kind"] == "initial")
    rolling = sum(row["maximum_phases_per_plan"] for row in rows
                  if row["kind"] == "rolling")
    initial_counts = {row["task_index"]: row["current_action_coordinates"]
                      for row in rows if row["kind"] == "rolling" and row["period"] == 0}
    later_growth = sum(row["current_action_coordinates"] > initial_counts[row["task_index"]]
                       for row in rows if row["kind"] == "rolling" and row["period"] > 0)
    if later_growth:
        raise AssertionError("later period added a current-action coordinate")
    conservative_rolling = sum(row["horizon"] * row["maximum_phases_per_plan"]
                               for row in rows if row["kind"] == "rolling"
                               and row["period"] == 0)
    if rolling > conservative_rolling:
        raise AssertionError("rolling period-zero phase ceiling was too low")
    return {
        "schema": 1, "scope": "all 58 proposed public profile tasks and all 148 task-period models",
        "new_mip_solves": 0, "task_count": len(tasks), "task_period_count": len(rows),
        "minimum_coefficient": min(row["minimum_coefficient"] for row in rows),
        "maximum_coefficient": max(row["maximum_coefficient"] for row in rows),
        "minimum_spacing": min(row["minimum_spacing"] for row in rows),
        "largest_to_smallest_ratio": max(
            row["maximum_coefficient"] / row["minimum_coefficient"] for row in rows),
        "maximum_initial_phases": initial,
        "audited_rolling_phases_with_empty_outstanding": rolling,
        "later_coordinate_growth_count": later_growth,
        "maximum_rolling_phases": conservative_rolling,
        "maximum_total_phases": initial + conservative_rolling,
        "rows": rows,
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    output = root / "docs/results/v2_secondary_scale_r5.json"
    if output.exists():
        raise FileExistsError(output)
    raw = canonical(audit(root))
    output.write_bytes(raw)
    print(json.dumps({"path": str(output), "sha256": sha256(raw).hexdigest(),
                      "new_mip_solves": 0}, sort_keys=True))


if __name__ == "__main__":
    main()
