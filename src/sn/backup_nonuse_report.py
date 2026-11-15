"""Rebuild the exploratory backup-nonuse packet from verified cached evidence."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import gzip
from hashlib import sha256
from io import BytesIO, StringIO
import json
from pathlib import Path

from .backup_nonuse import APPROVED_SHA256
from .suite import canonical

RESULT_SHA256 = "03818619eb178c4f741e565e57be90a6a2bc682b5ee1dbbca79a929565390c4f"
TOLERANCE = 1e-7


def _fmt(value: float) -> str:
    return f"{value:.2f}"


def _sign(value: float) -> str:
    if value > TOLERANCE:
        return "positive"
    if value < -TOLERANCE:
        return "negative"
    return "zero"


def build(raw: bytes) -> tuple[dict, str, str, bytes]:
    if sha256(raw).hexdigest() != RESULT_SHA256:
        raise ValueError("backup diagnostic result differs from independently verified SHA-256")
    result = json.loads(raw)
    if (canonical(result) != raw or result["manifest_sha256"] != APPROVED_SHA256
            or result["new_mip_solves"] or result["new_physical_trajectories"]):
        raise ValueError("backup result is noncanonical or outside approved scope")
    networks, paths, dates = (result[key] for key in
                              ("network_rows", "path_rows", "path_date_rows"))
    if (len(networks), len(paths), len(dates)) != (24, 72, 576):
        raise ValueError("backup result count mismatch")
    network_ids = [row["case_id"] for row in networks]
    if len(set(network_ids)) != 24:
        raise ValueError("duplicate network row")
    path_by_key = {(row["case_id"], row["demand_multiplier"]): row for row in paths}
    date_by_key = {(row["case_id"], row["demand_multiplier"], row["qualification_date"]): row
                   for row in dates}
    if len(path_by_key) != 72 or len(date_by_key) != 576:
        raise ValueError("duplicate path or date row")
    for case_id in network_ids:
        for multiplier in (0.8, 1, 1.2):
            path = path_by_key[case_id, multiplier]
            if len(path["outage_demand_overlap_periods"]) != 2:
                raise ValueError("design outage/demand overlap changed")
            for q in range(8):
                date = date_by_key[case_id, multiplier, q]
                if date["mandatory_payment"] != 2 + 2 * (8 - q):
                    raise ValueError("mandatory payment arithmetic changed")
                if date["line_item_ceiling_is_global_benefit_bound"]:
                    raise ValueError("line-item ceiling mislabeled as global bound")
    certified = Counter(row["substitutability"] for row in networks
                        if row["causal_binding_reason"] == "certified_for_all_three_paths")
    unresolved = [row["case_id"] for row in networks
                  if row["causal_binding_reason"] != "certified_for_all_three_paths"]
    reasons = Counter(reason for row in dates for reason in row["certified_date_exclusion_reasons"])
    reason_sets = Counter("+".join(row["certified_date_exclusion_reasons"]) or "unresolved"
                          for row in dates)
    summary = {
        "schema": 1, "status": "exploratory_cached_design_backup_nonuse",
        "result_sha256": RESULT_SHA256, "manifest_sha256": APPROVED_SHA256,
        "m3_trajectory_tree_sha256": result["m3_trajectory_tree_sha256"],
        "network_count": 24, "path_count": 72, "path_date_count": 576,
        "substitutability_counts": result["substitutability_counts"],
        "certified_network_count": result["certified_network_count"],
        "certified_network_counts_by_substitutability": dict(certified),
        "certified_path_count": result["certified_path_count"],
        "unresolved_network_ids": unresolved,
        "unresolved_path_keys": [[row["case_id"], row["demand_multiplier"]]
                                 for row in paths if not row["all_dates_certified_excluded"]],
        "date_reason_occurrences": dict(reasons),
        "date_reason_sets": dict(reason_sets),
        "line_item_ceiling_minus_mandatory_sign_counts": dict(Counter(
            _sign(row["ceiling_minus_mandatory_payment"]) for row in dates)),
        "q0_line_item_ceiling_minus_mandatory_sign_counts": dict(Counter(
            _sign(row["ceiling_minus_mandatory_payment"]) for row in dates
            if row["qualification_date"] == 0)),
        "observed_path_pattern_counts": dict(Counter(row["observed_pattern"] for row in paths)),
        "toy_comparison_row": result["toy_comparison_row"],
        "supplier_incentive_condition": result["supplier_incentive_condition"],
        "resources": result["resources"],
        "savings_ceiling_scope": result["savings_ceiling_scope"],
    }
    network_lines = [
        "# Exploratory backup nonuse by design network", "",
        "All rows use backup unit cost 2, qualification lead 2, minimum 1 per active period, fee 2, spot unit cost 6 and capacity 2, backlog charge 20 per unit-period, long outage length 3, and two demand periods overlapping outage. The q0 mandatory payment is 18.",
        "`U−M` is the cached post-arrival spot-plus-backlog line-item ceiling minus the mandatory backup payment; it is not a global benefit bound or a counterfactual. Values are listed for demand multipliers 0.8 / 1 / 1.2. `Certified paths` counts demand paths for which every qualification date is excluded by a structural or independent cost certificate.",
        "", "| Network | Substitute | Topological lead to demand | Initial main buffer | Alternate nominal cap | q0 U−M (0.8 / 1 / 1.2) | Certified paths | Causal reason |",
        "|---|---|---|---:|---:|---|---:|---|",
    ]
    for row in networks:
        case_id = row["case_id"]
        lead = ", ".join(f"{key}:{value}" for key, value in row["topological_leads"].items())
        first_path = path_by_key[case_id, 1]
        main_buffer = max(x["initial_buffer_units"] for x in first_path["buffer_cover"])
        alt_cap = sum(x["nominal_outbound_capacity_per_period"]
                      for x in first_path["alternative_sources"])
        q0 = " / ".join(_fmt(date_by_key[case_id, multiplier, 0]
                              ["ceiling_minus_mandatory_payment"])
                        for multiplier in (0.8, 1, 1.2))
        reason = "all dates excluded" if row["paths_all_dates_certified_excluded"] == 3 else "unresolved on ≥1 path"
        network_lines.append("| " + " | ".join((case_id, row["substitutability"], lead,
            _fmt(main_buffer), _fmt(alt_cap), q0,
            str(row["paths_all_dates_certified_excluded"]), reason)) + " |")
    network_lines.append("")
    path_lines = [
        "# Exploratory backup nonuse by cached demand path", "",
        "The first-demand buffer cover is shown as units / periods at each demand site and product. Alternate shipments are observed cached shipments. The q0 ceiling is restricted to cached spot and backlog line items from backup arrival at the primary site; it is not a counterfactual. All three scenarios have a three-period outage and two overlapping demand periods.",
        "", "| Network | Substitute | Demand multiplier | First-demand buffer cover | Alternate shipped | Spot units / cost | Backlog unit-periods / charge | q0 U / M / U−M | Certified dates of 8 | Causal reason |",
        "|---|---|---:|---|---:|---|---|---|---:|---|",
    ]
    for row in paths:
        case_id, multiplier = row["case_id"], row["demand_multiplier"]
        cover = ", ".join(f"{x['site']}/{x['product']}:{_fmt(x['opening_stock_at_first_demand'])}/{_fmt(x['opening_cover_periods'])}"
                          for x in row["buffer_cover"])
        alternate_shipped = sum(x["observed_shipped_units"] for x in row["alternative_sources"])
        q0 = date_by_key[case_id, multiplier, 0]
        q0_values = " / ".join(_fmt(q0[key]) for key in
                               ("cached_line_item_savings_ceiling", "mandatory_payment",
                                "ceiling_minus_mandatory_payment"))
        reason = "all dates excluded" if row["all_dates_certified_excluded"] else "unresolved"
        path_lines.append("| " + " | ".join((case_id, row["substitutability"],
            _fmt(multiplier), cover, _fmt(alternate_shipped),
            f"{_fmt(row['observed_spot_units'])} / {_fmt(row['observed_spot_cost'])}",
            f"{_fmt(row['observed_backlog_unit_periods'])} / {_fmt(row['observed_backlog_charge'])}",
            q0_values, str(row["certified_excluded_date_count"]), reason)) + " |")
    path_lines.append("")
    columns = (
        "case_id", "family", "substitutability", "demand_multiplier", "qualification_date",
        "first_backup_delivery_at_primary_site", "mandatory_paid_periods", "mandatory_payment",
        "cached_post_arrival_spot_cost", "cached_post_arrival_backlog_charge",
        "cached_line_item_savings_ceiling", "ceiling_minus_mandatory_payment",
        "primary_solve_primal_at_qualification_date", "earliest_topological_demand_arrival",
        "potentially_useful_demand_periods", "potentially_useful_demand_period_count",
        "paid_minus_potentially_useful_periods", "certified_date_exclusion_reasons",
        "line_item_ceiling_is_global_benefit_bound",
    )
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in dates:
        writer.writerow({key: json.dumps(row[key], sort_keys=True, separators=(",", ":"))
                         if isinstance(row[key], (dict, list)) else row[key]
                         for key in columns})
    return summary, "\n".join(network_lines), "\n".join(path_lines), stream.getvalue().encode()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.backup_nonuse_report")
    parser.add_argument("--screen", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    screen = args.screen.read_bytes()
    raw = gzip.decompress(screen) if args.screen.suffix == ".gz" else screen
    summary, network_table, path_table, date_csv = build(raw)
    files = {
        "screen.json.gz": None,
        "summary.json": canonical(summary),
        "network_table.md": network_table.encode(),
        "path_table.md": path_table.encode(),
        "path_date_ceilings.csv": date_csv,
    }
    packed = BytesIO()
    with gzip.GzipFile(filename="", mode="wb", compresslevel=9, mtime=0, fileobj=packed) as target:
        target.write(raw)
    files["screen.json.gz"] = packed.getvalue()
    if args.output.exists() and any((args.output / name).exists() for name in files):
        parser.error("report files already exist; no overwrite")
    args.output.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (args.output / name).write_bytes(data)
    print(json.dumps({name: sha256(data).hexdigest() for name, data in files.items()},
                     sort_keys=True))


if __name__ == "__main__":
    main()
