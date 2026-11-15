"""Approved, cache-only pure reporting-equilibrium screen."""

from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import resource
import signal
import sys
from time import monotonic

from .m3 import Run, Task, load_approved, source_hashes
from .m3_report import _metric, _read_task
from .suite import canonical

APPROVED_SHA256 = "7e5ae3da00e5abca6fbbcf9636121178336363f513c8d9bd6596a474a022c63a"
TYPES = ("short", "long")
NOTICES = ("minor", "major")
PROFILES = {
    "truthful_separating": {"short": "minor", "long": "major"},
    "reversed_separating": {"short": "major", "long": "minor"},
    "pool_minor": {"short": "minor", "long": "minor"},
    "pool_major": {"short": "major", "long": "major"},
}
TOLERANCE = 1e-7


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _rss_mib() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value / (1024 * 1024) if sys.platform == "darwin" else value / 1024


def _alarm(_signal_number, _frame) -> None:
    raise RuntimeError("approved M4 wall cap reached")


def load_screen(path: Path, repository_root: Path, m3_manifest: Path,
                m3_run: Path, m3_summary: Path) -> tuple[dict, dict, tuple, str]:
    digest = _digest(path)
    if digest != APPROVED_SHA256:
        raise ValueError("M4 manifest differs from G3-approved SHA-256")
    screen = json.loads(path.read_bytes())
    m3, cases, m3_digest = load_approved(m3_manifest, repository_root)
    if (screen["m3_manifest_sha256"] != m3_digest
            or screen["m3_analysis_summary_sha256"] != _digest(m3_summary)
            or screen["network_count"] != len(cases)
            or screen["sender_pure_profiles"] != list(PROFILES)
            or screen["notices"] != list(NOTICES)
            or screen["supplier_margin_grid"] != m3["payoff_revaluation_only"]["supplier_margin_grid"]
            or screen["shortfall_penalty_grid"] != m3["payoff_revaluation_only"]["shortfall_penalty_grid"]
            or [float(x) for x in screen["off_path_belief_grid_severe"]]
            != [float(x) for x in m3["belief_grid_severe"]]
            or screen["type_prior"] != {"short": 0.5, "long": 0.5}
            or screen["continuation_value"] != 0
            or any(screen[field] for field in ("confirmation_case_access", "local_model_calls",
                                               "paid_api_calls", "new_physical_trajectories"))
            or screen["resource_caps"]["auto_retries"] != 0):
        raise ValueError("M4 inputs differ from approved cached-screen contract")
    run_state = json.loads((m3_run / "run.json").read_bytes())
    summary = json.loads(m3_summary.read_bytes())
    if (run_state["status"] != "complete"
            or run_state["trajectory_tree_sha256"] != screen["m3_trajectory_tree_sha256"]
            or summary["trajectory_tree_sha256"] != run_state["trajectory_tree_sha256"]
            or summary["manifest_sha256"] != m3_digest):
        raise ValueError("M3 run or summary provenance differs from approved M4 inputs")
    return screen, m3, cases, digest


def _on_path(profile: dict[str, str]) -> dict[str, float]:
    posterior = {}
    for notice in NOTICES:
        mass = sum(0.5 for actual in TYPES if profile[actual] == notice)
        if mass:
            posterior[notice] = sum(0.5 for actual in TYPES
                                    if actual == "long" and profile[actual] == notice) / mass
    return posterior


def evaluate_game(cells: dict[tuple[str, str, float], dict[str, float]],
                  margin: float, penalty: float, profile_name: str,
                  off_path_belief: float | None) -> dict:
    profile = PROFILES[profile_name]
    beliefs = _on_path(profile)
    missing = set(NOTICES) - set(beliefs)
    if len(missing) != (off_path_belief is not None):
        raise ValueError("off-path belief does not match profile")
    for notice in missing:
        beliefs[notice] = float(off_path_belief)
    deviations = {}
    on_path_payoffs = {}
    deviation_payoffs = {}
    expected_harm = 0.0
    false_notice_harm = 0.0
    false_frequency = 0.0
    for actual in TYPES:
        notice = profile[actual]
        other = "major" if notice == "minor" else "minor"
        chosen = cells[(actual, notice, beliefs[notice])]
        alternate = cells[(actual, other, beliefs[other])]
        on_payoff = margin * chosen["primary_delivered"] - penalty * chosen["primary_shortfall"]
        dev_payoff = margin * alternate["primary_delivered"] - penalty * alternate["primary_shortfall"]
        deviations[actual] = dev_payoff - on_payoff
        on_path_payoffs[actual] = on_payoff
        deviation_payoffs[actual] = dev_payoff
        truth = "minor" if actual == "short" else "major"
        true_belief = 0.0 if actual == "short" else 1.0
        harm = chosen["cost"] - cells[(actual, truth, true_belief)]["cost"]
        expected_harm += 0.5 * harm
        if notice != truth:
            false_frequency += 0.5
            false_notice_harm += 0.5 * harm
    return {
        "profile": profile_name, "off_path_belief_severe": off_path_belief,
        "beliefs_severe": beliefs, "on_path_payoffs": on_path_payoffs,
        "deviation_payoffs": deviation_payoffs, "deviation_gains": deviations,
        "max_deviation_gain": max(deviations.values()),
        "candidate": all(value <= TOLERANCE for value in deviations.values()),
        "false_notice_frequency": false_frequency,
        "expected_planner_harm": expected_harm,
        "false_notice_paired_harm": false_notice_harm,
    }


def _weighted_cells(m3: dict, cases: tuple, run_root: Path, m3_digest: str,
                    started: float, caps: dict) -> dict[str, dict]:
    weights = [float(x) for x in m3["demand_scenario_weights"]]
    multipliers = [float(x) for x in m3["realized_demand_multipliers"]]
    beliefs = [float(x) for x in m3["belief_grid_severe"]]
    codes = source_hashes()
    case_cells = {}
    for case in cases:
        cells = {}
        for actual in TYPES:
            for notice in NOTICES:
                for belief in beliefs:
                    metrics = [_metric(_read_task(run_root, Task(case.identifier, actual, notice,
                                                                multiplier, "literal", belief),
                                                  case, m3, m3_digest, codes))
                               for multiplier in multipliers]
                    cells[(actual, notice, belief)] = {
                        field: sum(weight * item[field]
                                   for weight, item in zip(weights, metrics))
                        for field in ("cost", "primary_delivered", "primary_shortfall")
                    }
        case_cells[case.identifier] = cells
        if monotonic() - started >= caps["wall_seconds"] or _rss_mib() > caps["process_rss_mib"]:
            raise RuntimeError("approved M4 wall or memory cap reached")
    return case_cells


def screen_games(screen: dict, cases: tuple, case_cells: dict[str, dict],
                 started: float) -> tuple[list[dict], dict]:
    caps = screen["resource_caps"]
    by_family = defaultdict(list)
    for case in cases:
        by_family[case.family].append(case.identifier)
    keys = list(next(iter(case_cells.values())))
    aggregate = {
        key: {field: sum(sum(case_cells[case_id][key][field] for case_id in ids) / len(ids)
                         for ids in by_family.values()) / len(by_family)
              for field in ("cost", "primary_delivered", "primary_shortfall")}
        for key in keys
    }
    games = [("aggregate", "all_design_equal_family", aggregate)] + [
        ("network", case.identifier, case_cells[case.identifier]) for case in cases]
    rows = []
    counts = {"aggregate_games": 0, "network_games": 0, "profile_off_path_checks": 0}
    for scope, game_id, cells in games:
        for margin in screen["supplier_margin_grid"]:
            for penalty in screen["shortfall_penalty_grid"]:
                counts[f"{scope}_games"] += 1
                for profile_name in screen["sender_pure_profiles"]:
                    options = ([None] if len(_on_path(PROFILES[profile_name])) == 2
                               else [float(x) for x in screen["off_path_belief_grid_severe"]])
                    for belief in options:
                        result = evaluate_game(cells, margin, penalty, profile_name, belief)
                        rows.append({"scope": scope, "game_id": game_id,
                                     "margin": margin, "shortfall_penalty": penalty,
                                     **result})
                        counts["profile_off_path_checks"] += 1
                if (monotonic() - started >= caps["wall_seconds"]
                        or _rss_mib() > caps["process_rss_mib"]):
                    raise RuntimeError("approved M4 wall or memory cap reached")
    if (counts["aggregate_games"] != screen["expected_aggregate_games"]
            or counts["network_games"] != screen["expected_network_games"]
            or counts["profile_off_path_checks"] > screen["max_pure_profile_off_path_checks"]):
        raise ValueError("M4 workload exceeds approved counts")
    return rows, counts


def make_result(screen: dict, digest: str, m3_digest: str, rows: list[dict],
                counts: dict, seconds: float) -> dict:
    candidates = [row for row in rows if row["candidate"]]
    consequential = [row for row in candidates if row["false_notice_frequency"] > TOLERANCE
                     and row["false_notice_paired_harm"] > TOLERANCE]
    def tally(subset: list[dict]) -> dict:
        return {profile: sum(row["profile"] == profile for row in subset)
                for profile in PROFILES}
    return {
        "schema": 1, "status": "design_only_cached_pure_screen",
        "manifest_sha256": digest, "m3_manifest_sha256": m3_digest,
        "m3_trajectory_tree_sha256": screen["m3_trajectory_tree_sha256"],
        "m3_analysis_summary_sha256": screen["m3_analysis_summary_sha256"],
        "source_sha256": _digest(Path(__file__)), "tolerance": TOLERANCE,
        "counts": {**counts, "pure_candidates": len(candidates),
                   "consequential_pure_candidates": len(consequential),
                   "aggregate_pure_candidates": sum(row["scope"] == "aggregate" for row in candidates),
                   "aggregate_consequential_pure_candidates": sum(
                       row["scope"] == "aggregate" for row in consequential)},
        "candidate_profiles": tally(candidates),
        "consequential_candidate_profiles": tally(consequential),
        "post_screen_rule_passed": any(row["scope"] == "aggregate"
                                       for row in consequential),
        "resources": {"active_wall_seconds": seconds,
                      "peak_process_rss_mib": _rss_mib()},
        "limitations": ["sampled_off_path_beliefs_only", "mixed_equilibria_unresolved",
                        "policy_changes_between_sampled_beliefs_unresolved"],
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.m4")
    parser.add_argument("command", choices=("plan", "run", "verify"))
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--m3-manifest", type=Path, required=True)
    parser.add_argument("--m3-run", type=Path, required=True)
    parser.add_argument("--m3-summary", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    screen, m3, cases, digest = load_screen(
        args.manifest.resolve(), root, args.m3_manifest.resolve(),
        args.m3_run.resolve(), args.m3_summary.resolve())
    if args.command == "plan":
        print(json.dumps({"manifest_sha256": digest, "network_count": len(cases),
                          "expected_aggregate_games": screen["expected_aggregate_games"],
                          "expected_network_games": screen["expected_network_games"],
                          "resource_caps": screen["resource_caps"]}, sort_keys=True))
        return
    if args.output is None or not args.output.resolve().is_relative_to(root):
        parser.error("M4 output must be specified inside the strategic-notices repository")
    output = args.output.resolve()
    if args.command == "run" and output.exists():
        parser.error("M4 output already exists; no automatic retry or overwrite")
    started = monotonic()
    signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, screen["resource_caps"]["wall_seconds"])
    try:
        checker = Run(args.m3_run.resolve(), m3, cases, screen["m3_manifest_sha256"],
                      resume=False, verify_only=True)
        checker.execute()
        case_cells = _weighted_cells(m3, cases, args.m3_run.resolve(),
                                     screen["m3_manifest_sha256"], started,
                                     screen["resource_caps"])
        rows, counts = screen_games(screen, cases, case_cells, started)
        result = make_result(screen, digest, screen["m3_manifest_sha256"],
                             rows, counts, monotonic() - started)
        if args.command == "verify":
            saved = output.read_bytes()
            recorded = json.loads(saved)
            if saved != canonical(recorded):
                raise ValueError("M4 result bytes are not canonical")
            resources = recorded.get("resources")
            result["resources"] = resources
            if recorded != result:
                raise ValueError("M4 result differs from verified cache recomputation")
            print(json.dumps({"result_sha256": sha256(saved).hexdigest(),
                              "counts": recorded["counts"],
                              "post_screen_rule_passed": recorded["post_screen_rule_passed"]},
                             sort_keys=True))
            return
        raw = canonical(result)
        if (len(raw) > screen["resource_caps"]["disk_mib"] * 1024 * 1024
                or _rss_mib() > screen["resource_caps"]["process_rss_mib"]
                or monotonic() - started >= screen["resource_caps"]["wall_seconds"]):
            raise RuntimeError("approved M4 resource cap reached before output")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(raw)
        print(json.dumps({"result_sha256": sha256(raw).hexdigest(),
                          "counts": result["counts"],
                          "post_screen_rule_passed": result["post_screen_rule_passed"],
                          "resources": result["resources"]}, sort_keys=True))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


if __name__ == "__main__":
    main()
