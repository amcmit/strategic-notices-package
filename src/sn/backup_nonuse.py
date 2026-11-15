"""Cached-only diagnosis of backup nonuse under truthful major notices."""

from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import gzip
import heapq
import json
from pathlib import Path
import resource
import signal
import sys
from time import monotonic

from .m3 import Run, Task, load_approved, source_hashes
from .m3_report import _read_task
from .networks import NetworkCase, feature_levels, numeric_features
from .suite import canonical
from .toy import ToyContract

APPROVED_SHA256 = "c2f62b0429d898269115fb73f41b2d399f34f9684545a4b1db5ffe71b31882ac"
LEVELS = ("sole", "dual", "multi")


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _rss_mib() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value / (1024 * 1024) if sys.platform == "darwin" else value / 1024


def _alarm(_number, _frame) -> None:
    raise RuntimeError("approved backup diagnostic wall cap reached")


def load_inputs(root: Path, proposal: Path, m3_path: Path, run: Path,
                incentive_archive: Path) -> tuple[dict, dict, tuple[NetworkCase, ...], str, dict]:
    digest = _digest(proposal)
    if digest != APPROVED_SHA256:
        raise ValueError("backup diagnostic manifest differs from approved SHA-256")
    plan = json.loads(proposal.read_bytes())
    m3, cases, m3_digest = load_approved(m3_path, root)
    archived = incentive_archive.read_bytes()
    prior_raw = gzip.decompress(archived) if incentive_archive.suffix == ".gz" else archived
    prior = json.loads(prior_raw)
    run_state = json.loads((run / "run.json").read_bytes())
    counts = Counter(feature_levels(case)["substitutability"] for case in cases)
    if (plan["design_manifest_sha256"] != m3["design_manifest_sha256"]
            or plan["m3_manifest_sha256"] != m3_digest
            or plan["m3_trajectory_tree_sha256"] != run_state["trajectory_tree_sha256"]
            or run_state["status"] != "complete"
            or plan["incentive_result_sha256"] != sha256(prior_raw).hexdigest()
            or canonical(prior) != prior_raw
            or prior["m3_trajectory_tree_sha256"] != run_state["trajectory_tree_sha256"]
            or plan["network_count"] != len(cases) or len(cases) != 24
            or plan["substitutability_column_counts"] != dict(counts)
            or plan["cached_truthful_major_cell"] != {
                "actual_type": "long", "structured_notice": "major",
                "severe_belief": 1.0, "benchmark": "literal",
                "demand_multipliers": m3["realized_demand_multipliers"],
                "exact_cached_cell_count": len(cases) * len(m3["realized_demand_multipliers"])}
            or plan["expected_path_date_savings_ceiling_rows"] != 576
            or any(plan[key] for key in ("new_physical_trajectories", "new_mip_solves",
                                        "generator_changes", "parameter_changes",
                                        "confirmation_case_access", "local_model_calls",
                                        "paid_api_calls", "equilibrium_selection_work"))
            or not plan["m5_paused"] or plan["resource_caps"]["auto_retries"] != 0):
        raise ValueError("backup diagnostic provenance or scope mismatch")
    if len(prior["network_rows"]) != len(cases):
        raise ValueError("archived incentive network count mismatch")
    return plan, m3, cases, digest, prior


def product_leads(case: NetworkCase) -> dict[tuple[str, str], int]:
    """Optimistic topology lead from backup injection, ignoring recipe co-inputs."""
    graph: dict[tuple[str, str], list[tuple[tuple[str, str], int]]] = {}
    for lane in case.network.lanes:
        graph.setdefault((lane.origin, lane.product), []).append(
            ((lane.destination, lane.product), lane.lead))
    for recipe in case.network.recipes:
        for product, _ in recipe.inputs:
            graph.setdefault((recipe.site, product), []).append(
                ((recipe.site, recipe.output), 0))
    source = (case.network.primary_site, case.network.primary_product)
    leads = {source: 0}
    frontier = [(0, source)]
    while frontier:
        lead, key = heapq.heappop(frontier)
        if lead != leads[key]:
            continue
        for destination, extra in graph.get(key, []):
            candidate = lead + extra
            if candidate < leads.get(destination, 10**9):
                leads[destination] = candidate
                heapq.heappush(frontier, (candidate, destination))
    return leads


def savings_ceiling(ledgers: list[dict], *, qualification_date: int,
                    qualification_lead: int, qualification_fee: float,
                    backup_price: float, backup_minimum: float,
                    demand_keys: dict[tuple[str, str], list[int]],
                    leads: dict[tuple[str, str], int],
                    primary_primal: float, tolerance: float) -> dict:
    horizon = len(ledgers)
    arrival = qualification_date + qualification_lead
    if not 0 <= qualification_date < horizon or arrival >= horizon:
        raise ValueError("qualification date has no active backup period")
    paid_periods = horizon - arrival
    mandatory = qualification_fee + backup_price * backup_minimum * paid_periods
    spot = sum(row["spot_cost"] for row in ledgers[arrival:])
    backlog = sum(row["backlog_charge"] for row in ledgers[arrival:])
    ceiling = spot + backlog
    earliest = {f"{site}/{product}": arrival + leads[(site, product)]
                if (site, product) in leads else None for site, product in demand_keys}
    useful_demand_periods = sorted({t for key, periods in demand_keys.items()
                                    for t in periods if key in leads and
                                    t >= arrival + leads[key]})
    no_demand_reachable = all(value is None or value >= horizon
                              for value in earliest.values())
    certified = []
    if no_demand_reachable:
        certified.append("backup_cannot_reach_any_demand_key_before_horizon")
    if mandatory > primary_primal + tolerance:
        certified.append("mandatory_payment_exceeds_current_planner_primal")
    return {
        "qualification_date": qualification_date,
        "first_backup_delivery_at_primary_site": arrival,
        "mandatory_paid_periods": paid_periods,
        "mandatory_payment": mandatory,
        "cached_post_arrival_spot_cost": spot,
        "cached_post_arrival_backlog_charge": backlog,
        "cached_line_item_savings_ceiling": ceiling,
        "ceiling_minus_mandatory_payment": ceiling - mandatory,
        "primary_solve_primal_at_qualification_date": primary_primal,
        "earliest_topological_demand_arrival": earliest,
        "potentially_useful_demand_periods": useful_demand_periods,
        "potentially_useful_demand_period_count": len(useful_demand_periods),
        "paid_minus_potentially_useful_periods": paid_periods - len(useful_demand_periods),
        "certified_date_exclusion_reasons": certified,
        "line_item_ceiling_is_global_benefit_bound": False,
    }


def toy_row(plan: dict) -> dict:
    contract = ToyContract()
    expected = plan["toy_reference"]
    if (contract.horizon != len(expected["periods"]) or contract.onset != expected["long_outage_onset"]
            or contract.qualification_lead != expected["backup_qualification_lead"]
            or contract.backup_minimum != expected["backup_minimum_per_active_period"]
            or contract.backup_price != expected["backup_unit_cost"]
            or contract.qualification_cost != expected["qualification_fee"]
            or contract.spot_price != expected["spot_unit_cost"]):
        raise ValueError("toy source differs from frozen comparison row")
    outage = list(range(contract.onset, contract.onset + expected["long_outage_length"]))
    active = list(range(contract.qualification_lead, contract.horizon))
    if (outage != expected["outage_demand_overlap_periods"]
            or active != expected["truthful_major_backup_active_periods"]
            or expected["displaced_backup_units"] != len(active) * contract.backup_minimum
            or expected["incremental_primary_deliveries"] != 2
            or expected["incremental_shortfall"] != 1):
        raise ValueError("toy mechanism oracle differs from frozen comparison row")
    return {**expected, "mandatory_payment_truthful_major": (
        contract.qualification_cost + len(active) * contract.backup_price * contract.backup_minimum),
        "nonbackup_primary_delivery_residual": (expected["incremental_primary_deliveries"]
                                                 - expected["displaced_backup_units"]),
        "demand_overlap_count": len(outage)}


def _demand_keys(ledgers: list[dict], tolerance: float) -> dict[tuple[str, str], list[int]]:
    result: dict[tuple[str, str], list[int]] = {}
    for t, row in enumerate(ledgers):
        for item in row["new_demand"]:
            if item["quantity"] > tolerance:
                result.setdefault((item["site"], item["product"]), []).append(t)
    return result


def _buffer_cover(case: NetworkCase, ledgers: list[dict],
                  demand: dict[tuple[str, str], list[int]]) -> list[dict]:
    stock = {(item.site, item.product): item.quantity for item in case.network.initial_stock}
    rows = []
    for key, periods in sorted(demand.items()):
        first = periods[0]
        first_quantity = sum(item["quantity"] for item in ledgers[first]["new_demand"]
                             if (item["site"], item["product"]) == key)
        opening = sum(item["quantity"] for item in ledgers[first]["opening_stock"]
                      if (item["site"], item["product"]) == key)
        initial = stock.get(key, 0.0)
        if first_quantity <= 0:
            raise ValueError("demand-key first-period quantity must be positive")
        rows.append({"site": key[0], "product": key[1],
                     "first_demand_period": first, "first_period_demand": first_quantity,
                     "initial_buffer_units": initial,
                     "initial_buffer_cover_periods": initial / first_quantity,
                     "opening_stock_at_first_demand": opening,
                     "opening_cover_periods": opening / first_quantity})
    return rows


def _alternative_sources(case: NetworkCase, actions: list[dict]) -> list[dict]:
    result = []
    for source in case.alternative_sources:
        lanes = [(i, lane) for i, lane in enumerate(case.network.lanes)
                 if lane.origin == source and lane.product == case.network.primary_product]
        indices = {index for index, _ in lanes}
        shipped = sum(item["quantity"] for action in actions
                      for item in action["shipments"]
                      if item["lane_index"] in indices)
        stock = sum(item.quantity for item in case.network.initial_stock
                    if item.site == source and item.product == case.network.primary_product)
        result.append({"source": source, "initial_stock_units": stock,
                       "outbound_lane_indices": [index for index, _ in lanes],
                       "nominal_outbound_capacity_per_period": sum(lane.capacity for _, lane in lanes),
                       "outbound_leads": [lane.lead for _, lane in lanes],
                       "observed_shipped_units": shipped})
    return result


def build_result(plan: dict, m3: dict, cases: tuple[NetworkCase, ...],
                 manifest_sha: str, prior: dict, run: Path, started: float) -> dict:
    tolerance = plan["quantity_tolerance"]
    contract = m3["contract"]
    if (contract["qualification_cost"] != 2 or contract["qualification_lead"] != 2
            or contract["backup_price"] != 2 or contract["backup_minimum"] != 1
            or contract["spot_price"] != 6 or contract["spot_capacity"] != 2
            or contract["backlog_cost"] != 20):
        raise ValueError("frozen cost comparison parameters differ from proposal")
    source_hash = source_hashes()
    run_rows = []
    date_rows = []
    network_rows = []
    for case in cases:
        level = feature_levels(case)["substitutability"]
        features = numeric_features(case)
        leads = product_leads(case)
        path_rows = []
        for multiplier in m3["realized_demand_multipliers"]:
            task = Task(case.identifier, "long", "major", float(multiplier), "literal", 1.0)
            record = _read_task(run, task, case, m3, plan["m3_manifest_sha256"], source_hash)
            rollout = record["rollout"]
            actions, ledgers, certificates = (rollout[key] for key in
                                               ("actions", "ledgers", "certificates"))
            if len(actions) != case.network.horizon or len(ledgers) != len(actions):
                raise ValueError("cached truthful-major path length mismatch")
            if any(action["qualify"] for action in actions) or any(
                    abs(row["backup_delivered"]) > tolerance for row in ledgers):
                raise ValueError("approved zero-backup premise fails on cached path")
            demand = _demand_keys(ledgers, tolerance)
            if not demand or any(key not in leads for key in demand):
                raise ValueError("backup product cannot reach a recorded demand key")
            onset = case.demand_start - 1
            outage = list(range(onset, onset + m3["long_duration"]))
            overlap = sorted(set(outage) & {t for periods in demand.values() for t in periods})
            if len(outage) != 3 or len(overlap) != 2:
                raise ValueError("frozen design disruption-demand overlap changed")
            first_demand = min(t for periods in demand.values() for t in periods)
            if first_demand != case.demand_start:
                raise ValueError("cached demand timing differs from frozen design")
            q_rows = []
            for q in range(case.network.horizon - contract["qualification_lead"]):
                certificate = certificates[q][0]
                if (certificate["phase"] != "primary" or not certificate["optimal"]
                        or certificate["primal"] is None):
                    raise ValueError("cached primary solver certificate not usable")
                item = savings_ceiling(
                    ledgers, qualification_date=q,
                    qualification_lead=contract["qualification_lead"],
                    qualification_fee=contract["qualification_cost"],
                    backup_price=contract["backup_price"],
                    backup_minimum=contract["backup_minimum"],
                    demand_keys=demand, leads=leads,
                    primary_primal=certificate["primal"], tolerance=tolerance)
                q_rows.append(item)
                date_rows.append({"case_id": case.identifier, "family": case.family,
                                  "substitutability": level,
                                  "demand_multiplier": multiplier, **item})
            total_spot = sum(row["spot_cost"] for row in ledgers)
            total_backlog = sum(row["backlog_charge"] for row in ledgers)
            backlog_units = sum(item["quantity"] for row in ledgers for item in row["backlog"])
            demand_arrivals = {f"{site}/{product}": sum(item["quantity"]
                for row in ledgers for item in row["arrivals"]
                if (item["site"], item["product"]) == (site, product))
                for site, product in demand}
            row = {
                "case_id": case.identifier, "family": case.family, "size": case.size,
                "substitutability": level, "demand_multiplier": multiplier,
                "first_demand_period": first_demand, "long_outage_periods": outage,
                "corrected_recovery_period": onset + m3["long_duration"],
                "outage_demand_overlap_periods": overlap,
                "buffer_cover": _buffer_cover(case, ledgers, demand),
                "alternative_sources": _alternative_sources(case, actions),
                "demand_site_arrivals_units": demand_arrivals,
                "observed_primary_delivered_units": sum(row["primary_delivered"] for row in ledgers),
                "observed_spot_units": sum(row["spot_delivered"] for row in ledgers),
                "observed_spot_cost": total_spot,
                "observed_backlog_unit_periods": backlog_units,
                "observed_backlog_charge": total_backlog,
                "all_dates_certified_excluded": all(item["certified_date_exclusion_reasons"]
                                                    for item in q_rows),
                "certified_excluded_date_count": sum(bool(item["certified_date_exclusion_reasons"])
                                                     for item in q_rows),
                "observed_pattern": ("no_cached_spot_or_backlog" if total_spot <= tolerance
                                     and total_backlog <= tolerance else
                                     "cached_spot_and_backlog" if total_spot > tolerance
                                     and total_backlog > tolerance else
                                     "cached_spot_only" if total_spot > tolerance
                                     else "cached_backlog_only"),
                "causal_binding_reason": ("all_current_qualification_dates_excluded_by_certificates"
                                          if all(item["certified_date_exclusion_reasons"]
                                                 for item in q_rows)
                                          else "unresolved_without_forced_qualification_solve"),
            }
            run_rows.append(row)
            path_rows.append(row)
        network_rows.append({
            "case_id": case.identifier, "family": case.family, "size": case.size,
            "substitutability": level, "features": features,
            "primary_site": case.network.primary_site,
            "primary_product": case.network.primary_product,
            "demand_keys": sorted({key for row in path_rows for key in row["demand_site_arrivals_units"]}),
            "topological_leads": {f"{site}/{product}": lead for (site, product), lead in leads.items()
                                  if f"{site}/{product}" in {key for row in path_rows
                                                           for key in row["demand_site_arrivals_units"]}},
            "long_outage_length": m3["long_duration"],
            "qualification_lead": contract["qualification_lead"],
            "backup_minimum": contract["backup_minimum"],
            "backup_unit_cost": contract["backup_price"],
            "qualification_fee": contract["qualification_cost"],
            "spot_unit_cost": contract["spot_price"],
            "spot_capacity_per_period": contract["spot_capacity"],
            "stockout_cost_per_unit_period": contract["backlog_cost"],
            "path_count": len(path_rows),
            "paths_all_dates_certified_excluded": sum(row["all_dates_certified_excluded"]
                                                      for row in path_rows),
            "causal_binding_reason": ("certified_for_all_three_paths" if all(
                row["all_dates_certified_excluded"] for row in path_rows)
                else "unresolved_for_one_or_more_paths"),
            "observed_patterns": dict(Counter(row["observed_pattern"] for row in path_rows)),
        })
        if (monotonic() - started >= plan["resource_caps"]["wall_seconds"]
                or _rss_mib() > plan["resource_caps"]["process_rss_mib"]):
            raise RuntimeError("approved backup diagnostic resource cap reached")
    if (len(run_rows) != plan["cached_truthful_major_cell"]["exact_cached_cell_count"]
            or len(date_rows) != plan["expected_path_date_savings_ceiling_rows"]):
        raise ValueError("backup diagnostic workload count mismatch")
    prior_by_id = {row["case_id"]: row for row in prior["network_rows"]}
    if any(abs(prior_by_id[row["case_id"]]["delta"]["backup_delivered"]) > tolerance
           for row in network_rows):
        raise ValueError("prior incentive backup comparison is not zero")
    return {
        "schema": 1, "status": "exploratory_cached_design_backup_nonuse",
        "manifest_sha256": manifest_sha,
        "m3_trajectory_tree_sha256": plan["m3_trajectory_tree_sha256"],
        "incentive_result_sha256": plan["incentive_result_sha256"],
        "source_sha256": _digest(Path(__file__)),
        "network_count": len(network_rows), "path_count": len(run_rows),
        "path_date_count": len(date_rows),
        "substitutability_counts": dict(Counter(row["substitutability"] for row in network_rows)),
        "certified_network_count": sum(row["causal_binding_reason"] == "certified_for_all_three_paths"
                                       for row in network_rows),
        "certified_path_count": sum(row["all_dates_certified_excluded"] for row in run_rows),
        "toy_comparison_row": toy_row(plan),
        "supplier_incentive_condition": {
            "displaced_backup_units": "b=B_truth-B_false",
            "nonbackup_residual": "e=(D_false-D_truth)-b",
            "extra_shortfall": "s=S_false-S_truth",
            "gain": "r*(b+e)-kappa*s",
            "strictly_profitable_iff": "r*(b+e)>kappa*s",
            "toy_conversion": "b=4,e=-2,s=1,so_kappa<2*r",
        },
        "cost_parameters": contract,
        "savings_ceiling_scope": "cached_spot_and_backlog_charges_from_backup_activation_only_not_global_benefit_bound",
        "new_mip_solves": 0, "new_physical_trajectories": 0,
        "network_rows": network_rows, "path_rows": run_rows, "path_date_rows": date_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.backup_nonuse")
    parser.add_argument("command", choices=("plan", "run", "verify"))
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--m3-manifest", type=Path, required=True)
    parser.add_argument("--m3-run", type=Path, required=True)
    parser.add_argument("--incentive-archive", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    plan, m3, cases, digest, prior = load_inputs(
        root, args.manifest.resolve(), args.m3_manifest.resolve(),
        args.m3_run.resolve(), args.incentive_archive.resolve())
    if args.command == "plan":
        print(json.dumps({"manifest_sha256": digest, "network_count": len(cases),
                          "path_count": plan["cached_truthful_major_cell"]["exact_cached_cell_count"],
                          "path_date_count": plan["expected_path_date_savings_ceiling_rows"],
                          "resource_caps": plan["resource_caps"]}, sort_keys=True))
        return
    if args.output is None or not args.output.resolve().is_relative_to(root):
        parser.error("backup diagnostic output must be inside the repository")
    output = args.output.resolve()
    if args.command == "run" and output.exists():
        parser.error("output exists; no overwrite or automatic retry")
    started = monotonic()
    signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, plan["resource_caps"]["wall_seconds"])
    try:
        checker = Run(args.m3_run.resolve(), m3, cases, plan["m3_manifest_sha256"],
                      resume=False, verify_only=True)
        checker.execute()
        result = build_result(plan, m3, cases, digest, prior,
                              args.m3_run.resolve(), started)
        if args.command == "verify":
            raw = output.read_bytes()
            saved = json.loads(raw)
            if canonical(saved) != raw or saved.get("resources") is None:
                raise ValueError("saved backup diagnostic is noncanonical or incomplete")
            result["resources"] = saved["resources"]
            if result != saved:
                raise ValueError("saved diagnostic differs from verified cache recomputation")
            print(json.dumps({"result_sha256": sha256(raw).hexdigest(),
                              "network_count": result["network_count"],
                              "certified_network_count": result["certified_network_count"]},
                             sort_keys=True))
            return
        result["resources"] = {"active_wall_seconds": monotonic() - started,
                               "peak_process_rss_mib": _rss_mib()}
        raw = canonical(result)
        if (len(raw) > plan["resource_caps"]["disk_mib"] * 1024 * 1024
                or monotonic() - started >= plan["resource_caps"]["wall_seconds"]
                or _rss_mib() > plan["resource_caps"]["process_rss_mib"]):
            raise RuntimeError("approved backup diagnostic resource cap reached before output")
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        if temporary.exists():
            raise ValueError("stale temporary output; no automatic retry")
        temporary.write_bytes(raw)
        temporary.replace(output)
        print(json.dumps({"result_sha256": sha256(raw).hexdigest(),
                          "network_count": result["network_count"],
                          "path_count": result["path_count"],
                          "path_date_count": result["path_date_count"],
                          "certified_network_count": result["certified_network_count"],
                          "certified_path_count": result["certified_path_count"],
                          "resources": result["resources"], "output_bytes": len(raw)}, sort_keys=True))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


if __name__ == "__main__":
    main()
