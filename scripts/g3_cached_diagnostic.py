"""Read-only G3 incentive and family-sensitivity check from the frozen cache."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from statistics import mean

from sn.m3 import Run, Task, load_approved, source_hashes
from sn.m3_report import _metric, _read_task
from sn.suite import canonical


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("diagnostic output already exists")
    root = args.repository_root.resolve()
    manifest, cases, digest = load_approved(args.manifest.resolve(), root)
    checker = Run(args.run.resolve(), manifest, cases, digest, resume=False, verify_only=True)
    checker.execute()
    summary = json.loads(args.summary.read_bytes())
    if (summary["trajectory_tree_sha256"] != checker.state["trajectory_tree_sha256"]
            or summary["manifest_sha256"] != digest):
        raise ValueError("M3 report and verified trajectory cache differ")
    codes = source_hashes()
    multipliers = [float(x) for x in manifest["realized_demand_multipliers"]]
    weights = [float(x) for x in manifest["demand_scenario_weights"]]
    rows = []
    for case in cases:
        cells = {}
        for notice, belief in (("major", 1.0), ("minor", 0.0)):
            records = [_read_task(args.run.resolve(),
                                  Task(case.identifier, "long", notice, multiplier,
                                       "literal", belief), case, manifest, digest, codes)
                       for multiplier in multipliers]
            metrics = [_metric(record) for record in records]
            cells[notice] = {
                name: sum(weight * item[name] for weight, item in zip(weights, metrics))
                for name in ("cost", "primary_delivered", "primary_shortfall")
            }
        row = {
            "case_id": case.identifier, "family": case.family,
            "cost_harm": cells["minor"]["cost"] - cells["major"]["cost"],
            "delivered_difference": (cells["minor"]["primary_delivered"]
                                     - cells["major"]["primary_delivered"]),
            "shortfall_difference": (cells["minor"]["primary_shortfall"]
                                     - cells["major"]["primary_shortfall"]),
        }
        row["supplier_gain_margin3_penalty5"] = (
            3 * row["delivered_difference"] - 5 * row["shortfall_difference"])
        rows.append(row)

    families = sorted({row["family"] for row in rows})
    fields = ("cost_harm", "delivered_difference", "shortfall_difference",
              "supplier_gain_margin3_penalty5")
    family_means = {
        family: {field: mean(row[field] for row in rows if row["family"] == family)
                 for field in fields}
        for family in families
    }
    overall = {field: mean(family_means[family][field] for family in families)
               for field in fields}
    if abs(overall["cost_harm"] - summary["harm_by_type"]["long"]["joint"]["mean"]) > 1e-7:
        raise ValueError("joint harm does not agree with frozen G3 summary")
    leave_one_family_out = {
        omitted: {field: mean(family_means[family][field]
                              for family in families if family != omitted)
                  for field in ("cost_harm", "supplier_gain_margin3_penalty5")}
        for omitted in families
    }
    result = {
        "schema": 1, "status": "post_G3_question_read_only_diagnostic",
        "manifest_sha256": digest,
        "trajectory_tree_sha256": checker.state["trajectory_tree_sha256"],
        "script_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "margin": 3, "shortfall_penalty": 5,
        "network_count": len(rows), "overall_equal_family_mean": overall,
        "family_means": family_means,
        "leave_one_family_out": leave_one_family_out,
        "positive_harm_cases": [row for row in rows if row["cost_harm"] > 1e-7],
        "positive_supplier_gain_cases": [row for row in rows
                                         if row["supplier_gain_margin3_penalty5"] > 1e-7],
        "network_rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))
    print(json.dumps({"overall": overall,
                      "positive_harm_cases": len(result["positive_harm_cases"]),
                      "positive_supplier_gain_cases": len(result["positive_supplier_gain_cases"])},
                     sort_keys=True))


if __name__ == "__main__":
    main()
