"""Capped, outcome-blind resource profile on public design cases only."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import resource
import signal
import sys
from time import perf_counter, process_time

import scipy

from sn.core import Contract, Demand, Notice, initial_state
from sn.networks import case_demand_schedule, design_manifest
from sn.planner import PlannerFailure, PlanningProblem, Scenario, plan, run_benchmark
from sn.suite import canonical


class WallBudgetExceeded(RuntimeError):
    pass


def _alarm(_signum, _frame) -> None:
    raise WallBudgetExceeded("profile wall-clock cap reached")


def _save(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(canonical(record))
    temporary.replace(path)


def _rss_mib() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--design-manifest", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wall-cap-seconds", type=float, default=60)
    parser.add_argument("--per-phase-seconds", type=float, default=2)
    parser.add_argument("--rss-cap-mib", type=float, default=2048)
    parser.add_argument("--actual-type", choices=("short", "long"), default="short")
    parser.add_argument("--notice", choices=("minor", "major"), default="minor")
    parser.add_argument("--demand-multiplier", type=float, default=1)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("profile output already exists; no implicit retry or overwrite")
    if any(x <= 0 for x in (args.wall_cap_seconds, args.per_phase_seconds,
                             args.rss_cap_mib, args.demand_multiplier)):
        parser.error("resource caps must be positive")
    raw = args.design_manifest.read_bytes()
    manifest_hash = sha256(raw).hexdigest()
    if manifest_hash != args.expected_sha256:
        parser.error("design manifest hash differs from the declared input")
    manifest = json.loads(raw)
    cases = design_manifest(manifest["design_master_seed"])
    if len(cases) != 24 or len(manifest["networks"]) != len(cases):
        parser.error("expected the frozen 24-case design manifest")
    for case, row in zip(cases, manifest["networks"]):
        if case.identifier != row["id"] or sha256(canonical(asdict(case))).hexdigest() != row["content_sha256"]:
            parser.error("design case content hash mismatch")

    record = {
        "schema": 1, "status": "running", "manifest_sha256": manifest_hash,
        "profile_only": True, "saved_operating_outcomes": False,
        "limits": {"wall_seconds": args.wall_cap_seconds,
                   "per_phase_seconds": args.per_phase_seconds,
                   "rss_mib": args.rss_cap_mib},
        "environment": {"python": sys.version.split()[0],
                        "scipy": scipy.__version__, "platform": platform.platform(),
                        "machine": platform.machine(), "cpu_count": os.cpu_count()},
        "profile_configuration": {
            "onset": "demand_start - 1", "short_duration": 1,
            "long_duration": 3, "prior_severe": 0.5,
            "backlog_cost": 20, "spot_capacity": 2,
            "notice": args.notice, "actual_type_for_rollout": args.actual_type,
            "demand_multiplier": args.demand_multiplier,
        },
        "initial_solves": [], "rolling_strata": [],
    }
    _save(args.output, record)
    started = perf_counter()
    started_cpu = process_time()
    signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, args.wall_cap_seconds)
    try:
        for case in cases:
            network = case.network
            onset = case.demand_start - 1
            short = tuple(0 if onset <= t < onset + 1 else 1
                          for t in range(network.horizon))
            long = tuple(0 if onset <= t < onset + 3 else 1
                         for t in range(network.horizon))
            demand = tuple(tuple(Demand(item.site, item.product,
                                        item.quantity * args.demand_multiplier)
                                 for item in period)
                           for period in case_demand_schedule(case))
            announced = short if args.notice == "minor" else long
            actual = short if args.actual_type == "short" else long
            contract = Contract(revelation_period=onset + 1, backlog_cost=20,
                                spot_capacity=2)
            notice = Notice(announced, f"{args.notice} profile")
            begin_wall, begin_cpu = perf_counter(), process_time()
            choice = plan(PlanningProblem(
                network, contract, notice, initial_state(network),
                (Scenario("short", 0.5, short), Scenario("long", 0.5, long)),
                actual[0], demand[0], demand, args.per_phase_seconds,
            ))
            record["initial_solves"].append({
                "id": case.identifier, "family": case.family, "size": case.size,
                "wall_seconds": perf_counter() - begin_wall,
                "cpu_seconds": process_time() - begin_cpu,
                "peak_process_rss_mib_so_far": _rss_mib(),
                "primary_status": choice.primary_certificate.status,
                "tie_status": choice.tie_certificate.status,
                "primary_nodes": choice.primary_certificate.nodes,
                "tie_nodes": choice.tie_certificate.nodes,
                "variables": choice.primary_certificate.model_variables,
                "integer_variables": choice.primary_certificate.model_integer_variables,
                "constraints": choice.primary_certificate.model_constraints,
                "nonzeros": choice.primary_certificate.model_nonzeros,
            })
            _save(args.output, record)
            if _rss_mib() > args.rss_cap_mib:
                raise MemoryError("profile process exceeded RSS cap")

        for case in cases:
            if case.replicate != 0:
                continue  # One full rolling path per family-size stratum.
            network = case.network
            onset = case.demand_start - 1
            short = tuple(0 if onset <= t < onset + 1 else 1
                          for t in range(network.horizon))
            long = tuple(0 if onset <= t < onset + 3 else 1
                         for t in range(network.horizon))
            demand = tuple(tuple(Demand(item.site, item.product,
                                        item.quantity * args.demand_multiplier)
                                 for item in period)
                           for period in case_demand_schedule(case))
            announced = short if args.notice == "minor" else long
            begin_wall, begin_cpu = perf_counter(), process_time()
            rollout = run_benchmark(
                network=network,
                contract=Contract(revelation_period=onset + 1,
                                  backlog_cost=20, spot_capacity=2),
                notice=Notice(announced, f"{args.notice} profile"),
                short_capacity=short, long_capacity=long,
                actual_type=args.actual_type, realized_demand=demand, mean_demand=demand,
                benchmark="prior_only", prior_severe=0.5, literal_severe=0,
                time_limit=args.per_phase_seconds,
            )
            record["rolling_strata"].append({
                "id": case.identifier, "family": case.family, "size": case.size,
                "wall_seconds": perf_counter() - begin_wall,
                "cpu_seconds": process_time() - begin_cpu,
                "peak_process_rss_mib_so_far": _rss_mib(),
                "periods": len(rollout.ledgers),
                "solve_phases": 2 * len(rollout.certificates),
                "all_optimal": all(primary.optimal and tie.optimal
                                   for primary, tie in rollout.certificates),
            })
            _save(args.output, record)
            if _rss_mib() > args.rss_cap_mib:
                raise MemoryError("profile process exceeded RSS cap")
        record["status"] = "complete"
    except (WallBudgetExceeded, PlannerFailure, MemoryError) as exc:
        record["status"] = "stopped"
        record["stop_reason"] = str(exc)
        if isinstance(exc, PlannerFailure):
            record["solver_failure"] = asdict(exc.certificate)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        record["elapsed_wall_seconds"] = perf_counter() - started
        record["elapsed_cpu_seconds"] = process_time() - started_cpu
        record["peak_process_rss_mib"] = _rss_mib()
        _save(args.output, record)
    print(f"{record['status']}: {len(record['initial_solves'])} initial solves, "
          f"{len(record['rolling_strata'])} rolling paths; "
          f"{record['elapsed_wall_seconds']:.3f} wall seconds")
    if record["status"] != "complete":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
