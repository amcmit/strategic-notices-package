"""Summarize the verified M4 screen without new trajectories or searches."""

from __future__ import annotations

import argparse
from collections import Counter
import gzip
from hashlib import sha256
import json
from pathlib import Path

from .m3 import load_approved
from .m3_report import _bootstrap
from .m4 import APPROVED_SHA256, TOLERANCE
from .suite import canonical


def summarize(result: dict, cases: tuple, m3_digest: str, result_sha256: str) -> dict:
    if (result["manifest_sha256"] != APPROVED_SHA256
            or result["m3_manifest_sha256"] != m3_digest
            or result["counts"]["profile_off_path_checks"] != len(result["rows"])):
        raise ValueError("M4 result provenance or row count mismatch")
    rows = result["rows"]
    aggregate = [row for row in rows if row["scope"] == "aggregate"]
    candidates = [row for row in aggregate if row["candidate"]]
    consequential = [row for row in candidates if row["false_notice_frequency"] > TOLERANCE
                     and row["false_notice_paired_harm"] > TOLERANCE]
    strict = [row for row in candidates if all(gain < -TOLERANCE
                                                for gain in row["deviation_gains"].values())]
    selected = [row for row in aggregate if row["margin"] == 3
                and row["shortfall_penalty"] == 5 and row["candidate"]]
    selected = [row for row in selected if row["profile"] in
                ("truthful_separating", "pool_minor", "pool_major")]
    by_id = {case.identifier: case.family for case in cases}
    network_harm = [
        {"case_id": row["game_id"], "family": by_id[row["game_id"]],
         "harm": row["false_notice_paired_harm"]}
        for row in rows if row["scope"] == "network" and row["margin"] == 3
        and row["shortfall_penalty"] == 5 and row["profile"] == "pool_major"
        and row["off_path_belief_severe"] == 1.0
    ]
    if len(network_harm) != len(cases):
        raise ValueError("one network-level pool-major row per design case required")
    bootstrap = _bootstrap(network_harm, ("harm",), int(m3_digest[:16], 16), 10000)["harm"]
    if abs(bootstrap["mean"] - next(row["false_notice_paired_harm"] for row in selected
                                    if row["profile"] == "pool_major")) > TOLERANCE:
        raise ValueError("aggregate and network bootstrap means differ")
    return {
        "schema": 1, "status": "design_only_pure_screen_not_M5_approval",
        "m4_result_sha256": result_sha256, "m4_manifest_sha256": APPROVED_SHA256,
        "m3_manifest_sha256": m3_digest,
        "counts": result["counts"], "candidate_profiles": result["candidate_profiles"],
        "consequential_candidate_profiles": result["consequential_candidate_profiles"],
        "aggregate_candidate_profiles": dict(Counter(row["profile"] for row in candidates)),
        "aggregate_strict_candidate_profiles": dict(Counter(row["profile"] for row in strict)),
        "aggregate_consequential_candidate_profiles": dict(Counter(
            row["profile"] for row in consequential)),
        "aggregate_consequential_all_weak_ties": all(
            all(abs(gain) <= TOLERANCE for gain in row["deviation_gains"].values())
            for row in consequential),
        "aggregate_consequential_off_path_beliefs": sorted(set(
            row["off_path_belief_severe"] for row in consequential)),
        "representative_margin3_penalty5_candidates": selected,
        "pool_major_false_notice_harm_bootstrap": bootstrap,
        "pool_major_harm_sign_counts": {
            "positive": sum(row["harm"] > TOLERANCE for row in network_harm),
            "negative": sum(row["harm"] < -TOLERANCE for row in network_harm),
            "tie": sum(abs(row["harm"]) <= TOLERANCE for row in network_harm)},
        "pool_major_network_harm": network_harm,
        "resources": result["resources"],
        "limitations": result["limitations"] +
        ["weak_ties_are_not_robust_incentives", "33_payoff_cells_revalue_one_physical_profile"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.m4_report")
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--m3-manifest", type=Path, required=True)
    parser.add_argument("--screen", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("report destination already exists")
    source = args.screen.read_bytes()
    raw = gzip.decompress(source) if args.screen.suffix == ".gz" else source
    result = json.loads(raw)
    if canonical(result) != raw:
        raise ValueError("M4 screen bytes are not canonical")
    m3, cases, digest = load_approved(args.m3_manifest.resolve(), args.repository_root.resolve())
    summary = summarize(result, cases, digest, sha256(raw).hexdigest())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(summary))
    print(json.dumps({"aggregate_candidate_profiles": summary["aggregate_candidate_profiles"],
                      "aggregate_consequential_candidate_profiles":
                      summary["aggregate_consequential_candidate_profiles"],
                      "pool_major_false_notice_harm_bootstrap":
                      summary["pool_major_false_notice_harm_bootstrap"]}, sort_keys=True))


if __name__ == "__main__":
    main()
