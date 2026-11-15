"""Rebuild exploratory incentive tables from the archived verified result."""

from __future__ import annotations

import argparse
import gzip
from hashlib import sha256
import json
from pathlib import Path

from .incentive_diagnostic import APPROVED_SHA256
from .suite import canonical

RESULT_SHA256 = "9838d40a968c69c1c6566feec96c0413a245c2e086e2fe0c82bc15c76cc56975"
TOLERANCE = 1e-7


def _sign(value: float) -> str:
    return "positive" if value > TOLERANCE else "negative" if value < -TOLERANCE else "tie"


def _format(value: float | None) -> str:
    if value is None:
        return "—"
    if abs(value) <= TOLERANCE:
        return "0"
    return f"{value:.4f}"


def build(raw: bytes) -> tuple[dict, str]:
    if sha256(raw).hexdigest() != RESULT_SHA256:
        raise ValueError("exploratory result differs from verified raw SHA-256")
    result = json.loads(raw)
    if canonical(result) != raw or result["manifest_sha256"] != APPROVED_SHA256:
        raise ValueError("result bytes or approved manifest provenance mismatch")
    rows = result["network_rows"]
    scenarios = result["scenario_rows"]
    if len(rows) != 24 or len(scenarios) != 72:
        raise ValueError("unexpected network or scenario count")
    gains = {penalty: {
        category: sum(_sign(row["margin_results"]["3"]["critical_penalty"]
                                 [f"gain_at_penalty_{penalty}"]) == category for row in rows)
        for category in ("positive", "negative", "tie")}
        for penalty in (0, 5)}
    summary = {
        "schema": 1, "status": "exploratory_outside_approved_grid",
        "result_sha256": RESULT_SHA256, "manifest_sha256": APPROVED_SHA256,
        "m3_trajectory_tree_sha256": result["m3_trajectory_tree_sha256"],
        "network_count": len(rows), "scenario_count": len(scenarios),
        "threshold_classification_counts": result["threshold_classification_counts"],
        "gain_sign_counts_margin3": gains,
        "order_change_network_counts": result["order_change_network_counts"],
        "backup_difference_nonzero_network_count": sum(
            abs(row["delta"]["backup_delivered"]) > TOLERANCE for row in rows),
        "backup_used_truthful_scenario_count": sum(
            row["joint"]["truthful_totals"]["backup_delivered"] > TOLERANCE
            for row in scenarios),
        "backup_used_false_scenario_count": sum(
            row["joint"]["under_report_totals"]["backup_delivered"] > TOLERANCE
            for row in scenarios),
        "fixed_belief_physical_deltas_all_zero": all(
            all(abs(value) <= TOLERANCE for value in row["fixed_belief"]["delta"].values())
            for row in scenarios),
        "equal_family_delta": result["equal_family_delta"],
        "equal_family_gain_margin3_penalty0": result["equal_family_gain_margin3_penalty0"],
        "equal_family_gain_margin3_penalty5": result["equal_family_gain_margin3_penalty5"],
        "toy_validation": result["toy_validation"], "resources": result["resources"],
    }
    lines = [
        "# Per-network exploratory incentive diagnostic",
        "",
        "Long type: false minor/belief 0 minus truthful major/belief 1. Demand scenarios are weighted within network.",
        "Critical penalties use margin 3. `—` means no nonnegative critical penalty; a negative algebraic root is shown only as a diagnostic.",
        "Order columns count a difference in at least one paired demand scenario. `J` is the joint notice-and-belief comparison; `F` holds belief at 1.",
        "",
        "| Network | Δ primary delivered | Δ shortfall | Δ backup | Root class | Critical κ≥0 | Algebraic root | Gain κ=0 | Gain κ=5 | J primary order | J spot order | J accepted | F any order |",
        "|---|---:|---:|---:|---|---:|---:|---:|---:|:---:|:---:|:---:|:---:|",
    ]
    for row in rows:
        delta = row["delta"]
        threshold = row["margin_results"]["3"]["critical_penalty"]
        joint = row["order_change_flags"]["joint"]
        fixed = row["order_change_flags"]["fixed_belief"]
        yes = lambda field, flags: "Y" if flags[field]["any_scenario"] else ""
        lines.append("| " + " | ".join((
            row["case_id"], _format(delta["primary_delivered"]),
            _format(delta["primary_shortfall"]), _format(delta["backup_delivered"]),
            threshold["classification"], _format(threshold["critical_nonnegative_penalty"]),
            _format(threshold["algebraic_root"]),
            _format(threshold["gain_at_penalty_0"]),
            _format(threshold["gain_at_penalty_5"]),
            yes("primary_order", joint), yes("spot_order", joint),
            yes("accepted_obligation", joint), yes("either_order", fixed))) + " |")
    lines.append("")
    return summary, "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.incentive_report")
    parser.add_argument("--screen", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary_path = args.output / "summary.json"
    table_path = args.output / "network_table.md"
    if summary_path.exists() or table_path.exists():
        parser.error("report files already exist; no overwrite")
    packed = args.screen.read_bytes()
    raw = gzip.decompress(packed) if args.screen.suffix == ".gz" else packed
    summary, table = build(raw)
    args.output.mkdir(parents=True, exist_ok=True)
    summary_path.write_bytes(canonical(summary))
    table_path.write_text(table, encoding="utf-8")
    print(json.dumps({"summary_sha256": sha256(summary_path.read_bytes()).hexdigest(),
                      "table_sha256": sha256(table_path.read_bytes()).hexdigest()}, sort_keys=True))


if __name__ == "__main__":
    main()
