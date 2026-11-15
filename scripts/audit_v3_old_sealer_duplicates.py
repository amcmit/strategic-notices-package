"""Read-only audit of the withdrawn fixed-template sealer; no case construction."""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
from random import Random

from sn.allocation_v3 import PUBLIC_MASTER_SEED, canonical
from sn.confirmation_v3 import FACTOR_KEYS, _assign_factor_rows, _confirmation_row
from sn.networks_v3 import _seed


ROOT = Path(__file__).resolve().parents[1]
NO_LANE = {("serial", "small"), ("assembly", "small"), ("assembly", "large")}


def _has_no_lane(row: dict) -> bool:
    return ((row["family"], row["size"]) in NO_LANE
            and row["alternate_source_count"] == 0)


def _key(row: dict) -> tuple:
    return row["family"], row["size"], tuple((name, row[name]) for name in FACTOR_KEYS)


def _cheap(master: int, slot: str) -> int:
    # The withdrawn generator's only topology random draw was this 3-way cost.
    return Random(_seed(master, slot)).randrange(3)


def _index(rows: list[dict]) -> dict[tuple, set[int]]:
    result = defaultdict(set)
    for row in rows:
        result[_key(row)].add(_cheap(PUBLIC_MASTER_SEED, row["slot_id"]))
    return result


def _structure_key(row: dict) -> tuple:
    return (row["family"], row["size"], row["inventory_cover"],
            row["alternate_source_count"], row["alternate_stock_per_source"])


def _structure_index(rows: list[dict]) -> dict[tuple, set[int]]:
    result = defaultdict(set)
    for row in rows:
        result[_structure_key(row)].add(_cheap(PUBLIC_MASTER_SEED, row["slot_id"]))
    return result


def audit() -> dict:
    allocation = json.loads((ROOT / "configs/proposed-v3-allocation.json").read_bytes())
    selected = allocation["candidates"][0]["cases"]
    all_design = [row for candidate in allocation["candidates"]
                  for row in candidate["cases"]]
    indexes = {"selected_56": _index(selected), "all_168": _index(all_design)}
    structure_indexes = {"selected_56": _structure_index(selected),
                         "all_168": _structure_index(all_design)}
    summary = {}
    for name, design in indexes.items():
        exact_counts, no_lane_counts, structure_counts = [], [], []
        for seed_number in range(1000):
            public_seed_bytes = seed_number.to_bytes(32, "big")
            master = int.from_bytes(sha256(
                b"strategic-notices-v3-confirmation\0" + public_seed_bytes
            ).digest(), "big")
            assigned = _assign_factor_rows(selected, master)
            exact = no_lane = structural = 0
            for row in assigned:
                structural_options = structure_indexes[name].get(_structure_key(row), set())
                if (structural_options and (_has_no_lane(row)
                        or _cheap(master, _confirmation_row(row)["slot_id"])
                        in structural_options)):
                    structural += 1
                options = design.get(_key(row), set())
                if not options:
                    continue
                if _has_no_lane(row):
                    exact += 1
                    no_lane += 1
                elif _cheap(master, _confirmation_row(row)["slot_id"]) in options:
                    exact += 1
            exact_counts.append(exact)
            no_lane_counts.append(no_lane)
            structure_counts.append(structural)
        summary[name] = {
            "exact_physical_copy_min": min(exact_counts),
            "exact_physical_copy_max": max(exact_counts),
            "exact_physical_copy_mean": sum(exact_counts) / len(exact_counts),
            "no_lane_copy_min": min(no_lane_counts),
            "no_lane_copy_max": max(no_lane_counts),
            "no_lane_copy_mean": sum(no_lane_counts) / len(no_lane_counts),
            "seed_zero_exact_copies": exact_counts[0],
            "structural_copy_min": min(structure_counts),
            "structural_copy_max": max(structure_counts),
            "structural_copy_mean": sum(structure_counts) / len(structure_counts),
            "seed_zero_structural_copies": structure_counts[0],
        }
    return {
        "kind": "withdrawn_v3_sealer_duplicate_audit",
        "method": "factor permutation and three-way lane-cost draw only; no cases built",
        "public_test_seeds": "integers 0..999, each expanded to 32 bytes and domain-hashed",
        "exact_owner_seed_count": "unknown; owner seed was never read",
        "fixed_family_size_templates_reused_per_set": 56,
        "unpermuted_assignment_no_lane_forced_copies": sum(
            _has_no_lane(row) for row in selected),
        "comparators": summary,
    }


if __name__ == "__main__":
    path = ROOT / "docs/results/v3_old_confirmation_duplicate_audit.json"
    with path.open("xb") as output:
        output.write(canonical(audit()))
    print(path, sha256(path.read_bytes()).hexdigest())
