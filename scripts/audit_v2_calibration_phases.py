"""Solve-free upper bound for all proposed v2 truthful calibration paths."""

from __future__ import annotations

from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path

from sn import planner_v2
from sn.core import Notice, initial_state
from sn.networks_v2 import build_public_design, load_allocation
from sn.planner_v2 import PlanningProblem, Scenario
from sn.suite import canonical


class _Captured(Exception):
    pass


def audit(root: Path) -> dict:
    allocation, design = load_allocation(
        root / "configs/proposed-v2-factor-allocation.json",
        root / "configs/design/manifest.json",
    )
    cases = build_public_design(allocation, design)
    rows: list[dict] = []

    def capture(model, _objective, _time_limit, phase, **_kwargs):
        if phase != "primary":
            raise AssertionError("workload audit reached a solver phase after primary")
        current = len(planner_v2._current_action_indices(model, period))
        qualification = next(index for index, name in enumerate(model.names)
                             if name == ("qualify", 0, period, None))
        branch = int(model.upper[qualification] >= 1)
        period_rows.append({
            "period": period,
            "primary": 1,
            "qualification_branch_upper": branch,
            "tie_break": 1,
            "current_tie_upper": current,
            "total_upper": 2 + branch + current,
        })
        raise _Captured

    original = planner_v2._solve
    planner_v2._solve = capture
    try:
        for item in cases:
            for actual_type, schedule, notice_name in (
                ("long", item.long_capacity, "major"),
                ("short", item.short_capacity, "minor"),
            ):
                period_rows: list[dict] = []
                for period in range(item.case.network.horizon):
                    # No old accepted orders and no earlier qualification
                    # maximize the number of current actions and branches.
                    state = replace(initial_state(item.case.network), period=period)
                    problem = PlanningProblem(
                        item.case.network, item.contract,
                        Notice(schedule, f"truthful {notice_name} notice"),
                        state, (Scenario(actual_type, 1.0, schedule),),
                        schedule[period], item.baseline_demand[period],
                        item.baseline_demand, 4,
                    )
                    try:
                        planner_v2.plan(problem)
                    except _Captured:
                        pass
                    else:
                        raise AssertionError("audit did not intercept primary boundary")
                rows.append({
                    "candidate_index": item.candidate_index,
                    "slot_id": item.slot_id,
                    "case_id": item.case.identifier,
                    "case_sha256": sha256(canonical(asdict(item))).hexdigest(),
                    "actual_type": actual_type,
                    "notice_schedule": notice_name,
                    "periods": period_rows,
                    "maximum_mip_phases": sum(row["total_upper"] for row in period_rows),
                })
    finally:
        planner_v2._solve = original
    if len(rows) != 144:
        raise AssertionError("expected exactly 144 truthful paths")
    phase_counts = {
        key: sum(period[key] for path in rows for period in path["periods"])
        for key in ("primary", "qualification_branch_upper", "tie_break", "current_tie_upper")
    }
    return {
        "schema": 1,
        "scope": "all 144 truthful candidate paths, ten periods each, no MIP solve",
        "new_mip_solves": 0,
        "path_count": len(rows),
        "period_count": sum(len(row["periods"]) for row in rows),
        "phase_upper_counts": phase_counts,
        "maximum_mip_phases": sum(phase_counts.values()),
        "candidate_phase_upper_counts": {
            str(index): sum(row["maximum_mip_phases"] for row in rows
                            if row["candidate_index"] == index)
            for index in range(3)
        },
        "rows": rows,
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    output = root / "docs/results/v2_calibration_phase_audit.json"
    if output.exists():
        raise FileExistsError(output)
    raw = canonical(audit(root))
    output.write_bytes(raw)
    print(json.dumps({"path": str(output), "sha256": sha256(raw).hexdigest(),
                      "new_mip_solves": 0}, sort_keys=True))


if __name__ == "__main__":
    main()
