"""Rebuild M3 design-only tables and figures from verified cached trajectories."""

from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path

import numpy as np

from .m3 import Run, _digest, _key, _read_cached, load_approved, policy_signature, scientific_input, source_hashes
from .m3 import Task
from .networks import NetworkCase
from .suite import canonical


def _write(path: Path, value: object) -> None:
    path.write_bytes(canonical(value))


def _metric(record: dict) -> dict[str, float]:
    rollout = record["rollout"]
    ledgers = rollout["ledgers"]
    return {
        "cost": float(rollout["operating_cost"]),
        "primary_delivered": sum(row["primary_delivered"] for row in ledgers),
        "primary_shortfall": sum(row["primary_shortfall"] for row in ledgers),
        "backup_delivered": sum(row["backup_delivered"] for row in ledgers),
        "spot_delivered": sum(row["spot_delivered"] for row in ledgers),
        "backlog_charge": sum(row["backlog_charge"] for row in ledgers),
        "qualification_count": sum(bool(row["qualify"]) for row in rollout["actions"]),
    }


def _read_task(directory: Path, task: Task, case: NetworkCase, manifest: dict,
               manifest_hash: str, codes: dict[str, str]) -> dict:
    payload = scientific_input(task, case, manifest, manifest_hash, codes)
    record = _read_cached(directory / "trajectories" / f"{_key(payload)}.json", payload)
    if record is None:
        raise ValueError("verified M3 trajectory missing")
    return record


def _weighted(values: list[float], weights: list[float]) -> float:
    return float(sum(value * weight for value, weight in zip(values, weights)))


def _family_mean(rows: list[dict], field: str) -> float:
    by_family: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_family[row["family"]].append(row[field])
    return float(np.mean([np.mean(group) for group in by_family.values()]))


def _bootstrap(rows: list[dict], fields: tuple[str, ...], seed: int,
               repetitions: int = 10000) -> dict:
    rng = np.random.default_rng(seed)
    by_family: dict[str, np.ndarray] = {}
    for family in sorted({row["family"] for row in rows}):
        by_family[family] = np.array([[row[field] for field in fields]
                                      for row in rows if row["family"] == family], dtype=float)
    replicate = np.zeros((repetitions, len(fields)), dtype=float)
    for values in by_family.values():
        indices = rng.integers(0, len(values), size=(repetitions, len(values)))
        replicate += values[indices].mean(axis=1) / len(by_family)
    means = np.mean([values.mean(axis=0) for values in by_family.values()], axis=0)
    return {field: {"mean": float(means[i]),
                    "ci95": [float(x) for x in np.quantile(replicate[:, i], [0.025, 0.975])]}
            for i, field in enumerate(fields)}


def _plots(summary: dict, surface: list[dict], transitions: list[dict], output: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 4.6), constrained_layout=True)
    components = ("contractual", "belief", "interaction", "joint")
    colors = ("#28668e", "#e18c32", "#7b539e", "#3d7958")
    for i, actual in enumerate(("short", "long")):
        estimates = summary["harm_by_type"][actual]
        for j, component in enumerate(components):
            item = estimates[component]
            x = i * 5 + j
            ax.bar(x, item["mean"], color=colors[j], width=0.75)
            lo, hi = item["ci95"]
            ax.errorbar(x, item["mean"], yerr=[[item["mean"] - lo], [hi - item["mean"]]],
                        color="black", capsize=2, linewidth=0.8)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_xticks([0, 1, 2, 3, 5, 6, 7, 8],
                  [f"{actual}\n{component}" for actual in ("short", "long")
                   for component in components], rotation=25, ha="right")
    ax.set_ylabel("Operating-cost difference")
    ax.set_title("Paired notice and belief effects (design networks)")
    fig.savefig(output / "harm_decomposition.png", dpi=180)
    fig.savefig(output / "harm_decomposition.pdf")
    plt.close(fig)

    margins = sorted({row["margin"] for row in surface})
    penalties = sorted({row["shortfall_penalty"] for row in surface})
    values = {(row["margin"], row["shortfall_penalty"]): row["deviation_gain"]
              for row in surface if row["actual_type"] == "long"}
    matrix = np.array([[values[(margin, penalty)] for penalty in penalties]
                       for margin in margins])
    fig, ax = plt.subplots(figsize=(8, 3.8), constrained_layout=True)
    image = ax.imshow(matrix, aspect="auto", cmap="coolwarm")
    ax.set_xticks(range(len(penalties)), penalties)
    ax.set_yticks(range(len(margins)), margins)
    ax.set_xlabel("Shortfall penalty")
    ax.set_ylabel("Supplier margin")
    ax.set_title("Long-type minor-vs-major payoff gain under literal belief")
    fig.colorbar(image, ax=ax, label="Payoff difference")
    fig.savefig(output / "long_type_payoff_surface.png", dpi=180)
    fig.savefig(output / "long_type_payoff_surface.pdf")
    plt.close(fig)

    families = sorted({row["family"] for row in transitions})
    counts = [sum(row["changed_intervals"] > 0 for row in transitions
                  if row["family"] == family) for family in families]
    totals = [sum(row["family"] == family for row in transitions) for family in families]
    fig, ax = plt.subplots(figsize=(8, 3.5), constrained_layout=True)
    ax.bar(families, counts, color="#28668e")
    ax.set_ylim(0, max(totals) + 1)
    ax.set_ylabel("Groups with sampled policy changes")
    ax.set_title("Belief-grid policy changes by network family")
    for i, (count, total) in enumerate(zip(counts, totals)):
        ax.text(i, count + 0.2, f"{count}/{total}", ha="center", fontsize=8)
    fig.savefig(output / "policy_changes.png", dpi=180)
    fig.savefig(output / "policy_changes.pdf")
    plt.close(fig)


def build_report(repository_root: Path, manifest_path: Path, run_root: Path,
                 output: Path) -> dict:
    manifest, cases, digest = load_approved(manifest_path, repository_root)
    checker = Run(run_root, manifest, cases, digest, resume=False, verify_only=True)
    checker.execute()
    if output.exists():
        raise FileExistsError("report destination already exists; choose a new directory")
    output.mkdir(parents=True)
    codes = source_hashes()
    weights = [float(weight) for weight in manifest["demand_scenario_weights"]]
    multipliers = [float(value) for value in manifest["realized_demand_multipliers"]]
    beliefs = [float(value) for value in manifest["belief_grid_severe"]]
    metrics: dict[tuple, dict] = {}
    for case in cases:
        for actual in manifest["actual_types"]:
            for notice in manifest["notice_schedules"]:
                for multiplier in multipliers:
                    for belief in beliefs:
                        task = Task(case.identifier, actual, notice, multiplier, "literal", belief)
                        metrics[(case.identifier, actual, notice, multiplier, belief)] = _metric(
                            _read_task(run_root, task, case, manifest, digest, codes))

    harm_rows = []
    for case in cases:
        for actual in manifest["actual_types"]:
            correct = 0.0 if actual == "short" else 1.0
            misleading = 1.0 - correct
            truthful = "minor" if actual == "short" else "major"
            false = "major" if actual == "short" else "minor"
            cells = {}
            for name, notice, belief in (("tt", truthful, correct),
                                         ("fc", false, correct),
                                         ("tw", truthful, misleading),
                                         ("ff", false, misleading)):
                cells[name] = _weighted(
                    [metrics[(case.identifier, actual, notice, multiplier, belief)]["cost"]
                     for multiplier in multipliers], weights)
            contractual = cells["fc"] - cells["tt"]
            belief_effect = cells["tw"] - cells["tt"]
            interaction = cells["ff"] - cells["fc"] - cells["tw"] + cells["tt"]
            harm_rows.append({"case_id": case.identifier, "family": case.family,
                              "size": case.size, "actual_type": actual, **cells,
                              "contractual": contractual, "belief": belief_effect,
                              "interaction": interaction, "joint": cells["ff"] - cells["tt"]})
    for row in harm_rows:
        if abs(row["contractual"] + row["belief"] + row["interaction"] - row["joint"]) > 1e-7:
            raise ValueError("four-cell harm identity failed")

    seed = int(digest[:16], 16)
    fields = ("contractual", "belief", "interaction", "joint")
    harm_by_type = {actual: _bootstrap([row for row in harm_rows if row["actual_type"] == actual],
                                       fields, seed + index)
                    for index, actual in enumerate(manifest["actual_types"])}
    for actual, results in harm_by_type.items():
        if abs(sum(results[field]["mean"] for field in fields[:-1]) - results["joint"]["mean"]) > 1e-7:
            raise ValueError(f"aggregated harm identity failed for {actual}")

    # Supplier payoffs are revalued from fixed ledgers. This particular
    # deviation comparison uses literal notice beliefs, not equilibrium beliefs.
    full_surface = []
    deviation_surface = []
    deviation_network_rows = []
    for actual in manifest["actual_types"]:
        truthful = "minor" if actual == "short" else "major"
        false = "major" if actual == "short" else "minor"
        for margin in manifest["payoff_revaluation_only"]["supplier_margin_grid"]:
            for penalty in manifest["payoff_revaluation_only"]["shortfall_penalty_grid"]:
                payoff_by_cell = {}
                for notice in (truthful, false):
                    for belief in beliefs:
                        rows = []
                        for case in cases:
                            delivered = _weighted(
                                [metrics[(case.identifier, actual, notice, multiplier, belief)]["primary_delivered"]
                                 for multiplier in multipliers], weights)
                            shortfall = _weighted(
                                [metrics[(case.identifier, actual, notice, multiplier, belief)]["primary_shortfall"]
                                 for multiplier in multipliers], weights)
                            rows.append({"family": case.family,
                                         "payoff": margin * delivered - penalty * shortfall})
                        payoff = _family_mean(rows, "payoff")
                        payoff_by_cell[(notice, belief)] = payoff
                        full_surface.append({"actual_type": actual, "notice_name": notice,
                                             "severe_belief": belief, "margin": margin,
                                             "shortfall_penalty": penalty, "payoff": payoff})
                truth_belief = 0.0 if truthful == "minor" else 1.0
                false_belief = 0.0 if false == "minor" else 1.0
                truth_payoff = payoff_by_cell[(truthful, truth_belief)]
                false_payoff = payoff_by_cell[(false, false_belief)]
                deviation_surface.append({"actual_type": actual, "margin": margin,
                                          "shortfall_penalty": penalty,
                                          "truthful_payoff": truth_payoff,
                                          "false_payoff": false_payoff,
                                          "deviation_gain": false_payoff - truth_payoff})
                for case in cases:
                    network_payoffs = {}
                    for notice, belief in ((truthful, truth_belief), (false, false_belief)):
                        delivered = _weighted(
                            [metrics[(case.identifier, actual, notice, multiplier, belief)]["primary_delivered"]
                             for multiplier in multipliers], weights)
                        shortfall = _weighted(
                            [metrics[(case.identifier, actual, notice, multiplier, belief)]["primary_shortfall"]
                             for multiplier in multipliers], weights)
                        network_payoffs[notice] = margin * delivered - penalty * shortfall
                    deviation_network_rows.append({
                        "case_id": case.identifier, "family": case.family,
                        "actual_type": actual, "margin": margin,
                        "shortfall_penalty": penalty,
                        "truthful_payoff": network_payoffs[truthful],
                        "false_payoff": network_payoffs[false],
                        "deviation_gain": network_payoffs[false] - network_payoffs[truthful],
                    })
    for row in deviation_surface:
        matched = [item for item in deviation_network_rows
                   if item["actual_type"] == row["actual_type"]
                   and item["margin"] == row["margin"]
                   and item["shortfall_penalty"] == row["shortfall_penalty"]]
        inference = _bootstrap(matched, ("deviation_gain",), seed)["deviation_gain"]
        if abs(inference["mean"] - row["deviation_gain"]) > 1e-7:
            raise ValueError("deviation mean and network bootstrap disagree")
        row["ci95"] = inference["ci95"]

    # No interval is certified unchanged: all reported transitions are sampled
    # brackets, including the adaptively refined ones.
    transition_groups: dict[tuple, dict[float, tuple]] = defaultdict(dict)
    certificate_audit = {
        phase: {"count": 0, "nonoptimal_count": 0,
                "max_relative_gap": 0.0, "max_primal_dual_difference": 0.0,
                "relative_gap_over_one_percent_count": 0,
                "relative_gap_over_one_percent_with_active_action_count": 0,
                "max_relative_gap_with_active_action": 0.0}
        for phase in ("primary", "tie_break")
    }
    for path in (run_root / "trajectories").glob("*.json"):
        record = json.loads(path.read_bytes())
        payload = record["input"]
        for action, pair in zip(record["rollout"]["actions"], record["rollout"]["certificates"]):
            active = bool(action["qualify"] or action["primary_order"] > 1e-7
                          or action["spot_order"] > 1e-7 or action["production"]
                          or action["shipments"])
            for certificate in pair:
                audit = certificate_audit[certificate["phase"]]
                audit["count"] += 1
                audit["nonoptimal_count"] += int(not certificate["optimal"] or certificate["status"] != 0)
                gap = certificate["relative_gap"] or 0.0
                audit["max_relative_gap"] = max(audit["max_relative_gap"], gap)
                audit["max_primal_dual_difference"] = max(
                    audit["max_primal_dual_difference"],
                    certificate["primal"] - certificate["dual"])
                if gap > 0.01:
                    audit["relative_gap_over_one_percent_count"] += 1
                    audit["relative_gap_over_one_percent_with_active_action_count"] += int(active)
                if active:
                    audit["max_relative_gap_with_active_action"] = max(
                        audit["max_relative_gap_with_active_action"], gap)
        if payload["benchmark"] != "literal":
            continue
        group = (payload["case_id"], payload["actual_type"], payload["notice_name"],
                 payload["realized_demand_multiplier"])
        transition_groups[group][payload["severe_belief"]] = policy_signature(record)
    transitions = []
    case_by_id = {case.identifier: case for case in cases}
    for group, samples in sorted(transition_groups.items()):
        ordered = sorted(samples)
        brackets = [{"left": a, "right": b, "width": b - a}
                    for a, b in zip(ordered, ordered[1:]) if samples[a] != samples[b]]
        case_id, actual, notice, multiplier = group
        transitions.append({"case_id": case_id, "family": case_by_id[case_id].family,
                            "actual_type": actual, "notice_name": notice,
                            "demand_multiplier": multiplier, "sampled_beliefs": ordered,
                            "policy_classes_observed": len(set(samples.values())),
                            "changed_intervals": len(brackets),
                            "unresolved_transition_brackets": brackets,
                            "equal_endpoint_intervals_not_certified": len(ordered) - 1 - len(brackets)})

    clair_rows = []
    information_rows = []
    for case in cases:
        for actual in manifest["actual_types"]:
            for notice in manifest["notice_schedules"]:
                correct = 0.0 if actual == "short" else 1.0
                gaps = []
                for multiplier in multipliers:
                    task = Task(case.identifier, actual, notice, multiplier, "clairvoyant", None)
                    clair = _metric(_read_task(run_root, task, case, manifest, digest, codes))["cost"]
                    full = metrics[(case.identifier, actual, notice, multiplier, correct)]["cost"]
                    gaps.append(full - clair)
                clair_rows.append({"case_id": case.identifier, "family": case.family,
                                   "actual_type": actual, "notice_name": notice,
                                   "full_minus_clairvoyant": _weighted(gaps, weights)})
                prior_minus_full = _weighted([
                    metrics[(case.identifier, actual, notice, multiplier, 0.5)]["cost"]
                    - metrics[(case.identifier, actual, notice, multiplier, correct)]["cost"]
                    for multiplier in multipliers], weights)
                information_rows.append({"case_id": case.identifier, "family": case.family,
                                         "actual_type": actual, "notice_name": notice,
                                         "prior_minus_full": prior_minus_full})

    summary = {
        "schema": 1, "status": "design_only", "manifest_sha256": digest,
        "trajectory_tree_sha256": checker.state["trajectory_tree_sha256"],
        "analysis_source_sha256": _digest(Path(__file__)),
        "network_count": len(cases), "trajectory_count": checker.state["completed"],
        "resources": {key: checker.state[key] for key in
                      ("active_wall_seconds", "trajectory_bytes", "peak_process_rss_mib")},
        "solver_certificate_audit": certificate_audit,
        "bootstrap": {"repetitions": 10000, "seed": seed,
                       "unit": "network", "strata": "family",
                       "scenario_handling": "paired weighted mean before network bootstrap",
                       "interval": "percentile 95 percent"},
        "harm_by_type": harm_by_type,
        "policy_transition_groups": len(transitions),
        "policy_change_groups": sum(row["changed_intervals"] > 0 for row in transitions),
        "unresolved_transition_brackets": sum(row["changed_intervals"] for row in transitions),
        "max_observed_policy_classes": max(row["policy_classes_observed"] for row in transitions),
        "full_minus_clairvoyant_by_type": {
            actual: _family_mean([row for row in clair_rows if row["actual_type"] == actual],
                                 "full_minus_clairvoyant")
            for actual in manifest["actual_types"]},
        "prior_minus_full_by_type": {
            actual: _family_mean([row for row in information_rows if row["actual_type"] == actual],
                                 "prior_minus_full")
            for actual in manifest["actual_types"]},
        "literal_deviation_sign_counts": [
            {"actual_type": actual, "margin": margin, "shortfall_penalty": penalty,
             "positive": sum(row["deviation_gain"] > 1e-7 for row in deviation_network_rows
                             if row["actual_type"] == actual and row["margin"] == margin
                             and row["shortfall_penalty"] == penalty),
             "zero": sum(abs(row["deviation_gain"]) <= 1e-7 for row in deviation_network_rows
                         if row["actual_type"] == actual and row["margin"] == margin
                         and row["shortfall_penalty"] == penalty),
             "negative": sum(row["deviation_gain"] < -1e-7 for row in deviation_network_rows
                             if row["actual_type"] == actual and row["margin"] == margin
                             and row["shortfall_penalty"] == penalty)}
            for actual in manifest["actual_types"]
            for margin in manifest["payoff_revaluation_only"]["supplier_margin_grid"]
            for penalty in manifest["payoff_revaluation_only"]["shortfall_penalty_grid"]
        ],
    }
    _write(output / "summary.json", summary)
    _write(output / "harm_network_rows.json", harm_rows)
    _write(output / "payoff_surface.json", full_surface)
    _write(output / "literal_deviation_surface.json", deviation_surface)
    _write(output / "literal_deviation_network_rows.json", deviation_network_rows)
    _write(output / "policy_transitions.json", transitions)
    _write(output / "clairvoyant_network_rows.json", clair_rows)
    _write(output / "information_value_network_rows.json", information_rows)
    _plots(summary, deviation_surface, transitions, output)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.m3_report")
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = build_report(args.repository_root.resolve(), args.manifest.resolve(),
                           args.run.resolve(), args.output.resolve())
    print(json.dumps({key: summary[key] for key in ("trajectory_count", "harm_by_type",
                                                     "policy_change_groups")}, sort_keys=True))


if __name__ == "__main__":
    main()
