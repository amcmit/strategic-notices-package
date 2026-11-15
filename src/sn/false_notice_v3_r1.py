"""Approval-gated design-only long/minor paths; preparation never solves."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import resource
import signal
import sys
import traceback
from time import monotonic

from . import planner_v3_single_r3 as planner
from .allocation_v3 import canonical
from .core import Action, Notice, Production, Realization, Shipment
from .core_v3 import initial_state_v3, step_v3
from .freeze_v3_design import verify_freeze
from .networks_v3 import _approved_hash, build_public_case, load_allocation
from .planner_v3_single_r3 import PlannerFailure
from .profile_v3 import _receipt
from .confirmation_v3_r2 import verify_metadata


MANIFEST_REL = "configs/proposed-v3-design-false-notice-r1.json"
RESULT_ROOT = "results/v3_design_false_notice_r1"
DECISION = "owner_approved_v3_design_false_notice_r1"
SOURCE_FILES = (
    "src/sn/allocation_v3.py", "src/sn/core.py", "src/sn/core_v2.py",
    "src/sn/core_v3.py", "src/sn/handoff_v3_single.py",
    "src/sn/networks_v3.py", "src/sn/planner_v3_single_r3.py",
    "src/sn/freeze_v3_design.py", "src/sn/confirmation_v3.py",
    "src/sn/confirmation_structure_v3.py", "src/sn/confirmation_v3_r2.py",
    "src/sn/profile_v3.py", "src/sn/false_notice_v3_r1.py",
    "src/sn/verify_false_notice_v3_r1.py",
)
PROTOCOL_FILES = (
    "docs/PROPOSED_V3_PROTOCOL_RECONCILED.md",
    "docs/V3_SINGLE_SOLVE_R3_PROTOCOL.md",
    "docs/V3_PROTOCOL_R4_REPORTING_AMENDMENT.md",
    "docs/V3_PROTOCOL_R5_CONFIRMATION_POPULATION.md",
    "docs/V3_PRECONFIRMATION_PRECISION_FORECAST_SPEC.md",
)


class ResourceStop(RuntimeError):
    pass


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _rss_mib() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value / (1024 * 1024) if sys.platform == "darwin" else value / 1024


def _read_canonical(path: Path) -> dict:
    raw = path.read_bytes()
    value = json.loads(raw)
    if canonical(value) != raw:
        raise ValueError(f"noncanonical bound input: {path}")
    return value


def _frozen_inputs(root: Path) -> tuple[dict, dict, dict]:
    freeze_path = root / "configs/v3-design/selection-freeze.json"
    freeze_hash = verify_freeze(root)
    freeze = _read_canonical(freeze_path)
    if freeze_hash != _digest(freeze_path) or freeze["selected_candidate"] != 0:
        raise ValueError("selected-design freeze differs")
    public_path = root / "configs/v3-confirmation-public.json"
    metadata_hash = verify_metadata(root, public_path)  # public metadata only
    if metadata_hash != "c6c9056671382246ea5ad56855731f9f5f054f628ba01faf198ce14758721bab":
        raise ValueError("owner-reported public metadata hash differs")
    metadata = _read_canonical(public_path)
    truth_result = _read_canonical(root / "results/v3_truthful_calibration_r4/result.json")
    truth_verification = _read_canonical(root / "results/v3_truthful_calibration_r4/verification.json")
    if (truth_result["status"] != "completed" or truth_result["failures"]
            or truth_result["selected_candidate"] != 0
            or truth_verification["status"] != "verified"
            or truth_verification["result_sha256"]
            != _digest(root / "results/v3_truthful_calibration_r4/result.json")
            or truth_result["progress_sha256"]
            != _digest(root / "results/v3_truthful_calibration_r4/progress.jsonl")
            or metadata["selected_design_freeze_sha256"] != freeze_hash):
        raise ValueError("truthful or public-metadata evidence differs")
    allocation = load_allocation(
        root / "configs/proposed-v3-allocation.json",
        root / "configs/design/manifest.json",
        _approved_hash(root / "configs/owner-approvals/v3-allocation.json"),
    )
    rows = {row["case_id"]: row for row in allocation["candidates"][0]["cases"]}
    if (len(rows) != 56 or len(freeze["cases"]) != 56
            or {item["case_id"] for item in freeze["cases"]} != set(rows)):
        raise ValueError("frozen selected allocation is not 56 cases")
    return freeze, rows, truth_result


def expected_manifest(root: Path) -> dict:
    root = root.resolve()
    freeze, _, truth = _frozen_inputs(root)
    tasks = [{"path_id": item["case_id"] + "|long/minor",
              "truth_path_id": item["case_id"] + "|long/major",
              **item} for item in freeze["cases"]]
    if len({item["path_id"] for item in tasks}) != 56:
        raise ValueError("duplicate false-notice path")
    return {
        "schema": 1, "kind": "v3_design_false_notice_execution_r1",
        "status": "proposal_unrun_pending_exact_owner_hash",
        "design_freeze_sha256": _digest(root / "configs/v3-design/selection-freeze.json"),
        "public_design_manifest_sha256": _digest(root / "configs/v3-design/manifest.json"),
        "allocation_sha256": _digest(root / "configs/proposed-v3-allocation.json"),
        "truthful_calibration_result_sha256": _digest(root / "results/v3_truthful_calibration_r4/result.json"),
        "truthful_calibration_verification_sha256": _digest(root / "results/v3_truthful_calibration_r4/verification.json"),
        "truthful_calibration_progress_sha256": truth["progress_sha256"],
        "confirmation_public_metadata_sha256": _digest(root / "configs/v3-confirmation-public.json"),
        "source_sha256": {name: _digest(root / name) for name in SOURCE_FILES},
        "protocol_sha256": {name: _digest(root / name) for name in PROTOCOL_FILES},
        "test_sha256": _digest(root / "tests/test_v3_design_false_notice.py"),
        "tasks": tasks,
        "physical_cell": "long/minor", "truth_reference_cell": "long/major",
        "receiver": {"benchmark": "literal", "pre_revelation_severe_probability": 0.0,
                     "post_revelation": "actual_long_schedule"},
        "operating_configuration": {"demand_multiplier": 1.0, "presolve": False,
                                    "threads": 1, "rolling_planner": "planner_v3_single_r3"},
        "resource_caps": {"paths": 56, "periods_per_path": 20,
                          "maximum_mip_phases": 1120, "per_mip_seconds": 30,
                          "wall_seconds": 300, "peak_rss_mib": 512,
                          "saved_bytes": 67108864, "retries": 0},
        "receipt_decision": DECISION,
        "paths": {"approval_receipt": "configs/owner-approvals/v3-design-false-notice-r1.json",
                  "output_root": RESULT_ROOT,
                  "attempt_marker": RESULT_ROOT + "/attempt.started",
                  "first_solve_marker": RESULT_ROOT + "/first_solve.started",
                  "progress": RESULT_ROOT + "/progress.jsonl",
                  "result": RESULT_ROOT + "/result.json",
                  "verification": RESULT_ROOT + "/verification.json"},
        "scope": {"selected_rotation_only": True, "false_notice_paths": 56,
                  "new_truthful_paths": 0, "confirmation_private_access": False,
                  "sealing": False, "m5_paused": True, "retries": 0},
    }


def verify_manifest(root: Path, path: Path) -> tuple[dict, str]:
    root, path = root.resolve(), path.resolve()
    if path != root / MANIFEST_REL:
        raise ValueError("unexpected false-notice manifest path")
    raw = path.read_bytes()
    value = json.loads(raw)
    if raw != canonical(value) or value != expected_manifest(root):
        raise ValueError("false-notice manifest differs from frozen inputs")
    return value, sha256(raw).hexdigest()


def _action(value: dict) -> Action:
    if set(value) != {"qualify", "primary_order", "spot_order", "production", "shipments"}:
        raise ValueError("saved truthful action shape differs")
    return Action(value["qualify"], value["primary_order"], value["spot_order"],
                  tuple(Production(**x) for x in value["production"]),
                  tuple(Shipment(**x) for x in value["shipments"]))


def _truth_paths(root: Path, wanted: set[str]) -> dict[str, dict]:
    paths = {}
    for line in (root / "results/v3_truthful_calibration_r4/progress.jsonl").read_bytes().splitlines(keepends=True):
        saved = json.loads(line)
        if canonical(saved) != line:
            raise ValueError("truthful calibration progress is noncanonical")
        if saved["path_id"] in wanted:
            if saved["path_id"] in paths or saved["status"] != "completed":
                raise ValueError("duplicate or incomplete truthful reference")
            paths[saved["path_id"]] = saved
    if set(paths) != wanted:
        raise ValueError("truthful reference lacks selected design paths")
    return paths


def _replay_truth(case, saved: dict) -> tuple:
    state = initial_state_v3(case.network)
    notice = Notice(case.long_capacity, "major")
    ledgers = []
    if len(saved["periods"]) != case.network.horizon:
        raise ValueError("truthful reference horizon differs")
    for t, period in enumerate(saved["periods"]):
        if period["period"] != t or period["certificate"]["optimal"] is not True:
            raise ValueError("truthful reference period/certificate differs")
        state, ledger = step_v3(
            case.network, case.contract, notice, state, _action(period["action"]),
            Realization(case.long_capacity[t], case.baseline_demand[t]),
            revealed_schedule=case.long_capacity if t == case.contract.revelation_period else None,
        )
        if _digest_ledger(ledger) != period["ledger_sha256"]:
            raise ValueError("truthful reference ledger replay differs")
        ledgers.append(ledger)
    if sha256(canonical(asdict(state))).hexdigest() != saved["final_state_sha256"]:
        raise ValueError("truthful reference final state differs")
    return tuple(ledgers)


def _digest_ledger(ledger) -> str:
    return sha256(canonical(asdict(ledger))).hexdigest()


def _belief_scenario(case, period: int) -> planner.Scenario:
    """Literal minor until disclosure; true long thereafter."""
    if not 0 <= period < case.network.horizon:
        raise ValueError("period outside frozen horizon")
    if period < case.contract.revelation_period:
        return planner.Scenario("short", 1.0, case.short_capacity)
    return planner.Scenario("long", 1.0, case.long_capacity)


def summarize_pair(case_id: str, truth: tuple, false: tuple) -> dict:
    def totals(rows):
        return {
            "backup": sum(x.backup_delivered for x in rows),
            "primary": sum(x.primary_delivered for x in rows),
            "accepted_shortfall": sum(x.primary_shortfall - x.pre_horizon_order_shortfall for x in rows),
            "pre_horizon_shortfall": sum(x.pre_horizon_order_shortfall for x in rows),
            "buyer_cost": sum(x.operating_cost for x in rows),
        }
    t, f = totals(truth), totals(false)
    b = t["backup"] - f["backup"]
    d = f["primary"] - t["primary"]
    s = f["accepted_shortfall"] - t["accepted_shortfall"]
    return {"case_id": case_id, "truth": t, "false": f,
            "b": b, "delta_d": d, "e": d - b, "s": s,
            "g_3_5": 3 * d - 5 * s,
            "buyer_harm": f["buyer_cost"] - t["buyer_cost"]}


def _failure(path_id: str, period: int | None, error: Exception) -> dict:
    item = {"path_id": path_id, "period": period, "type": type(error).__name__,
            "full_error_text": str(error), "traceback": traceback.format_exc()}
    if isinstance(error, PlannerFailure):
        item["solver_certificate"] = asdict(error.certificate)
    return item


def _alarm(_signum, _frame) -> None:
    raise ResourceStop("false-notice global wall cap reached")


def run(root: Path, manifest_path: Path) -> dict:
    root = root.resolve()
    manifest, manifest_sha = verify_manifest(root, manifest_path)
    _receipt(root / manifest["paths"]["approval_receipt"], DECISION, manifest_sha)
    output = root / RESULT_ROOT
    if output.exists():
        raise FileExistsError("false-notice attempt already exists; zero retries")
    output.mkdir(parents=True)
    (root / manifest["paths"]["attempt_marker"]).write_bytes(canonical({"execution_manifest_sha256": manifest_sha}))
    freeze, rows, _ = _frozen_inputs(root)
    truth = _truth_paths(root, {x["truth_path_id"] for x in manifest["tasks"]})
    caps = manifest["resource_caps"]
    started = monotonic()
    phases = 0
    durations = []
    failures = []
    summaries = []
    completed = 0
    original_solve = planner._solve

    def measured_solve(model, objective, time_limit, phase, **kwargs):
        nonlocal phases
        if phases >= caps["maximum_mip_phases"]:
            raise ResourceStop("false-notice MIP phase cap reached")
        marker = root / manifest["paths"]["first_solve_marker"]
        if not marker.exists():
            marker.write_bytes(canonical({"execution_manifest_sha256": manifest_sha}))
        phases += 1
        began = monotonic()
        try:
            return original_solve(model, objective, time_limit, phase, **kwargs)
        finally:
            duration = monotonic() - began
            durations.append(duration)
            if duration > caps["per_mip_seconds"] + 0.01:
                raise ResourceStop("false-notice per-MIP time cap reached")
            if _rss_mib() > caps["peak_rss_mib"]:
                raise ResourceStop("false-notice peak RSS cap reached")
            if monotonic() - started > caps["wall_seconds"]:
                raise ResourceStop("false-notice wall cap reached")

    planner._solve = measured_solve
    old_alarm = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, caps["wall_seconds"])
    active_id = "<runner>"
    period = None
    try:
        with (root / manifest["paths"]["progress"]).open("xb") as stream:
            for task in manifest["tasks"]:
                active_id, period = task["path_id"], None
                saved = {"path_id": active_id, "status": "running", "periods": []}
                stop = False
                try:
                    case = build_public_case(rows[task["case_id"]])
                    if sha256(canonical(asdict(case))).hexdigest() != task["content_sha256"]:
                        raise ValueError("frozen public design content differs")
                    truth_ledgers = _replay_truth(case, truth[task["truth_path_id"]])
                    state = initial_state_v3(case.network)
                    notice = Notice(case.short_capacity, "minor")
                    false_ledgers = []
                    for period in range(case.network.horizon):
                        scenario = _belief_scenario(case, period)
                        problem = planner.PlanningProblem(
                            case.network, case.contract, notice, state, (scenario,),
                            case.long_capacity[period], case.baseline_demand[period],
                            case.baseline_demand, caps["per_mip_seconds"], False, 1,
                        )
                        choice = planner.plan(problem)
                        state, ledger = step_v3(
                            case.network, case.contract, notice, state, choice.action,
                            Realization(case.long_capacity[period], case.baseline_demand[period]),
                            revealed_schedule=(case.long_capacity if period == case.contract.revelation_period
                                               else None),
                        )
                        false_ledgers.append(ledger)
                        cert = choice.certificate
                        saved["periods"].append({
                            "period": period, "action": asdict(choice.action),
                            "ledger": asdict(ledger),
                            "certificate": {"status": cert.status, "optimal": cert.optimal,
                                            "primal": cert.primal, "dual": cert.dual,
                                            "relative_gap": cert.relative_gap,
                                            "message": cert.message},
                            "handoff_corrections": [asdict(x) for x in choice.handoff.corrections],
                        })
                    saved["status"] = "completed"
                    saved["final_state_sha256"] = sha256(canonical(asdict(state))).hexdigest()
                    summary = summarize_pair(task["case_id"], truth_ledgers, tuple(false_ledgers))
                    saved["paired_summary"] = summary
                    summaries.append(summary)
                    completed += 1
                except Exception as error:
                    failure = _failure(active_id, period, error)
                    saved["status"] = "failed"
                    saved["failure"] = failure
                    failures.append(failure)
                    stop = isinstance(error, ResourceStop)
                stream.write(canonical(saved))
                stream.flush()
                if (root / manifest["paths"]["progress"]).stat().st_size > caps["saved_bytes"]:
                    raise ResourceStop("false-notice progress byte cap reached")
                if stop:
                    break
    except Exception as error:
        failures.append(_failure(active_id, period, error))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_alarm)
        planner._solve = original_solve
    status = "completed" if completed == 56 and not failures else "stopped"
    result = {
        "status": status, "execution_manifest_sha256": manifest_sha,
        "attempted_paths": completed + sum(x["path_id"] not in ("<runner>", "<selection>") for x in failures),
        "completed_paths": completed, "failures": failures,
        "mip_phases": phases, "phase_elapsed_seconds_max": max(durations, default=0.0),
        "elapsed_seconds": monotonic() - started, "peak_rss_mib": _rss_mib(),
        "progress_sha256": _digest(root / manifest["paths"]["progress"]),
        "paired_summaries": summaries if status == "completed" else None,
        "confirmation_private_access": False, "m5_paused": True,
    }
    raw = canonical(result)
    if len(raw) + (root / manifest["paths"]["progress"]).stat().st_size > caps["saved_bytes"]:
        raise ResourceStop("false-notice saved-output byte cap reached")
    (root / manifest["paths"]["result"]).write_bytes(raw)
    return {"status": status, "execution_manifest_sha256": manifest_sha,
            "result_sha256": sha256(raw).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.false_notice_v3_r1")
    parser.add_argument("command", choices=("propose", "review", "run"))
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    path = args.manifest or root / MANIFEST_REL
    if args.command == "propose":
        with path.open("xb") as stream:
            stream.write(canonical(expected_manifest(root)))
        print(json.dumps({"manifest_sha256": _digest(path), "mip_phases": 0}, sort_keys=True))
    elif args.command == "review":
        value, digest = verify_manifest(root, path)
        print(json.dumps({"manifest_sha256": digest, "paths": len(value["tasks"]),
                          "maximum_mip_phases": value["resource_caps"]["maximum_mip_phases"],
                          "mip_phases": 0}, sort_keys=True))
    else:
        print(json.dumps(run(root, path), sort_keys=True))


if __name__ == "__main__":
    main()
