"""Receipt-gated v3 confirmation opening; no private access on import/review."""

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
from .analysis_v3_frozen import analyze
from .confirmation_v3_r2 import verify_metadata
from .core import Notice, Realization
from .core_v3 import initial_state_v3, step_v3
from .false_notice_v3_r2 import summarize_case
from .opening_audit_v3 import PUBLIC_SHA256, audit_private_set
from .planner_v3_single_r3 import PlannerFailure
from .profile_v3 import _receipt


MANIFEST_REL = "configs/proposed-v3-confirmation-opening.json"
RESULT_ROOT = "results/v3_confirmation_opening"
DECISION = "owner_approved_v3_confirmation_opening"
SOURCES = (
    "src/sn/allocation_v3.py", "src/sn/core.py", "src/sn/core_v2.py",
    "src/sn/core_v3.py", "src/sn/handoff_v3_single.py", "src/sn/networks_v3.py",
    "src/sn/planner_v3_single_r3.py", "src/sn/confirmation_v3.py",
    "src/sn/confirmation_structure_v3.py", "src/sn/confirmation_v3_r2.py",
    "src/sn/opening_audit_v3.py", "src/sn/false_notice_v3_r2.py",
    "src/sn/analysis_v3_frozen.py", "src/sn/confirmation_open_v3.py",
    "src/sn/verify_confirmation_open_v3.py", "src/sn/profile_v3.py",
    "src/sn/verify_false_notice_v3_r3.py",
)
PROTOCOLS = (
    "docs/PROPOSED_V3_PROTOCOL_RECONCILED.md",
    "docs/V3_SINGLE_SOLVE_R3_PROTOCOL.md",
    "docs/V3_PROTOCOL_R4_REPORTING_AMENDMENT.md",
    "docs/V3_PROTOCOL_R5_CONFIRMATION_POPULATION.md",
    "docs/V3_PROTOCOL_R6_FOUR_CELL_DESIGN.md",
    "docs/V3_PROTOCOL_R7_CONFIRMATION_FAILURE_POLICY.md",
    "docs/V3_PRECONFIRMATION_PRECISION_FORECAST_SPEC.md",
)
EVIDENCE = (
    "configs/proposed-v3-allocation.json",
    "configs/v3-design/selection-freeze.json",
    "configs/v3-confirmation-public.json",
    "results/v3_design_false_notice_r2/result.json",
    "results/v3_design_false_notice_r2/verification.json",
    "results/v3_design_false_notice_r2/precision_forecast.json",
    "results/v3_single_full_r4/stability.json",
    "configs/proposed-v3-preopening-robustness.json",
)
CELLS = ("C00", "C10", "C01", "C11")


class ResourceStop(RuntimeError):
    pass


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _rss_mib() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value / (1024 * 1024) if sys.platform == "darwin" else value / 1024


def _read(path: Path) -> dict:
    raw = path.read_bytes()
    value = json.loads(raw)
    if raw != canonical(value):
        raise ValueError(f"noncanonical frozen input: {path}")
    return value


def expected_manifest(root: Path) -> dict:
    """Public-only manifest proposal; does not inspect mounted private files."""
    root = root.resolve()
    if verify_metadata(root, root / "configs/v3-confirmation-public.json") != PUBLIC_SHA256:
        raise ValueError("owner-sealed public metadata hash differs")
    metadata = _read(root / "configs/v3-confirmation-public.json")
    forecast = _read(root / "results/v3_design_false_notice_r2/precision_forecast.json")
    verification = _read(root / "results/v3_design_false_notice_r2/verification.json")
    g1 = _read(root / "results/v3_single_full_r4/stability.json")
    if (forecast["status"] != "locked_design_only_before_confirmation_opening"
            or forecast["confirmation_private_access"] is not False
            or forecast["input_sha256"]["independent_verification"] !=
               _digest(root / "results/v3_design_false_notice_r2/verification.json")
            or forecast["input_sha256"]["design_result"] !=
               _digest(root / "results/v3_design_false_notice_r2/result.json")
            or verification["status"] != "verified"
            or verification["complete_crossed_paths"] != 168
            or g1["status"] != "completed" or g1["completed_paths"] != 1344
            or g1["path_failure_count"] != 0
            or g1["pinned_gate_acceptance"] is not True
            or g1["stability_acceptance"] is not True
            or g1["sensitivity_optimal_agreement"] is not True):
        raise ValueError("G1 or locked design evidence differs")
    tasks = [{"path_id": item["case_id"] + "|" + cell,
              "case_id": item["case_id"], "cell": cell,
              "content_sha256": item["content_sha256"],
              "structure_sha256": item["structure_sha256"]}
             for item in metadata["cases"] for cell in CELLS]
    if len(tasks) != 224 or len({x["path_id"] for x in tasks}) != 224:
        raise ValueError("confirmation four-cell task count differs")
    return {
        "schema": 1, "kind": "v3_confirmation_opening_execution",
        "status": "proposal_unrun_pending_exact_owner_hash",
        "public_metadata_sha256": PUBLIC_SHA256,
        "source_sha256": {name: _digest(root / name) for name in SOURCES},
        "protocol_sha256": {name: _digest(root / name) for name in PROTOCOLS},
        "evidence_sha256": {name: _digest(root / name) for name in EVIDENCE},
        "test_sha256": _digest(root / "tests/test_v3_confirmation_opening.py"),
        "tasks": tasks,
        "cell_semantics": {
            "C00": {"notice": "major", "belief": "long"},
            "C10": {"notice": "minor", "belief": "long"},
            "C01": {"notice": "major", "belief": "short"},
            "C11": {"notice": "minor", "belief": "short"},
        },
        "actual_type": "long", "demand_multiplier": 1.0,
        "planner": {"source": "planner_v3_single_r3", "presolve": False,
                    "threads": 1, "MIP_phases_per_period": 1},
        "analysis": {"source": "analysis_v3_frozen", "purpose": "confirmation",
                     "all_24_R1_interior_in_H1": True,
                     "eight_boundaries_descriptive": True,
                     "gated_secondary_family": True},
        "preopening_gate": {"manifest_sha256": _digest(
            root / "configs/proposed-v3-preopening-robustness.json"),
            "complete_zero_failure_result_required_before_private_access": True},
        "resource_caps": {"cases": 56, "paths": 224, "periods_per_path": 20,
                          "maximum_mip_phases": 4480, "per_mip_seconds": 30,
                          "wall_seconds": 600, "peak_rss_mib": 512,
                          "saved_bytes": 67108864, "retries": 0},
        "receipt_decision": DECISION,
        "paths": {"approval_receipt": "configs/owner-approvals/v3-confirmation-opening.json",
                  "output_root": RESULT_ROOT,
                  "attempt_marker": RESULT_ROOT + "/attempt.started",
                  "audit": RESULT_ROOT + "/audit.json",
                  "first_solve_marker": RESULT_ROOT + "/first_solve.started",
                  "progress": RESULT_ROOT + "/progress.jsonl",
                  "result": RESULT_ROOT + "/result.json",
                  "verification": RESULT_ROOT + "/verification.json"},
        "scope": {"private_seed_read": False, "private_path_supplied_at_run": True,
                  "private_audit_all_56_before_first_solve": True,
                  "case_exclusion": False, "retries": 0, "m5_paused": True},
    }


def verify_manifest(root: Path, path: Path) -> tuple[dict, str]:
    root, path = root.resolve(), path.resolve()
    if path != root / MANIFEST_REL:
        raise ValueError("unexpected confirmation manifest path")
    raw = path.read_bytes()
    value = json.loads(raw)
    if raw != canonical(value) or value != expected_manifest(root):
        raise ValueError("confirmation manifest differs from frozen public inputs")
    return value, sha256(raw).hexdigest()


def _failure(path_id: str, period: int | None, error: BaseException) -> dict:
    item = {"path_id": path_id, "period": period, "type": type(error).__name__,
            "full_error_text": str(error), "traceback": traceback.format_exc()}
    if isinstance(error, PlannerFailure):
        item["solver_certificate"] = asdict(error.certificate)
    return item


def _alarm(_signum, _frame) -> None:
    raise ResourceStop("confirmation wall cap reached")


def _require_preopening_pass(root: Path, manifest: dict) -> None:
    """Check the public-seed engineering gate before opening private files."""
    proposal = root / "configs/proposed-v3-preopening-robustness.json"
    result_path = root / "results/v3_preopening_robustness/result.json"
    progress_path = root / "results/v3_preopening_robustness/progress.jsonl"
    if (not result_path.is_file() or not progress_path.is_file()
            or _digest(proposal) != manifest["preopening_gate"]["manifest_sha256"]):
        raise PermissionError("approved pre-opening robustness result is unavailable")
    result = _read(result_path)
    if (result["status"] != "completed" or result["failures"]
            or result["execution_manifest_sha256"] != _digest(proposal)
            or result["completed_paths"] != 2240 or result["mip_phases"] != 44800
            or result["progress_sha256"] != _digest(progress_path)
            or result["private_case_access"] is not False
            or result["scientific_quantities_saved"] is not False):
        raise PermissionError("pre-opening public-seed robustness gate did not pass")


def run(root: Path, manifest_path: Path, private_root: Path) -> dict:
    """The only private-opening entry point. Never accepts a seed path."""
    root = root.resolve()
    manifest, manifest_sha = verify_manifest(root, manifest_path)
    _receipt(root / manifest["paths"]["approval_receipt"], DECISION, manifest_sha)
    _require_preopening_pass(root, manifest)
    output = root / RESULT_ROOT
    if output.exists():
        raise FileExistsError("confirmation opening attempt already exists; zero retries")
    output.mkdir(parents=True)
    (output / "attempt.started").write_bytes(canonical({"execution_manifest_sha256": manifest_sha}))
    started = monotonic()
    caps = manifest["resource_caps"]
    audit_path = output / "audit.json"
    metadata = _read(root / "configs/v3-confirmation-public.json")
    try:
        cases = audit_private_set(root, private_root, metadata)
        if monotonic() - started > caps["wall_seconds"] or _rss_mib() > caps["peak_rss_mib"]:
            raise ResourceStop("confirmation all-case audit exceeded wall or memory cap")
        audit = {"status": "passed", "count": len(cases),
                 "public_metadata_sha256": PUBLIC_SHA256,
                 "content_sha256": {item["case_id"]: item["content_sha256"]
                                    for item in metadata["cases"]},
                 "structure_sha256": {item["case_id"]: item["structure_sha256"]
                                      for item in metadata["cases"]},
                 "mip_phases_before_audit": 0, "private_seed_read": False}
    except Exception as error:
        audit = {"status": "failed", "failure": _failure("<audit>", None, error),
                 "mip_phases_before_audit": 0, "private_seed_read": False}
        audit_path.write_bytes(canonical(audit))
        return {"status": "audit_failed_no_solve", "audit_sha256": _digest(audit_path),
                "execution_manifest_sha256": manifest_sha}
    audit_path.write_bytes(canonical(audit))
    phases, durations, failures, completed = 0, [], [], 0
    case_records: dict[str, dict] = {}
    case_rows = []
    original_solve = planner._solve

    def measured_solve(model, objective, time_limit, phase, **kwargs):
        nonlocal phases
        if phases >= caps["maximum_mip_phases"]:
            raise ResourceStop("confirmation MIP phase cap reached")
        marker = output / "first_solve.started"
        if not marker.exists():
            marker.write_bytes(canonical({"execution_manifest_sha256": manifest_sha,
                                          "audit_sha256": _digest(audit_path)}))
        phases += 1
        began = monotonic()
        try:
            return original_solve(model, objective, time_limit, phase, **kwargs)
        finally:
            duration = monotonic() - began
            durations.append(duration)
            if duration > caps["per_mip_seconds"] + 0.01:
                raise ResourceStop("confirmation per-MIP cap reached")
            if _rss_mib() > caps["peak_rss_mib"]:
                raise ResourceStop("confirmation peak RSS cap reached")
            if monotonic() - started > caps["wall_seconds"]:
                raise ResourceStop("confirmation wall cap reached")

    planner._solve = measured_solve
    previous_alarm = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, caps["wall_seconds"])
    try:
        with (output / "progress.jsonl").open("xb") as stream:
            for task in manifest["tasks"]:
                path_id, cell = task["path_id"], task["cell"]
                saved = {"path_id": path_id, "cell": cell, "status": "running", "periods": []}
                period = None
                try:
                    case = cases[task["case_id"]]
                    if sha256(canonical(asdict(case))).hexdigest() != task["content_sha256"]:
                        raise ValueError("audited case changed before planning")
                    state = initial_state_v3(case.network)
                    minor = cell in ("C10", "C11")
                    notice = Notice(case.short_capacity if minor else case.long_capacity,
                                    "minor" if minor else "major")
                    ledgers = []
                    for period in range(case.network.horizon):
                        scenario = planner.Scenario(
                            "long" if period >= case.contract.revelation_period or
                            cell in ("C00", "C10") else "short", 1.0,
                            case.long_capacity if period >= case.contract.revelation_period or
                            cell in ("C00", "C10") else case.short_capacity)
                        problem = planner.PlanningProblem(
                            case.network, case.contract, notice, state, (scenario,),
                            case.long_capacity[period], case.baseline_demand[period],
                            case.baseline_demand, caps["per_mip_seconds"], False, 1)
                        choice = planner.plan(problem)
                        state, ledger = step_v3(
                            case.network, case.contract, notice, state, choice.action,
                            Realization(case.long_capacity[period], case.baseline_demand[period]),
                            revealed_schedule=(case.long_capacity if period == case.contract.revelation_period
                                               else None))
                        ledgers.append(ledger)
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
                    dates = [p["period"] for p in saved["periods"] if p["action"]["qualify"]]
                    case_records.setdefault(case.case_id, {})[cell] = (tuple(ledgers), dates)
                    if cell == "C11":
                        case_rows.append(summarize_case(
                            {"case_id": case.case_id, "regime": case.regime,
                             "category": case.category, "family": case.family,
                             "size": case.size}, case_records[case.case_id]))
                    completed += 1
                except Exception as error:
                    saved["status"] = "failed"
                    saved["failure"] = _failure(path_id, period, error)
                    failures.append(saved["failure"])
                stream.write(canonical(saved))
                stream.flush()
                if saved["status"] == "failed":
                    break
                if stream.tell() > caps["saved_bytes"]:
                    failures.append({"path_id": path_id, "period": period,
                                     "type": "ResourceStop", "full_error_text":
                                     "confirmation progress byte cap reached", "traceback": ""})
                    break
    except Exception as error:
        failures.append(_failure("<runner>", None, error))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_alarm)
        planner._solve = original_solve
    status = "completed" if completed == 224 and not failures and len(case_rows) == 56 else "stopped"
    analysis = None
    if status == "completed":
        try:
            analysis = analyze(case_rows, purpose="confirmation")
            if monotonic() - started > caps["wall_seconds"] or _rss_mib() > caps["peak_rss_mib"]:
                raise ResourceStop("confirmation analysis exceeded wall or memory cap")
        except Exception as error:
            failures.append(_failure("<analysis>", None, error))
            status = "stopped"
    progress_path = output / "progress.jsonl"
    result = {
        "status": status, "execution_manifest_sha256": manifest_sha,
        "audit_sha256": _digest(audit_path), "completed_paths": completed,
        "attempted_paths": completed + sum(x["path_id"] not in ("<runner>", "<analysis>")
                                         for x in failures),
        "failures": failures, "mip_phases": phases,
        "phase_elapsed_seconds_max": max(durations, default=0.0),
        "elapsed_seconds": monotonic() - started, "peak_rss_mib": _rss_mib(),
        "progress_sha256": _digest(progress_path),
        "case_rows": case_rows if status == "completed" else None,
        "analysis_confirmation": analysis if status == "completed" else None,
        "partial_outcomes": None if status == "completed" else {
            "status": "incomplete_descriptive_no_H1_decision",
            "completed_paths": completed,
            "fully_crossed_cases": len(case_rows),
            "fully_crossed_case_rows": case_rows,
            "path_evidence": manifest["paths"]["progress"],
        },
        "private_seed_read": False, "m5_paused": True,
    }
    raw = canonical(result)
    if len(raw) + progress_path.stat().st_size > caps["saved_bytes"]:
        raise ResourceStop("confirmation saved-output byte cap reached")
    (output / "result.json").write_bytes(raw)
    return {"status": status, "result_sha256": sha256(raw).hexdigest(),
            "execution_manifest_sha256": manifest_sha}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.confirmation_open_v3")
    parser.add_argument("command", choices=("propose", "review", "run"))
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--private-root", type=Path)
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
        if args.private_root is None:
            parser.error("run requires --private-root")
        print(json.dumps(run(root, path, args.private_root), sort_keys=True))


if __name__ == "__main__":
    main()
