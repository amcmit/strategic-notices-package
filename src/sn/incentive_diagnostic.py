"""Exploratory, manifest-bound incentive diagnostic from verified M3 ledgers."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import resource
import signal
import sys
from time import monotonic

from .m3 import Run, Task, load_approved, source_hashes
from .m3_report import _read_task
from .suite import canonical
from .toy import ToyContract, ToyNotice, simulate_toy, supplier_payoff as toy_payoff

APPROVED_SHA256 = "d92ec2e8da741daaddd1446710e324e80346f154158bbb40fc4f42a751b5ca7e"
FIELDS = ("primary_due", "primary_delivered", "primary_shortfall", "accepted_new",
          "backup_delivered", "spot_delivered", "backup_active_periods",
          "primary_order_total", "spot_order_total")
SETTINGS = {"truthful": ("major", 1.0), "joint": ("minor", 0.0),
            "fixed_belief": ("minor", 1.0)}


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _rss_mib() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value / (1024 * 1024) if sys.platform == "darwin" else value / 1024


def _alarm(_signal_number, _frame) -> None:
    raise RuntimeError("approved exploratory diagnostic wall cap reached")


def load_inputs(root: Path, proposed: Path, m3_path: Path, run: Path,
                m3_summary: Path, g3_followup: Path) -> tuple[dict, dict, tuple, str, dict]:
    digest = _digest(proposed)
    if digest != APPROVED_SHA256:
        raise ValueError("diagnostic manifest differs from owner-approved SHA-256")
    plan = json.loads(proposed.read_bytes())
    m3, cases, m3_digest = load_approved(m3_path, root)
    state = json.loads((run / "run.json").read_bytes())
    summary = json.loads(m3_summary.read_bytes())
    followup = json.loads(g3_followup.read_bytes())
    if (plan["m3_manifest_sha256"] != m3_digest
            or plan["m3_trajectory_tree_sha256"] != state["trajectory_tree_sha256"]
            or state["status"] != "complete"
            or summary["trajectory_tree_sha256"] != state["trajectory_tree_sha256"]
            or plan["m3_analysis_summary_sha256"] != _digest(m3_summary)
            or plan["g3_followup_diagnostic_sha256"] != _digest(g3_followup)
            or followup["trajectory_tree_sha256"] != state["trajectory_tree_sha256"]
            or plan["network_count"] != len(cases)
            or plan["exact_cached_cell_count"] != len(cases) * 3 * len(SETTINGS)
            or plan["demand_scenario_multipliers"] != m3["realized_demand_multipliers"]
            or plan["margin_primary"] != 3
            or plan["margin_descriptive_sensitivity"] != [1, 5]
            or plan["primary_comparison"] != {
                "truthful": {"structured_notice": "major", "severe_belief": 1.0},
                "under_report": {"structured_notice": "minor", "severe_belief": 0.0}}
            or plan["fixed_belief_order_comparison"] != {
                "truthful": {"structured_notice": "major", "severe_belief": 1.0},
                "under_report": {"structured_notice": "minor", "severe_belief": 1.0}}
            or any(plan[field] for field in ("new_physical_trajectories", "mip_solves",
                                              "confirmation_case_access", "local_model_calls",
                                              "paid_api_calls", "equilibrium_selection_work"))
            or not plan["m5_paused"] or plan["resource_caps"]["auto_retries"] != 0):
        raise ValueError("diagnostic provenance or approved scope mismatch")
    return plan, m3, cases, digest, followup


def _totals(record: dict, tolerance: float) -> dict[str, float]:
    actions = record["rollout"]["actions"]
    ledgers = record["rollout"]["ledgers"]
    if len(actions) != len(ledgers):
        raise ValueError("action and ledger lengths differ")
    for row in ledgers:
        if abs(row["primary_due"] - row["primary_delivered"]
               - row["primary_shortfall"]) > tolerance:
            raise ValueError("accepted due does not reconcile with delivery and shortfall")
    result = {field: sum(row[field] for row in ledgers) for field in
              ("primary_due", "primary_delivered", "primary_shortfall",
               "accepted_new", "backup_delivered", "spot_delivered")}
    result["backup_active_periods"] = sum(row["backup_delivered"] > tolerance
                                          for row in ledgers)
    result["primary_order_total"] = sum(row["primary_order"] for row in actions)
    result["spot_order_total"] = sum(row["spot_order"] for row in actions)
    if abs(result["primary_due"] - result["accepted_new"]) > tolerance:
        raise ValueError("accepted orders do not reconcile over the horizon")
    return result


def _paired(truth: dict, false: dict, tolerance: float) -> dict:
    old, new = (record["rollout"] for record in (truth, false))
    if len(old["actions"]) != len(new["actions"]):
        raise ValueError("paired paths have different horizons")
    differences = []
    for t, (a, b, x, y) in enumerate(zip(old["actions"], new["actions"],
                                     old["ledgers"], new["ledgers"])):
        primary = b["primary_order"] - a["primary_order"]
        spot = b["spot_order"] - a["spot_order"]
        accepted = y["accepted_new"] - x["accepted_new"]
        differences.append({
            "period": t, "primary_order_difference": primary,
            "spot_order_difference": spot, "accepted_new_difference": accepted,
            "primary_due_difference": y["primary_due"] - x["primary_due"],
            "primary_delivered_difference": y["primary_delivered"] - x["primary_delivered"],
            "primary_shortfall_difference": y["primary_shortfall"] - x["primary_shortfall"],
            "backup_delivered_difference": y["backup_delivered"] - x["backup_delivered"],
            "qualification_changed": bool(a["qualify"] != b["qualify"]),
        })
    flags = {
        "primary_order": any(abs(row["primary_order_difference"]) > tolerance
                             for row in differences),
        "spot_order": any(abs(row["spot_order_difference"]) > tolerance
                          for row in differences),
        "accepted_obligation": any(abs(row["accepted_new_difference"]) > tolerance
                                   for row in differences),
        "qualification": any(row["qualification_changed"] for row in differences),
    }
    flags["either_order"] = flags["primary_order"] or flags["spot_order"]
    before = _totals(truth, tolerance)
    after = _totals(false, tolerance)
    delta = {field: after[field] - before[field] for field in FIELDS}
    for field, key in (("primary_order_total", "primary_order_difference"),
                       ("spot_order_total", "spot_order_difference"),
                       ("accepted_new", "accepted_new_difference"),
                       ("primary_due", "primary_due_difference"),
                       ("primary_delivered", "primary_delivered_difference"),
                       ("primary_shortfall", "primary_shortfall_difference"),
                       ("backup_delivered", "backup_delivered_difference")):
        if abs(delta[field] - sum(row[key] for row in differences)) > tolerance:
            raise ValueError(f"period and horizon {field} differences disagree")
    return {"truthful_totals": before, "under_report_totals": after,
            "delta": delta, "order_change_flags": flags,
            "period_differences": differences}


def critical_penalty(delivered_delta: float, shortfall_delta: float,
                     margin: float, tolerance: float, old_grid_max: float) -> dict:
    intercept = margin * delivered_delta
    if abs(shortfall_delta) <= tolerance:
        classification = ("identically_zero" if abs(intercept) <= tolerance else
                          "constant_positive" if intercept > 0 else "constant_negative")
        root = None
        critical = None
    else:
        root = intercept / shortfall_delta
        if root > tolerance:
            classification = "positive_crossing"
            critical = root
        elif abs(root) <= tolerance:
            classification = "zero_endpoint_only"
            critical = 0.0
        else:
            classification = "negative_root_outside_domain"
            critical = None
    return {"margin": margin, "classification": classification,
            "algebraic_root": root, "critical_nonnegative_penalty": critical,
            "within_former_0_to_10_grid": (critical is not None and
                                          critical <= old_grid_max + tolerance),
            "gain_at_penalty_0": intercept,
            "gain_at_penalty_5": intercept - 5 * shortfall_delta,
            "gain_at_penalty_10": intercept - 10 * shortfall_delta}


def _decomposition(delivered: float, shortfall: float, backup: float,
                   margin: float, penalty: float, tolerance: float) -> dict:
    retained = margin * delivered
    penalty_term = -penalty * shortfall
    residual = margin * (delivered + backup)
    backup_proxy = -margin * backup
    gain = retained + penalty_term
    if abs(gain - (residual + backup_proxy + penalty_term)) > tolerance:
        raise ValueError("three-column accounting bridge does not reconcile")
    return {"margin": margin, "penalty": penalty,
            "retained_primary_margin": retained,
            "shortfall_penalty_term": penalty_term,
            "supplier_gain": gain,
            "noncausal_backup_allocation_proxy": backup_proxy,
            "nonbackup_volume_residual": residual}


def _toy_check(plan: dict, tolerance: float) -> dict:
    contract = ToyContract()
    truthful = simulate_toy("long", ToyNotice.named("major"), contract)
    false = simulate_toy("long", ToyNotice.named("minor"), contract)
    delta = {
        "primary_delivered": sum(x.primary_delivered for x in false.periods)
                             - sum(x.primary_delivered for x in truthful.periods),
        "primary_shortfall": sum(x.primary_shortfall for x in false.periods)
                             - sum(x.primary_shortfall for x in truthful.periods),
        "backup_delivered": sum(x.backup_delivered for x in false.periods)
                            - sum(x.backup_delivered for x in truthful.periods),
    }
    expected = plan["toy_oracle_at_margin3"]
    if (delta["primary_delivered"] != expected["long_false_minus_truth_primary_delivered"]
            or delta["primary_shortfall"] != expected["long_false_minus_truth_primary_shortfall"]
            or delta["backup_delivered"] != expected["long_false_minus_truth_backup_delivered"]):
        raise ValueError("frozen toy component oracle disagrees")
    for penalty in (0, 5, expected["critical_penalty"]):
        direct = (toy_payoff(false, margin=3, penalty=penalty)
                  - toy_payoff(truthful, margin=3, penalty=penalty))
        if abs(direct - (6 - penalty)) > tolerance:
            raise ValueError("frozen toy gain oracle disagrees")
    root = critical_penalty(delta["primary_delivered"], delta["primary_shortfall"],
                            3, tolerance, 10)
    if abs(root["critical_nonnegative_penalty"] - expected["critical_penalty"]) > tolerance:
        raise ValueError("frozen toy critical penalty disagrees")
    return {"delta": delta, "critical_penalty_at_margin3": root,
            "decomposition_at_penalty5": _decomposition(2, 1, -4, 3, 5, tolerance)}


def _family_mean(rows: list[dict], field: str) -> float:
    groups = defaultdict(list)
    for row in rows:
        groups[row["family"]].append(row["delta"][field])
    return sum(sum(values) / len(values) for values in groups.values()) / len(groups)


def build_result(plan: dict, m3: dict, cases: tuple, digest: str, followup: dict,
                 run: Path, started: float) -> dict:
    tolerance = float(plan["quantity_tolerance"])
    multipliers = [float(x) for x in m3["realized_demand_multipliers"]]
    weights = [float(x) for x in m3["demand_scenario_weights"]]
    codes = source_hashes()
    network_rows = []
    scenario_rows = []
    for case in cases:
        pairs = []
        for multiplier, weight in zip(multipliers, weights):
            records = {name: _read_task(run,
                         Task(case.identifier, "long", notice, multiplier, "literal", belief),
                         case, m3, plan["m3_manifest_sha256"], codes)
                       for name, (notice, belief) in SETTINGS.items()}
            joint = _paired(records["truthful"], records["joint"], tolerance)
            fixed = _paired(records["truthful"], records["fixed_belief"], tolerance)
            pairs.append((weight, joint, fixed))
            scenario_rows.append({"case_id": case.identifier, "family": case.family,
                                  "demand_multiplier": multiplier, "weight": weight,
                                  "joint": joint, "fixed_belief": fixed})
        delta = {field: sum(weight * joint["delta"][field]
                            for weight, joint, _ in pairs) for field in FIELDS}
        if abs(delta["primary_due"] - delta["primary_delivered"]
               - delta["primary_shortfall"]) > tolerance:
            raise ValueError("weighted primary order flow does not reconcile")
        margins = {}
        for margin in (plan["margin_primary"], *plan["margin_descriptive_sensitivity"]):
            threshold = critical_penalty(delta["primary_delivered"],
                                         delta["primary_shortfall"], margin,
                                         tolerance, max(plan["approved_discrete_penalty_grid_for_reference"]))
            penalties = [0.0, 5.0]
            if threshold["critical_nonnegative_penalty"] is not None:
                penalties.append(float(threshold["critical_nonnegative_penalty"]))
            components = [_decomposition(delta["primary_delivered"],
                                         delta["primary_shortfall"],
                                         delta["backup_delivered"], margin,
                                         penalty, tolerance) for penalty in penalties]
            for penalty in (0, 5):
                direct = sum(weight * (
                    margin * joint["under_report_totals"]["primary_delivered"]
                    - penalty * joint["under_report_totals"]["primary_shortfall"]
                    - margin * joint["truthful_totals"]["primary_delivered"]
                    + penalty * joint["truthful_totals"]["primary_shortfall"])
                    for weight, joint, _ in pairs)
                if abs(direct - (margin * delta["primary_delivered"]
                                 - penalty * delta["primary_shortfall"])) > tolerance:
                    raise ValueError("direct ledger payoff and affine gain disagree")
            margins[str(margin)] = {"critical_penalty": threshold,
                                    "decomposition": components}
        order_flags = {}
        for name, index in (("joint", 1), ("fixed_belief", 2)):
            order_flags[name] = {
                field: {"any_scenario": any(pair[index]["order_change_flags"][field]
                                            for pair in pairs),
                        "all_three_scenarios": all(pair[index]["order_change_flags"][field]
                                                   for pair in pairs)}
                for field in ("primary_order", "spot_order", "either_order",
                              "accepted_obligation", "qualification")}
        row = {"case_id": case.identifier, "family": case.family, "size": case.size,
               "delta": delta, "backup_units_displaced_proxy": -delta["backup_delivered"],
               "margin_results": margins, "order_change_flags": order_flags}
        reference = next(item for item in followup["network_rows"]
                         if item["case_id"] == case.identifier)
        if (abs(delta["primary_delivered"] - reference["delivered_difference"]) > tolerance
                or abs(delta["primary_shortfall"] - reference["shortfall_difference"]) > tolerance
                or abs(margins["3"]["critical_penalty"]["gain_at_penalty_5"]
                       - reference["supplier_gain_margin3_penalty5"]) > tolerance):
            raise ValueError("network row differs from frozen G3 diagnostic")
        network_rows.append(row)
        if (monotonic() - started >= plan["resource_caps"]["wall_seconds"]
                or _rss_mib() > plan["resource_caps"]["process_rss_mib"]):
            raise RuntimeError("approved exploratory diagnostic resource cap reached")
    if len(scenario_rows) != plan["network_count"] * len(multipliers):
        raise ValueError("exploratory diagnostic scenario count mismatch")
    mean_delta = {field: _family_mean(network_rows, field) for field in FIELDS}
    prior = followup["overall_equal_family_mean"]
    if (abs(mean_delta["primary_delivered"] - prior["delivered_difference"]) > tolerance
            or abs(mean_delta["primary_shortfall"] - prior["shortfall_difference"]) > tolerance):
        raise ValueError("equal-family means differ from G3 diagnostic")
    order_counts = {
        comparison: {field: {
            criterion: sum(row["order_change_flags"][comparison][field][criterion]
                           for row in network_rows)
            for criterion in ("any_scenario", "all_three_scenarios")}
            for field in ("primary_order", "spot_order", "either_order",
                          "accepted_obligation", "qualification")}
        for comparison in ("joint", "fixed_belief")}
    threshold_counts = {
        str(margin): dict(Counter(row["margin_results"][str(margin)]
                                  ["critical_penalty"]["classification"]
                                  for row in network_rows))
        for margin in (plan["margin_primary"], *plan["margin_descriptive_sensitivity"])}
    return {
        "schema": 1, "status": "exploratory_outside_approved_grid",
        "manifest_sha256": digest,
        "m3_trajectory_tree_sha256": plan["m3_trajectory_tree_sha256"],
        "source_sha256": _digest(Path(__file__)),
        "network_count": len(network_rows), "exact_cached_cell_count": plan["exact_cached_cell_count"],
        "scenario_count": len(scenario_rows),
        "mip_solves": 0, "new_physical_trajectories": 0,
        "primary_margin": plan["margin_primary"],
        "equal_family_delta": mean_delta,
        "equal_family_gain_margin3_penalty0": 3 * mean_delta["primary_delivered"],
        "equal_family_gain_margin3_penalty5": (3 * mean_delta["primary_delivered"]
                                               - 5 * mean_delta["primary_shortfall"]),
        "threshold_classification_counts": threshold_counts,
        "order_change_network_counts": order_counts,
        "toy_validation": _toy_check(plan, tolerance),
        "network_rows": network_rows, "scenario_rows": scenario_rows,
        "limitations": ["descriptive_outside_approved_grid",
                        "backup_allocation_proxy_not_causal",
                        "no_equilibrium_or_confirmation_claim"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.incentive_diagnostic")
    parser.add_argument("command", choices=("plan", "run", "verify"))
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--m3-manifest", type=Path, required=True)
    parser.add_argument("--m3-run", type=Path, required=True)
    parser.add_argument("--m3-summary", type=Path, required=True)
    parser.add_argument("--g3-followup", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    plan, m3, cases, digest, followup = load_inputs(
        root, args.manifest.resolve(), args.m3_manifest.resolve(),
        args.m3_run.resolve(), args.m3_summary.resolve(), args.g3_followup.resolve())
    if args.command == "plan":
        print(json.dumps({"manifest_sha256": digest, "network_count": len(cases),
                          "exact_cached_cell_count": plan["exact_cached_cell_count"],
                          "resource_caps": plan["resource_caps"]}, sort_keys=True))
        return
    if args.output is None or not args.output.resolve().is_relative_to(root):
        parser.error("diagnostic output must be inside this repository")
    output = args.output.resolve()
    if args.command == "run" and output.exists():
        parser.error("diagnostic output exists; no overwrite or automatic retry")
    started = monotonic()
    signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, plan["resource_caps"]["wall_seconds"])
    try:
        checker = Run(args.m3_run.resolve(), m3, cases, plan["m3_manifest_sha256"],
                      resume=False, verify_only=True)
        checker.execute()
        result = build_result(plan, m3, cases, digest, followup,
                              args.m3_run.resolve(), started)
        if args.command == "verify":
            raw = output.read_bytes()
            saved = json.loads(raw)
            if raw != canonical(saved) or saved.get("resources") is None:
                raise ValueError("saved diagnostic is not canonical or lacks resource account")
            result["resources"] = saved["resources"]
            if saved != result:
                raise ValueError("diagnostic differs from independently replayed M3 cache")
            print(json.dumps({"result_sha256": sha256(raw).hexdigest(),
                              "network_count": result["network_count"],
                              "order_change_network_counts": result["order_change_network_counts"]},
                             sort_keys=True))
            return
        result["resources"] = {"active_wall_seconds": monotonic() - started,
                               "peak_process_rss_mib": _rss_mib()}
        raw = canonical(result)
        if (len(raw) > plan["resource_caps"]["disk_mib"] * 1024 * 1024
                or _rss_mib() > plan["resource_caps"]["process_rss_mib"]
                or monotonic() - started >= plan["resource_caps"]["wall_seconds"]):
            raise RuntimeError("approved exploratory diagnostic resource cap reached before output")
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        if temporary.exists():
            raise ValueError("stale output temporary file; no automatic retry")
        temporary.write_bytes(raw)
        temporary.replace(output)
        print(json.dumps({"result_sha256": sha256(raw).hexdigest(),
                          "network_count": result["network_count"],
                          "threshold_classification_counts": result["threshold_classification_counts"],
                          "order_change_network_counts": result["order_change_network_counts"],
                          "resources": result["resources"], "output_bytes": len(raw)}, sort_keys=True))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


if __name__ == "__main__":
    main()
