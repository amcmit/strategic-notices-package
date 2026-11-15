"""Manifest-bound, resumable M3 design-only trajectory execution."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import resource
import signal
import sys
from time import monotonic

from .core import (Action, Contract, Demand, Notice, Production, Realization,
                   Shipment, audit_step, initial_state, step)
from .networks import NetworkCase, case_demand_schedule, design_manifest
from .planner import PlannerFailure, run_benchmark
from .suite import canonical

APPROVED_MANIFEST_SHA256 = "a53b7ac7b343ac95f5c393b1c0339d9f1e2e5a1470bd8c2c2577d1e7ce5aa0c4"
SOURCE_FILES = ("core.py", "planner.py", "networks.py", "suite.py", "m3.py")


@dataclass(frozen=True)
class Task:
    case_id: str
    actual_type: str
    notice_name: str
    demand_multiplier: float
    benchmark: str
    severe_belief: float | None


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def load_approved(path: Path, repository_root: Path) -> tuple[dict, tuple[NetworkCase, ...], str]:
    digest = _digest(path)
    if digest != APPROVED_MANIFEST_SHA256:
        raise ValueError("M3 manifest differs from the G2-approved SHA-256")
    manifest = json.loads(path.read_bytes())
    design_path = repository_root / manifest["design_manifest"]
    if _digest(design_path) != manifest["design_manifest_sha256"]:
        raise ValueError("design manifest hash mismatch")
    design = json.loads(design_path.read_bytes())
    cases = design_manifest(design["design_master_seed"])
    if len(cases) != manifest["network_count"] or len(design["networks"]) != len(cases):
        raise ValueError("design case count mismatch")
    for case, row in zip(cases, design["networks"]):
        if (case.identifier != row["id"]
                or sha256(canonical(asdict(case))).hexdigest() != row["content_sha256"]):
            raise ValueError(f"design case hash mismatch: {case.identifier}")
    if (manifest["confirmation_case_access"] or manifest["local_model_calls"]
            or manifest["paid_api_calls"] or manifest["resource_caps"]["auto_retries"] != 0):
        raise ValueError("M3 access or retry flags are not approved")
    return manifest, cases, digest


def source_hashes() -> dict[str, str]:
    root = Path(__file__).parent
    return {name: _digest(root / name) for name in SOURCE_FILES}


def base_tasks(manifest: dict, cases: tuple[NetworkCase, ...]) -> tuple[Task, ...]:
    return tuple(
        Task(case.identifier, actual, notice, float(multiplier), "literal", float(belief))
        for case in cases
        for actual in manifest["actual_types"]
        for notice in manifest["notice_schedules"]
        for multiplier in manifest["realized_demand_multipliers"]
        for belief in manifest["belief_grid_severe"]
    )


def clairvoyant_tasks(manifest: dict, cases: tuple[NetworkCase, ...]) -> tuple[Task, ...]:
    return tuple(
        Task(case.identifier, actual, notice, float(multiplier), "clairvoyant", None)
        for case in cases
        for actual in manifest["actual_types"]
        for notice in manifest["notice_schedules"]
        for multiplier in manifest["realized_demand_multipliers"]
    )


def _schedule(case: NetworkCase, duration: int) -> tuple[float, ...]:
    onset = case.demand_start - 1
    return tuple(0.0 if onset <= t < onset + duration else case.primary_capacity
                 for t in range(case.network.horizon))


def _demands(case: NetworkCase, multiplier: float) -> tuple[tuple[Demand, ...], ...]:
    return tuple(tuple(Demand(item.site, item.product, item.quantity * multiplier)
                       for item in period) for period in case_demand_schedule(case))


def scientific_input(task: Task, case: NetworkCase, manifest: dict,
                     manifest_hash: str, code_hashes: dict[str, str]) -> dict:
    short = _schedule(case, manifest["short_duration"])
    long = _schedule(case, manifest["long_duration"])
    notice_schedule = short if task.notice_name == "minor" else long
    contract = Contract(revelation_period=case.demand_start,
                        **manifest["contract"])
    mean = _demands(case, manifest["future_conditional_mean_demand_multiplier"])
    realized = _demands(case, task.demand_multiplier)
    return {
        "schema": 1, "manifest_sha256": manifest_hash,
        "source_sha256": code_hashes,
        "case_id": case.identifier,
        "case_content_sha256": sha256(canonical(asdict(case))).hexdigest(),
        "actual_type": task.actual_type, "notice_name": task.notice_name,
        "notice": asdict(Notice(notice_schedule, task.notice_name)),
        "short_capacity": short, "long_capacity": long,
        "contract": asdict(contract), "benchmark": task.benchmark,
        "severe_belief": task.severe_belief,
        "public_severe_prior": manifest["public_severe_prior"],
        "realized_demand_multiplier": task.demand_multiplier,
        "realized_demand": [[asdict(item) for item in period] for period in realized],
        "mean_demand": [[asdict(item) for item in period] for period in mean],
        "per_solve_phase_time_limit_seconds": manifest["per_solve_phase_time_limit_seconds"],
    }


def _key(payload: dict) -> str:
    return sha256(canonical(payload)).hexdigest()


def _atomic(path: Path, value: dict) -> int:
    raw = canonical(value)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(raw)
    temporary.replace(path)
    return len(raw)


def _wall_alarm(_signum, _frame) -> None:
    raise RuntimeError("approved M3 wall cap reached")


def _read_cached(path: Path, payload: dict) -> dict | None:
    if not path.exists():
        return None
    raw = path.read_bytes()
    record = json.loads(raw)
    if raw != canonical(record) or record.get("input") != json.loads(canonical(payload)):
        raise ValueError(f"cache input or canonical bytes mismatch: {path.name}")
    if not isinstance(record.get("rollout"), dict):
        raise ValueError(f"cache rollout missing: {path.name}")
    return record


def _tree_digest(directory: Path) -> str:
    entries = [(path.name, _digest(path)) for path in sorted(directory.glob("*.json"))]
    return sha256(canonical(entries)).hexdigest()


def _verify_physical(record: dict, case: NetworkCase) -> None:
    payload, rollout = record["input"], record["rollout"]
    actual = tuple(payload["short_capacity"] if payload["actual_type"] == "short"
                   else payload["long_capacity"])
    contract = Contract(**payload["contract"])
    notice = Notice(tuple(payload["notice"]["announced_capacity"]), payload["notice"]["text"])
    actions, ledgers, certificates = (rollout[field] for field in ("actions", "ledgers", "certificates"))
    if not (len(actions) == len(ledgers) == len(certificates) == case.network.horizon):
        raise ValueError("cached rollout length mismatch")
    state = initial_state(case.network)
    operating_cost = 0.0
    for t, (row, saved, pair) in enumerate(zip(actions, ledgers, certificates)):
        action = Action(
            qualify=row["qualify"], primary_order=row["primary_order"],
            spot_order=row["spot_order"],
            production=tuple(Production(**item) for item in row["production"]),
            shipments=tuple(Shipment(**item) for item in row["shipments"]),
        )
        demand = tuple(Demand(**item) for item in payload["realized_demand"][t])
        before = state
        state, ledger = step(
            case.network, contract, notice, state, action,
            Realization(actual[t], demand),
            revealed_schedule=actual if t == contract.revelation_period else None,
        )
        audit_step(case.network, before, state, ledger)
        if json.loads(canonical(asdict(ledger))) != saved:
            raise ValueError(f"cached physical ledger mismatch at period {t}")
        for certificate in pair:
            if (not certificate["optimal"] or certificate["status"] != 0
                    or certificate["primal"] is None or certificate["dual"] is None
                    or certificate["primal"] - certificate["dual"] >
                    certificate["checked_primal_dual_absolute_tolerance"] + 1e-9):
                raise ValueError(f"cached solver certificate unresolved at period {t}")
        operating_cost += ledger.operating_cost
    if abs(operating_cost - rollout["operating_cost"]) > 1e-7:
        raise ValueError("cached operating cost mismatch")


def _rss_mib() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


class Run:
    def __init__(self, root: Path, manifest: dict, cases: tuple[NetworkCase, ...],
                 manifest_hash: str, *, resume: bool, verify_only: bool = False):
        self.root = root
        self.manifest = manifest
        self.cases = {case.identifier: case for case in cases}
        self.manifest_hash = manifest_hash
        self.code_hashes = source_hashes()
        self.verify_only = verify_only
        self.started = monotonic()
        if verify_only or resume:
            self.state = json.loads((root / "run.json").read_bytes())
            if (self.state["manifest_sha256"] != manifest_hash
                    or self.state["source_sha256"] != self.code_hashes):
                raise ValueError("run provenance differs from current manifest or source")
            if not verify_only and self.state["status"] not in ("running", "interrupted"):
                raise ValueError("only an interrupted running process may resume")
        else:
            root.mkdir(parents=True, exist_ok=False)
            (root / "trajectories").mkdir()
            self.state = {
                "schema": 1, "status": "running", "manifest_sha256": manifest_hash,
                "source_sha256": self.code_hashes, "completed": 0,
                "active_wall_seconds": 0.0, "trajectory_bytes": 0,
                "peak_process_rss_mib": _rss_mib(),
            }
            _atomic(root / "run.json", self.state)
        self.previous_elapsed = float(self.state["active_wall_seconds"])
        self.seen: set[str] = set()
        self.max_count = int(manifest["max_total_trajectory_count"])
        if resume:
            # A process can die after an atomic trajectory rename but before
            # its run-state checkpoint. Count those durable files on resume.
            files = list((root / "trajectories").glob("*.json"))
            self.state["completed"] = len(files)
            self.state["trajectory_bytes"] = sum(path.stat().st_size for path in files)
            self.state["status"] = "running"
            self._checkpoint()

    def _check_caps(self, extra_bytes: int = 0) -> None:
        caps = self.manifest["resource_caps"]
        if self.previous_elapsed + monotonic() - self.started >= caps["wall_seconds"]:
            raise RuntimeError("approved M3 wall cap reached")
        if _rss_mib() > caps["process_rss_mib"]:
            raise RuntimeError("approved M3 process RSS cap reached")
        if self.state["trajectory_bytes"] + extra_bytes > caps["disk_mib"] * 1024 * 1024:
            raise RuntimeError("approved M3 trajectory disk cap reached")
        if self.state["completed"] >= self.max_count:
            raise RuntimeError("approved M3 trajectory-count cap reached")

    def _checkpoint(self) -> None:
        if self.verify_only:
            return
        self.state["active_wall_seconds"] = self.previous_elapsed + monotonic() - self.started
        self.state["peak_process_rss_mib"] = max(self.state["peak_process_rss_mib"], _rss_mib())
        _atomic(self.root / "run.json", self.state)

    def get(self, task: Task) -> dict:
        case = self.cases[task.case_id]
        payload = scientific_input(task, case, self.manifest, self.manifest_hash, self.code_hashes)
        key = _key(payload)
        if key in self.seen:
            raise ValueError("duplicate scientific input in task plan")
        self.seen.add(key)
        path = self.root / "trajectories" / f"{key}.json"
        cached = _read_cached(path, payload)
        if cached is not None:
            _verify_physical(cached, case)
            return cached
        if self.verify_only:
            raise ValueError(f"missing trajectory cache: {key}")
        self._check_caps()
        short = tuple(payload["short_capacity"])
        long = tuple(payload["long_capacity"])
        demand = _demands(case, task.demand_multiplier)
        mean = _demands(case, self.manifest["future_conditional_mean_demand_multiplier"])
        rollout = run_benchmark(
            network=case.network,
            contract=Contract(**payload["contract"]),
            notice=Notice(tuple(payload["notice"]["announced_capacity"]),
                          payload["notice"]["text"]),
            short_capacity=short, long_capacity=long,
            actual_type=task.actual_type, realized_demand=demand, mean_demand=mean,
            benchmark=task.benchmark,
            prior_severe=self.manifest["public_severe_prior"],
            literal_severe=task.severe_belief if task.severe_belief is not None else 0,
            time_limit=self.manifest["per_solve_phase_time_limit_seconds"],
        )
        record = json.loads(canonical({"input": payload, "rollout": asdict(rollout)}))
        raw_size = len(canonical(record))
        self._check_caps(raw_size)
        _atomic(path, record)
        self.state["completed"] += 1
        self.state["trajectory_bytes"] += raw_size
        self._checkpoint()
        if self.state["completed"] % 100 == 0:
            print(f"M3 cached {self.state['completed']} trajectories", flush=True)
        return record

    def execute(self) -> None:
        base = base_tasks(self.manifest, tuple(self.cases.values()))
        clairvoyant = clairvoyant_tasks(self.manifest, tuple(self.cases.values()))
        if len(base) != self.manifest["base_trajectory_count"] or len(clairvoyant) != self.manifest["additional_clairvoyant_trajectories"]:
            raise ValueError("approved M3 workload count mismatch")
        for task in (*base, *clairvoyant):
            self.get(task)
        grid = [float(value) for value in self.manifest["belief_grid_severe"]]
        max_refinements = self.manifest["max_adaptive_belief_refinements_per_network_type_notice_demand"]
        for case in self.cases.values():
            for actual in self.manifest["actual_types"]:
                for notice in self.manifest["notice_schedules"]:
                    for multiplier in self.manifest["realized_demand_multipliers"]:
                        samples: dict[float, tuple] = {}
                        for belief in grid:
                            task = Task(case.identifier, actual, notice, float(multiplier), "literal", belief)
                            payload = scientific_input(task, case, self.manifest, self.manifest_hash, self.code_hashes)
                            record = _read_cached(self.root / "trajectories" / f"{_key(payload)}.json", payload)
                            if record is None:
                                raise ValueError("base trajectory missing before adaptive search")
                            samples[belief] = policy_signature(record)
                        for _ in range(max_refinements):
                            ordered = sorted(samples)
                            changed = [(b - a, a, b) for a, b in zip(ordered, ordered[1:])
                                       if samples[a] != samples[b]]
                            if not changed:
                                break
                            _, left, right = max(changed, key=lambda row: (row[0], -row[1]))
                            middle = round((left + right) / 2, 12)
                            if middle in samples:
                                break
                            task = Task(case.identifier, actual, notice, float(multiplier), "literal", middle)
                            record = self.get(task)
                            samples[middle] = policy_signature(record)
        if self.verify_only:
            if self.state["status"] != "complete" or len(self.seen) != self.state["completed"]:
                raise ValueError("completed status or cache count mismatch")
            files = list((self.root / "trajectories").glob("*.json"))
            if len(files) != len(self.seen):
                raise ValueError("extra or missing trajectory file")
            if _tree_digest(self.root / "trajectories") != self.state.get("trajectory_tree_sha256"):
                raise ValueError("trajectory tree SHA-256 mismatch")
            return
        self.state["status"] = "complete"
        self.state["trajectory_tree_sha256"] = _tree_digest(self.root / "trajectories")
        self._checkpoint()


def policy_signature(record: dict) -> tuple:
    """Observed action signature for transition bracketing, rounded only for comparison."""
    actions = record["rollout"]["actions"]
    return tuple((bool(action["qualify"]), round(action["primary_order"], 6),
                  round(action["spot_order"], 6),
                  tuple((item["site"], item["product"], round(item["quantity"], 6))
                        for item in action["production"]),
                  tuple((item["lane_index"], round(item["quantity"], 6))
                        for item in action["shipments"])) for action in actions)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.m3")
    parser.add_argument("command", choices=("plan", "run", "resume", "verify"))
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    manifest, cases, digest = load_approved(args.manifest.resolve(), root)
    if args.command == "plan":
        print(json.dumps({"manifest_sha256": digest, "design_cases": len(cases),
                          "base_trajectories": len(base_tasks(manifest, cases)),
                          "clairvoyant_trajectories": len(clairvoyant_tasks(manifest, cases)),
                          "max_total_trajectories": manifest["max_total_trajectory_count"],
                          "resource_caps": manifest["resource_caps"]}, sort_keys=True))
        return
    if args.output is None:
        parser.error("--output is required for run, resume and verify")
    if not args.output.resolve().is_relative_to(root):
        parser.error("M3 output must be inside the strategic-notices repository")
    runner = Run(args.output.resolve(), manifest, cases, digest,
                 resume=args.command == "resume", verify_only=args.command == "verify")
    if args.command != "verify":
        signal.signal(signal.SIGALRM, _wall_alarm)
        remaining = manifest["resource_caps"]["wall_seconds"] - runner.previous_elapsed
        if remaining <= 0:
            parser.error("approved M3 wall cap already exhausted")
        signal.setitimer(signal.ITIMER_REAL, remaining)
    try:
        runner.execute()
    except (Exception, KeyboardInterrupt) as exc:
        if args.command != "verify":
            runner.state["status"] = "stopped" if not isinstance(exc, KeyboardInterrupt) else "interrupted"
            runner.state["stop_reason"] = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, PlannerFailure):
                runner.state["solver_failure"] = asdict(exc.certificate)
            runner._checkpoint()
        raise
    finally:
        if args.command != "verify":
            signal.setitimer(signal.ITIMER_REAL, 0)
    print(f"M3 {args.command}: {runner.state['completed']} trajectories; status {runner.state['status']}")


if __name__ == "__main__":
    main()
