"""Owner-only v3 confirmation sealing and public-metadata-only verification.

Do not invoke owner-seal in an agent workspace. The owner supplies an encrypted
volume and a private seed outside the repository. This module never solves a
planner problem and never writes a private case into the repository.
"""

from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from random import Random
import re
from stat import S_IMODE, S_ISREG

from .allocation_v3 import HORIZON, canonical
from .core_v3 import ContractV3
from .freeze_v3_design import OUTPUT as FREEZE_PATH, verify_freeze
from .networks_v3 import V3PublicCase, _approved_hash, _topology, load_allocation


PUBLIC_METADATA = "configs/v3-confirmation-public.json"
FACTOR_KEYS = (
    "short_duration", "qualification_lead", "long_duration",
    "inventory_cover", "qualification_fee", "commitment",
    "alternate_source_count", "alternate_stock_per_source",
    "spot_capacity", "spot_price",
)
SOURCE_FILES = (
    "src/sn/allocation_v3.py", "src/sn/core.py", "src/sn/core_v3.py",
    "src/sn/networks_v3.py", "src/sn/freeze_v3_design.py",
    "src/sn/confirmation_v3.py",
)


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _source_hashes(root: Path) -> dict[str, str]:
    return {name: _digest(root / name) for name in SOURCE_FILES}


def _selected_rows(root: Path) -> list[dict]:
    digest = verify_freeze(root)
    freeze = json.loads((root / FREEZE_PATH).read_bytes())
    if digest != _digest(root / FREEZE_PATH) or freeze["selected_candidate"] != 0:
        raise ValueError("v3 selected-design freeze changed")
    allocation = load_allocation(
        root / "configs/proposed-v3-allocation.json",
        root / "configs/design/manifest.json",
        _approved_hash(root / "configs/owner-approvals/v3-allocation.json"),
    )
    rows = allocation["candidates"][0]["cases"]
    if len(rows) != 56 or [row["case_id"] for row in rows] != [
            case["case_id"] for case in freeze["cases"]]:
        raise ValueError("confirmation slots differ from frozen selected design")
    return rows


def _confirmation_row(row: dict) -> dict:
    result = dict(row)
    result["slot_id"] = "confirmation-" + row["slot_id"]
    result["case_id"] = f"v3-c0-{result['slot_id']}"
    return result


def _assign_factor_rows(rows: list[dict], master_seed: int) -> list[dict]:
    """Permute full factor bundles within regime/category, keeping slots fixed."""
    rng = Random(int.from_bytes(sha256(canonical((
        master_seed, "v3-confirmation-factor-allocation"))).digest(), "big"))
    assigned = [dict(row) for row in rows]
    for group in sorted({(row["regime"], row["category"]) for row in rows}):
        indexes = [i for i, row in enumerate(rows)
                   if (row["regime"], row["category"]) == group]
        donors = indexes.copy()
        rng.shuffle(donors)
        for index, donor in zip(indexes, donors):
            for key in FACTOR_KEYS:
                assigned[index][key] = rows[donor][key]
    return assigned


def _public_row(row: dict) -> dict:
    private = _confirmation_row(row)
    return {
        "case_id": private["case_id"], "slot_id": private["slot_id"],
        "regime": row["regime"], "category": row["category"],
        "family": row["family"], "size": row["size"],
        "factors": {key: row[key] for key in FACTOR_KEYS},
        "no_disruption_feasible": True,
    }


def _expected_header(root: Path, rows: list[dict]) -> dict:
    return {
        "schema": 1, "kind": "v3_owner_sealed_confirmation_public_metadata",
        "status": "owner_sealed_private_cases_not_opened_by_agent",
        "selected_candidate": 0, "selected_design_freeze_sha256": _digest(root / FREEZE_PATH),
        "allocation_sha256": _digest(root / "configs/proposed-v3-allocation.json"),
        "base_protocol_sha256": _digest(root / "docs/V3_SINGLE_SOLVE_R3_PROTOCOL.md"),
        "reporting_protocol_amendment_sha256": _digest(
            root / "docs/V3_PROTOCOL_R4_REPORTING_AMENDMENT.md"),
        "source_sha256": _source_hashes(root), "count": 56,
        "factor_assignment_rule": "private-seed permutation of complete factor bundles within regime and category over fixed family-size slots",
        "regime_counts": dict(sorted(Counter(x["regime"] for x in rows).items())),
        "category_counts": dict(sorted(Counter(x["category"] for x in rows).items())),
        "family_counts": dict(sorted(Counter(x["family"] for x in rows).items())),
        "size_counts": dict(sorted(Counter(x["size"] for x in rows).items())),
        "private_seed_minimum_bits": 128,
        "confirmation_seed_disclosed": False,
        "private_root_disclosed": False,
    }


def validate_confirmation_metadata_value(root: Path, value: dict) -> None:
    """Pure schema, allocation and count check for public metadata."""
    if not isinstance(value, dict):
        raise ValueError("confirmation public metadata must be an object")
    rows = _selected_rows(root)
    expected = _expected_header(root, rows)
    if set(value) != set(expected) | {"cases"} or any(
            value[key] != item for key, item in expected.items()):
        raise ValueError("confirmation header or factor counts differ")
    saved = value["cases"]
    if not isinstance(saved, list) or len(saved) != 56:
        raise ValueError("confirmation case count differs")
    hashes = set()
    factor_bundles: dict[tuple[str, str], Counter[bytes]] = {}
    for item, row in zip(saved, rows):
        if not isinstance(item, dict) or set(item) != set(_public_row(row)) | {"content_sha256"}:
            raise ValueError("unexpected confirmation case metadata fields")
        digest = item["content_sha256"]
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("malformed private case content hash")
        expected = _public_row(row)
        if (any(item[key] != expected[key] for key in expected
                if key != "factors")
                or not isinstance(item["factors"], dict)
                or set(item["factors"]) != set(FACTOR_KEYS)):
            raise ValueError("confirmation case slot, factor or feasibility differs")
        group = (row["regime"], row["category"])
        factor_bundles.setdefault(group, Counter())[canonical(item["factors"])] += 1
        hashes.add(digest)
    if len(hashes) != 56:
        raise ValueError("duplicate private case content hash")
    for group in factor_bundles:
        expected_bundles = Counter(canonical({key: row[key] for key in FACTOR_KEYS})
                                   for row in rows
                                   if (row["regime"], row["category"]) == group)
        if factor_bundles[group] != expected_bundles:
            raise ValueError("confirmation factor-bundle counts differ")


def verify_confirmation_metadata(root: Path, metadata_path: Path) -> str:
    """Check public fields and declared hashes; never reads private cases."""
    root = root.resolve()
    if metadata_path.resolve() != root / PUBLIC_METADATA:
        raise ValueError("unexpected v3 confirmation public-metadata path")
    raw = metadata_path.read_bytes()
    value = json.loads(raw)
    if canonical(value) != raw:
        raise ValueError("noncanonical confirmation public metadata")
    validate_confirmation_metadata_value(root, value)
    return sha256(raw).hexdigest()


def _private_seed(seed_file: Path, root: Path) -> int:
    if seed_file.is_symlink() or seed_file.resolve().is_relative_to(root):
        raise ValueError("private seed must be a regular file outside the repository")
    info = seed_file.stat()
    if not S_ISREG(info.st_mode) or S_IMODE(info.st_mode) & 0o077:
        raise ValueError("private seed must be a private regular file (mode 0600)")
    raw = seed_file.read_text(encoding="ascii").strip()
    if (len(raw) < 32 or len(raw) % 2 != 0
            or re.fullmatch(r"[0-9a-fA-F]+", raw) is None):
        raise ValueError("private seed must contain at least 128 bits as hex")
    seed = bytes.fromhex(raw)
    return int.from_bytes(sha256(b"strategic-notices-v3-confirmation\0" + seed).digest(), "big")


def _build_private_case(row: dict, master_seed: int) -> V3PublicCase:
    """Mirror frozen public economics; use a distinct ID and private topology seed."""
    private = _confirmation_row(row)
    network, demand = _topology(private, master_seed)
    contract = ContractV3(
        order_lead=1, revelation_period=row["short_duration"],
        primary_price=1, backup_price=1.25, backup_minimum=1,
        qualification_lead=row["qualification_lead"],
        qualification_cost=row["qualification_fee"],
        backup_commitment_periods=(row["long_duration"]
                                   if row["commitment"] == "finite" else None),
        spot_capacity=row["spot_capacity"],
        spot_price=float(row["spot_price"]) if row["spot_price"] is not None else 6,
        net_shortage_value=20, demand_rule="lost_sales",
    )
    capacity = lambda duration: tuple(0.0 if t < duration else 1.0
                                      for t in range(HORIZON))
    return V3PublicCase(
        0, private["case_id"], private["slot_id"], row["regime"],
        row["category"], row["family"], row["size"], network, contract,
        capacity(row["short_duration"]), capacity(row["long_duration"]), demand,
    )


def owner_seal(root: Path, seed_file: Path, private_root: Path,
               public_metadata: Path) -> str:
    """The earlier fixed-template sealer is withdrawn and cannot run."""
    raise RuntimeError("fixed-template owner-seal is superseded; use the reviewed structural version")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.confirmation_v3")
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
        digest = verify_confirmation_metadata(root, args.public_metadata)
    print(json.dumps({"public_metadata_sha256": digest, "case_count": 56,
                      "private_cases_opened_by_agent": False}, sort_keys=True))


if __name__ == "__main__":
    main()
