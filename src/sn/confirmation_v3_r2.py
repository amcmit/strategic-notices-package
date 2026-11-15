"""Revised owner-only structural holdout seal; public-seed fixtures are in-memory."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import re

from .allocation_v3 import canonical
from .confirmation_v3 import (
    FACTOR_KEYS, PUBLIC_METADATA, _build_private_case,
    _expected_header as _old_header, _private_seed, _public_row,
    _selected_rows, validate_confirmation_metadata_value,
)
from .confirmation_structure_v3 import (
    LANE_LEADS, ORDINARY_CAPACITIES, READY_CAPACITIES,
    design_structure_digests, draw_novel_case, structure_digest,
)
from .freeze_v3_design import OUTPUT as FREEZE_PATH


R5_PROTOCOL = "docs/V3_PROTOCOL_R5_CONFIRMATION_POPULATION.md"
SOURCE_FILES = (
    "src/sn/allocation_v3.py", "src/sn/core.py", "src/sn/core_v3.py",
    "src/sn/networks_v3.py", "src/sn/freeze_v3_design.py",
    "src/sn/confirmation_v3.py", "src/sn/confirmation_structure_v3.py",
    "src/sn/confirmation_v3_r2.py",
)


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _header(root: Path, rows: list[dict]) -> dict:
    return {
        "schema": 2, "kind": "v3_structural_confirmation_public_metadata",
        "status": "owner_sealed_private_cases_not_opened_by_agent",
        "selected_candidate": 0,
        "selected_design_freeze_sha256": _digest(root / FREEZE_PATH),
        "r5_population_protocol_sha256": _digest(root / R5_PROTOCOL),
        "public_design_manifest_sha256": _digest(root / "configs/v3-design/manifest.json"),
        "source_sha256": {name: _digest(root / name) for name in SOURCE_FILES},
        "count": 56,
        "regime_counts": dict(sorted(Counter(x["regime"] for x in rows).items())),
        "category_counts": dict(sorted(Counter(x["category"] for x in rows).items())),
        "family_counts": dict(sorted(Counter(x["family"] for x in rows).items())),
        "size_counts": dict(sorted(Counter(x["size"] for x in rows).items())),
        "private_seed_minimum_bits": 128,
        "maximum_draws_per_slot": 64,
        "private_seed_disclosed": False, "private_root_disclosed": False,
    }


def _summary(case) -> dict:
    lanes = case.network.lanes
    demand = case.baseline_demand[0]
    return {
        "site_count": len(case.network.sites),
        "lane_count": len(lanes),
        "intermediate_count": sum(name.startswith((
            "hub", "bypass", "crossdock", "transit_extra"))
            for name in case.network.sites),
        "outlet_count": sum(item.site != "buyer" for item in demand),
        "buyer_demand": next((item.quantity for item in demand
                              if item.site == "buyer"), 0.0),
        "maximum_lane_lead": max((lane.lead for lane in lanes), default=0),
        "minimum_lane_capacity": min((lane.capacity for lane in lanes), default=None),
        "maximum_lane_capacity": max((lane.capacity for lane in lanes), default=None),
    }


def draw_fixture_set(root: Path, public_master_seed: int) -> list[tuple[dict, object, int]]:
    """In-memory development fixtures only; never writes confirmation files."""
    if type(public_master_seed) is not int or public_master_seed < 0:
        raise ValueError("public fixture seed must be a nonnegative integer")
    rows = _selected_rows(root)
    design = design_structure_digests(root)
    accepted: set[str] = set()
    drawn = []
    for row in rows:
        base = _build_private_case(row, public_master_seed)
        case, attempts = draw_novel_case(base, row, public_master_seed,
                                         design, accepted)
        drawn.append((row, case, attempts))
    return drawn


def _public_case_row(row: dict, case, attempts: int) -> dict:
    return {**_public_row(row),
            "structure_sha256": structure_digest(case),
            "structure_summary": _summary(case),
            "draw_attempts": attempts,
            "content_sha256": sha256(canonical(asdict(case))).hexdigest()}


def _validate_summary(item: dict) -> None:
    summary = item["structure_summary"]
    if (not isinstance(summary, dict) or set(summary) != {
            "site_count", "lane_count", "intermediate_count", "outlet_count",
            "buyer_demand", "maximum_lane_lead", "minimum_lane_capacity",
            "maximum_lane_capacity"}):
        raise ValueError("structural summary shape differs")
    family, size = item["family"], item["size"]
    factors = item["factors"]
    if not isinstance(factors, dict) or set(factors) != set(FACTOR_KEYS):
        raise ValueError("structural summary lacks declared factor bundle")
    base = {
        ("serial", "small"): (1, 0, 0, 0),
        ("serial", "large"): (2, 1, 0, 1),
        ("assembly", "small"): (1, 0, 0, 0),
        ("assembly", "large"): (1, 0, 0, 0),
        ("distribution", "small"): (2, 1, 0, 1),
        ("distribution", "large"): (3, 2, 0, 2),
        ("layered_dual", "small"): (3, 3, 1, 1),
        ("layered_dual", "large"): (4, 5, 2, 1),
        ("planted", "small"): (3, 3, 1, 1),
        ("planted", "large"): (4, 5, 2, 1),
    }
    if (family, size) not in base:
        raise ValueError("unknown family-size summary")
    sites, lanes, hubs, outlets = base[family, size]
    alt = factors["alternate_source_count"]
    if type(alt) is not int or alt not in (0, 1, 2):
        raise ValueError("invalid ready-source count")
    new_outlets = summary["outlet_count"] - outlets
    extra_hubs = summary["intermediate_count"] - hubs
    maximum_outlets = (1 if family == "serial" and size == "small" else
                       1 if family == "assembly" and size == "small" else
                       2 if family == "assembly" and size == "large" else 0)
    maximum_hubs = 1 if size == "small" else 2
    if (type(new_outlets) is not int or type(extra_hubs) is not int
            or not 0 <= new_outlets <= maximum_outlets
            or not 0 <= extra_hubs <= maximum_hubs
            or (outlets + new_outlets == 0 and extra_hubs != 0)
            or summary["site_count"] != sites + alt + new_outlets + extra_hubs
            or summary["lane_count"] != lanes + alt + new_outlets + 2 * extra_hubs):
        raise ValueError("structural node or route count outside family-size range")
    allowed_buyer = {
        ("serial", "small"): ({1.0} if new_outlets == 0 else {0.75, 0.875}),
        ("serial", "large"): {0.75, 0.875},
        ("assembly", "small"): ({1.0} if new_outlets == 0 else {0.75, 0.875}),
        ("assembly", "large"): ({1.0} if new_outlets == 0 else
                                {0.75, 0.875} if new_outlets == 1 else
                                {0.5, 0.625, 0.75}),
        ("distribution", "small"): {0.75, 0.875},
        ("distribution", "large"): {0.5, 0.625, 0.75},
        ("layered_dual", "small"): {0.375, 0.5, 0.625},
        ("layered_dual", "large"): {0.375, 0.5, 0.625},
        ("planted", "small"): {0.375, 0.5, 0.625},
        ("planted", "large"): {0.375, 0.5, 0.625},
    }
    if summary["buyer_demand"] not in allowed_buyer[family, size]:
        raise ValueError("buyer demand split outside family range")
    lane_count = summary["lane_count"]
    if lane_count == 0:
        if (summary["maximum_lane_lead"] != 0
                or summary["minimum_lane_capacity"] is not None
                or summary["maximum_lane_capacity"] is not None):
            raise ValueError("empty lane summary differs")
    else:
        low, high = summary["minimum_lane_capacity"], summary["maximum_lane_capacity"]
        allowed = set(ORDINARY_CAPACITIES) | set(READY_CAPACITIES)
        if (summary["maximum_lane_lead"] not in LANE_LEADS
                or low not in allowed or high not in allowed or low > high):
            raise ValueError("lane lead or capacity outside declared range")


def validate_metadata_value(root: Path, value: dict) -> None:
    """Validate public declarations only; private file audit is separately gated."""
    rows = _selected_rows(root)
    header = _header(root, rows)
    if (not isinstance(value, dict) or set(value) != set(header) | {"cases"}
            or any(value[key] != item for key, item in header.items())):
        raise ValueError("v3 structural confirmation header differs")
    saved = value["cases"]
    if not isinstance(saved, list) or len(saved) != 56:
        raise ValueError("v3 structural confirmation case count differs")
    old = {**_old_header(root, rows), "cases": []}
    structure_hashes = set()
    design_hashes = design_structure_digests(root)
    for item in saved:
        if (not isinstance(item, dict)
                or set(item) != set(_public_row(rows[0])) | {
                    "content_sha256", "structure_sha256", "structure_summary",
                    "draw_attempts"}):
            raise ValueError("unexpected structural confirmation row fields")
        old["cases"].append({key: item[key] for key in
                             set(_public_row(rows[0])) | {"content_sha256"}})
        signature = item["structure_sha256"]
        if (not isinstance(signature, str)
                or re.fullmatch(r"[0-9a-f]{64}", signature) is None
                or signature in design_hashes or signature in structure_hashes):
            raise ValueError("duplicate or invalid declared structural signature")
        structure_hashes.add(signature)
        if type(item["draw_attempts"]) is not int or not 1 <= item["draw_attempts"] <= 64:
            raise ValueError("structural draw attempt outside cap")
        _validate_summary(item)
    validate_confirmation_metadata_value(root, old)
    for item, row in zip(saved, rows):
        expected_factors = {key: row[key] for key in FACTOR_KEYS}
        if item["factors"] != expected_factors:
            raise ValueError("confirmation factor bundle differs from fixed allocation slot")


def verify_metadata(root: Path, path: Path) -> str:
    root = root.resolve()
    if path.resolve() != root / PUBLIC_METADATA:
        raise ValueError("unexpected v3 public-metadata path")
    raw = path.read_bytes()
    value = json.loads(raw)
    if raw != canonical(value):
        raise ValueError("noncanonical v3 confirmation metadata")
    validate_metadata_value(root, value)
    return sha256(raw).hexdigest()


def owner_seal(root: Path, seed_file: Path, private_root: Path,
               public_metadata: Path) -> str:
    """Owner-only one-shot generation; no agent invocation or retries."""
    root = root.resolve()
    if (public_metadata.resolve() != root / PUBLIC_METADATA
            or private_root.resolve().is_relative_to(root)
            or seed_file.resolve().is_relative_to(root)
            or private_root.exists() or public_metadata.exists()
            or private_root.is_symlink() or not private_root.parent.is_dir()):
        raise ValueError("owner-seal private/public destination violates scope")
    master = _private_seed(seed_file, root)
    # Construct and reject all 56 before creating any output directory.
    rows = _selected_rows(root)
    design = design_structure_digests(root)
    accepted: set[str] = set()
    drawn = []
    for row in rows:
        base = _build_private_case(row, master)
        case, attempts = draw_novel_case(base, row, master, design, accepted)
        drawn.append((row, case, attempts))
    private_root.mkdir(mode=0o700, parents=False, exist_ok=False)
    public_rows = []
    for row, case, attempts in drawn:
        raw = canonical(asdict(case))
        descriptor = os.open(private_root / f"{case.case_id}.json",
                             os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
        public_rows.append(_public_case_row(row, case, attempts))
    metadata = {**_header(root, rows), "cases": public_rows}
    with public_metadata.open("xb") as stream:
        stream.write(canonical(metadata))
    return verify_metadata(root, public_metadata)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.confirmation_v3_r2")
    sub = parser.add_subparsers(dest="command", required=True)
    seal = sub.add_parser("owner-seal")
    seal.add_argument("--repository-root", required=True, type=Path)
    seal.add_argument("--seed-file", required=True, type=Path)
    seal.add_argument("--private-root", required=True, type=Path)
    seal.add_argument("--public-metadata", required=True, type=Path)
    check = sub.add_parser("verify-confirmation-metadata")
    check.add_argument("--repository-root", required=True, type=Path)
    check.add_argument("--public-metadata", required=True, type=Path)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    if args.command == "owner-seal":
        digest = owner_seal(root, args.seed_file, args.private_root, args.public_metadata)
    else:
        digest = verify_metadata(root, args.public_metadata)
    print(json.dumps({"public_metadata_sha256": digest, "case_count": 56,
                      "private_cases_opened_by_agent": False}, sort_keys=True))


if __name__ == "__main__":
    main()
