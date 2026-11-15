"""Receipt-gated forensic replay of four R2 public paths; no strategic outputs.

The proposal and verification commands make no MIP calls. The run command
requires an owner receipt naming the exact proposal hash and an unused output
namespace. This is not a v3 planner, core, or scientific-run modification.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path
import resource
import sys
from time import monotonic
import traceback

from sn import planner_v3
from sn.allocation_v3 import canonical
from sn.core import Notice, Realization, TOL
from sn.core_v3 import initial_state_v3, step_v3
from sn.networks_v3 import build_public_case


MANIFEST_PATH = "configs/proposed-v3-r2-diagnostic.json"
RECEIPT_PATH = "configs/owner-approvals/v3-r2-diagnostic.json"
OUTPUT_PATH = "results/v3_r2_diagnostic/diagnostic.json"
MARKER_PATH = "results/v3_r2_diagnostic/attempt.started"
R2_MANIFEST_PATH = "configs/proposed-v3-tie-stability-r2.json"
R2_RESULT_PATH = "results/v3_tie_stability_r2/stability.json"
ALLOCATION_PATH = "configs/proposed-v3-allocation.json"
DESIGN_PATH = "configs/v3-design/manifest.json"
TASKS = (
    ("shared-r4-planted", "v3-c0-r4-6|short/minor", 9, 31,
     "presolve-on prefix; on and off from the identical period-9 state"),
    ("r1-planted", "v3-c2-r1-interior-planted-small-1|short/minor", 10, 33,
     "presolve-off natural path"),
    ("r3-planted", "v3-c0-r3-6|short/minor", 7, 24,
     "presolve-off natural path"),
    ("r4-distribution", "v3-c0-r4-3|short/minor", 13, 42,
     "presolve-off natural path"),
)
MAX_PHASES = 130
PER_MIP_SECONDS = 30
WALL_SECONDS = 300
RSS_MIB = 512
SAVED_BYTES = 4 * 1024 * 1024
EXPECTED_R2_MANIFEST_SHA256 = "97fff29b08b657c79aa4d0d7556fecbfe00498add3b422abea1785b732930dbd"
EXPECTED_R2_RESULT_SHA256 = "558edc1bebffd57dad7d3d5341b54c02264e63a3132f246939497b3e439c8629"


class ResourceCapError(RuntimeError):
    pass


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _rss_mib() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value / (1024 * 1024) if sys.platform == "darwin" else value / 1024


def _case_rows(root: Path) -> dict[str, dict]:
    allocation = json.loads((root / ALLOCATION_PATH).read_bytes())
    return {row["case_id"]: row for group in allocation["candidates"]
            for row in group["cases"]}


def proposal(root: Path) -> dict:
    root = root.resolve()
    if digest(root / R2_MANIFEST_PATH) != EXPECTED_R2_MANIFEST_SHA256:
        raise ValueError("R2 manifest hash differs from the consumed attempt")
    if digest(root / R2_RESULT_PATH) != EXPECTED_R2_RESULT_SHA256:
        raise ValueError("R2 result hash differs from the saved evidence")
    r2 = json.loads((root / R2_MANIFEST_PATH).read_bytes())
    if (digest(root / ALLOCATION_PATH) != r2["allocation_sha256"]
            or digest(root / DESIGN_PATH) != r2["public_design_manifest_sha256"]):
        raise ValueError("R2-bound public inputs changed")
    source = dict(r2["source_sha256"])
    for name, expected in source.items():
        if digest(root / "src/sn" / name) != expected:
            raise ValueError(f"R2-bound source changed: {name}")
    source["scripts/v3_r2_diagnostic.py"] = digest(Path(__file__).resolve())
    rows = _case_rows(root)
    design = json.loads((root / DESIGN_PATH).read_bytes())
    public_hashes = {row["case_id"]: row["content_sha256"]
                     for row in design["cases"]}
    result = json.loads((root / R2_RESULT_PATH).read_bytes())
    entries = []
    for name, path_id, target_period, maximum_phases, mode in TASKS:
        case_id, cell = path_id.split("|", 1)
        if cell != "short/minor":
            raise ValueError("diagnostic is limited to the frozen short/minor cell")
        case = build_public_case(rows[case_id])
        if sha256(canonical(asdict(case))).hexdigest() != public_hashes[case_id]:
            raise ValueError(f"public case hash differs: {case_id}")
        failing = [failure for group in result["path_failures"].values()
                   for failure in group if failure["path_id"] == path_id]
        if not failing:
            raise ValueError(f"R2 result has no selected failure: {path_id}")
        entries.append({
            "name": name, "path_id": path_id,
            "case_content_sha256": public_hashes[case_id],
            "target_period": target_period,
            "maximum_mip_phases": maximum_phases, "mode": mode,
            "lane_capacity_by_index": [lane.capacity for lane in case.network.lanes],
            "lane_lead_by_index": [lane.lead for lane in case.network.lanes],
        })
    return {
        "schema": 1,
        "status": "proposal_awaiting_owner_named_hash",
        "purpose": "forensic diagnostic only; closed G1 is not reopened",
        "r2_manifest_sha256": digest(root / R2_MANIFEST_PATH),
        "r2_result_sha256": digest(root / R2_RESULT_PATH),
        "allocation_sha256": digest(root / ALLOCATION_PATH),
        "public_design_sha256": digest(root / DESIGN_PATH),
        "source_sha256": source,
        "tasks": entries,
        "source_capacity_equivalence": {
            "planner_rule": "ship(s,t,lane) upper = lane.capacity when arrival is in horizon, otherwise 0",
            "core_rule": "aggregate shipment on lane must be <= lane.capacity + 1e-9",
            "interpretation": "same physical lane.capacity; the core's 1e-9 is numerical acceptance only",
            "planner_source": "src/sn/planner_v3.py",
            "core_source": "src/sn/core.py",
        },
        "execution": {
            "command": "PYTHONPATH=src python3 scripts/v3_r2_diagnostic.py run --repository-root \"$PWD\"",
            "receipt": RECEIPT_PATH, "output": OUTPUT_PATH,
            "attempt_marker": MARKER_PATH,
            "maximum_mip_phases": MAX_PHASES,
            "per_mip_seconds": PER_MIP_SECONDS,
            "wall_seconds": WALL_SECONDS,
            "peak_rss_mib": RSS_MIB,
            "saved_bytes": SAVED_BYTES,
            "retries": 0,
            "threads": 1,
            "full_error_text": True,
            "save_payoffs": False,
            "save_strategic_outcomes": False,
            "confirmation_access": False,
            "calibration": False,
            "m5_paused": True,
        },
    }


def verify(root: Path) -> tuple[dict, str]:
    root = root.resolve()
    path = root / MANIFEST_PATH
    raw = path.read_bytes()
    value = json.loads(raw)
    if canonical(value) != raw or value != proposal(root):
        raise ValueError("diagnostic manifest differs from bound proposal")
    return value, sha256(raw).hexdigest()


def _problem(case, state, t: int, presolve: bool):
    from sn.planner_v3 import PlanningProblem, Scenario

    return PlanningProblem(
        network=case.network, contract=case.contract,
        notice=Notice(case.short_capacity, "minor"), state=state,
        scenarios=(Scenario("short", 1.0, case.short_capacity),),
        observed_capacity=case.short_capacity[t],
        observed_demand=case.baseline_demand[t],
        demand_forecast=case.baseline_demand,
        time_limit=PER_MIP_SECONDS, presolve=presolve, threads=1,
    )


def _lane_detail(case, action) -> list[dict]:
    amounts: dict[int, float] = {}
    for item in action.shipments:
        amounts[item.lane_index] = amounts.get(item.lane_index, 0.0) + item.quantity
    return [{"lane_index": index, "capacity": lane.capacity,
             "raw_shipment": amounts.get(index, 0.0),
             "excess": amounts.get(index, 0.0) - lane.capacity,
             "core_tolerance": TOL}
            for index, lane in enumerate(case.network.lanes)
            if amounts.get(index, 0.0) > lane.capacity + TOL]


def _error(error: BaseException, stage: str, case, action=None) -> dict:
    row = {"stage": stage, "type": type(error).__name__,
           "full_error_text": str(error), "traceback": traceback.format_exc()}
    if action is not None:
        row["lane_violations"] = _lane_detail(case, action)
    if isinstance(error, planner_v3.PlannerFailure):
        certificate = error.certificate
        row["solver"] = {"phase": certificate.phase,
                         "status": certificate.status,
                         "full_message": certificate.message,
                         "relative_gap": certificate.relative_gap}
    return row


def run(root: Path) -> dict:
    root = root.resolve()
    manifest, manifest_sha = verify(root)
    receipt_path = root / RECEIPT_PATH
    expected_receipt = {"schema": 1, "decision": "owner_approved_v3_r2_diagnostic",
                        "execution_manifest_sha256": manifest_sha}
    if (not receipt_path.is_file()
            or receipt_path.read_bytes() != canonical(expected_receipt)):
        raise PermissionError("owner has not named this exact diagnostic manifest hash")
    output, marker = root / OUTPUT_PATH, root / MARKER_PATH
    if output.exists() or marker.exists() or output.parent.exists():
        raise FileExistsError("diagnostic output namespace already exists; zero retries")
    output.parent.mkdir(parents=True)
    marker.write_bytes(canonical({"execution_manifest_sha256": manifest_sha}))
    started = monotonic()
    original_solve = planner_v3._solve
    original_validate = planner_v3._validate_current_action
    phases = []
    attempted_phases = 0
    current = {"task": None, "period": None, "action": None, "stage": None,
               "task_phases": 0, "task_cap": 0}

    def checked_solve(model, objective, time_limit, phase, **kwargs):
        nonlocal attempted_phases
        if monotonic() - started >= WALL_SECONDS or _rss_mib() > RSS_MIB:
            raise ResourceCapError("diagnostic wall or memory cap reached before solve")
        if (attempted_phases >= MAX_PHASES
                or current["task_phases"] >= current["task_cap"]):
            raise ResourceCapError("diagnostic MIP phase cap reached")
        attempted_phases += 1
        current["task_phases"] += 1
        begun = monotonic()
        try:
            vector, certificate = original_solve(model, objective, time_limit, phase,
                                                  **kwargs)
        except planner_v3.PlannerFailure as error:
            certificate, vector = error.certificate, None
        except Exception as error:
            phases.append({"task": current["task"], "period": current["period"],
                           "phase": phase, "presolve": kwargs.get("presolve"),
                           "status": None, "full_message": str(error),
                           "elapsed_seconds": monotonic() - begun,
                           "exception_type": type(error).__name__})
            raise
        elapsed = monotonic() - begun
        phases.append({"task": current["task"], "period": current["period"],
                       "phase": phase, "presolve": kwargs.get("presolve"),
                       "status": certificate.status, "full_message": certificate.message,
                       "elapsed_seconds": elapsed,
                       "relative_gap": certificate.relative_gap})
        if (elapsed > PER_MIP_SECONDS + 0.01 or monotonic() - started > WALL_SECONDS
                or _rss_mib() > RSS_MIB):
            raise ResourceCapError("diagnostic resource cap reached")
        if vector is None:
            raise planner_v3.PlannerFailure(certificate)
        return vector, certificate

    def capture_validation(problem, action):
        current["action"] = action
        current["stage"] = "planner validation"
        return original_validate(problem, action)

    planner_v3._solve = checked_solve
    planner_v3._validate_current_action = capture_validation
    records = []
    global_stop = False
    try:
        for task in manifest["tasks"]:
            case_id = task["path_id"].split("|", 1)[0]
            case = build_public_case(_case_rows(root)[case_id])
            current.update(task=task["name"], period=None, action=None,
                           stage=None, task_phases=0,
                           task_cap=task["maximum_mip_phases"])
            state = initial_state_v3(case.network)
            outcome = {"name": task["name"], "path_id": task["path_id"],
                       "target_period": task["target_period"], "attempts": []}
            for t in range(task["target_period"] + 1):
                current.update(period=t, action=None, stage="planner")
                if task["name"] == "shared-r4-planted" and t == 9:
                    state_hash = sha256(canonical(asdict(state))).hexdigest()
                    outcome["period_9_state_sha256"] = state_hash
                    for setting in (True, False):
                        current.update(action=None, stage="planner")
                        try:
                            choice = planner_v3.plan(_problem(case, state, t, setting))
                            current["action"] = choice.action
                            current["stage"] = "core transition"
                            step_v3(case.network, case.contract,
                                    Notice(case.short_capacity, "minor"),
                                    state, choice.action,
                                    Realization(case.short_capacity[t],
                                                case.baseline_demand[t]),
                                    revealed_schedule=(case.short_capacity if
                                        t == case.contract.revelation_period else None))
                            event = {"status": "no_error"}
                        except ResourceCapError:
                            raise
                        except Exception as error:
                            event = {"status": "error", "error": _error(
                                error, current["stage"], case, current["action"])}
                        outcome["attempts"].append({"presolve": setting, **event})
                    break
                try:
                    choice = planner_v3.plan(_problem(case, state, t, False
                                                     if task["name"] != "shared-r4-planted"
                                                     else True))
                    current["action"] = choice.action
                    current["stage"] = "core transition"
                    state, _ = step_v3(
                        case.network, case.contract,
                        Notice(case.short_capacity, "minor"), state, choice.action,
                        Realization(case.short_capacity[t], case.baseline_demand[t]),
                        revealed_schedule=(case.short_capacity if
                            t == case.contract.revelation_period else None),
                    )
                except ResourceCapError:
                    raise
                except Exception as error:
                    outcome["attempts"].append({"presolve": task["name"] == "shared-r4-planted",
                        "status": "error", "period": t,
                        "error": _error(error, current["stage"], case, current["action"])})
                    break
                if t == task["target_period"]:
                    outcome["attempts"].append({"presolve": False,
                                                 "status": "no_error", "period": t})
            outcome["mip_phases"] = current["task_phases"]
            records.append(outcome)
    except Exception as error:
        global_stop = True
        records.append({"status": "global_stop", "error": _error(error,
                        current["stage"] or "global", case if 'case' in locals() else None)})
    finally:
        planner_v3._solve = original_solve
        planner_v3._validate_current_action = original_validate
    result = {"schema": 1, "execution_manifest_sha256": manifest_sha,
              "status": "stopped" if global_stop or len(records) != len(TASKS)
                        else "completed",
              "mip_phases": attempted_phases, "tasks": records,
              "phase_records": phases, "elapsed_seconds": monotonic() - started,
              "peak_rss_mib": _rss_mib(), "payoffs_saved": False,
              "strategic_outcomes_saved": False}
    raw = canonical(result)
    if len(raw) > SAVED_BYTES:
        raise RuntimeError("diagnostic saved-output cap reached")
    output.write_bytes(raw)
    return {"status": result["status"], "output_sha256": sha256(raw).hexdigest(),
            "mip_phases": attempted_phases}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("propose", "verify", "run"))
    parser.add_argument("--repository-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    if args.command == "propose":
        path = root / MANIFEST_PATH
        if path.exists():
            raise FileExistsError(path)
        path.write_bytes(canonical(proposal(root)))
        print(json.dumps({"manifest_sha256": digest(path), "mip_solves": 0}))
    elif args.command == "verify":
        _, value = verify(root)
        print(json.dumps({"manifest_sha256": value, "mip_solves": 0}))
    else:
        print(json.dumps(run(root)))


if __name__ == "__main__":
    main()
