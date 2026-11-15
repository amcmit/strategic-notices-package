"""Pure v3 public factor allocation. It builds metadata, never networks."""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path


V1_PUBLIC_MANIFEST_SHA256 = "25a9adf84c8a4bcb8aa653456ef24e4304a1e9be87931695f5d7fad5a473f3f3"
G0_PROTOCOL_SHA256 = "f9b7c4a4bf5d449dd5e9e6f625cfd4daf9c5c716033cb66b93a0a4484fbc3a2c"
PUBLIC_MASTER_SEED = 20261114
HORIZON = 20
R1_TIMINGS = ((1, 2, 3), (2, 2, 4), (1, 3, 4), (2, 3, 5))
R2_TIMINGS = ((1, 6, 3), (2, 6, 3), (1, 6, 4), (2, 6, 4))
R3_TIMINGS = ((1, 2, 6), (2, 3, 8))
INTERIOR_INVENTORY = (
    ("0.25", 1, 1, "0.25", "0.25", 1),
    ("0.25", 1, 3, 3, "0.25", 1),
    ("0.25", 3, 3, 1, 3, "0.25"),
    (3, 1, 3, "0.25", 1, 3),
)
CONTRAST_INVENTORY = ("0.25", 1, 3, "0.25", 1, 3, "0.25", 1)
CONTRAST_FEE = (1, 3, 6, 3, 6, 1, 1, 3)
CONTRAST_FAMILY_SIZE = {
    "R2": (("serial", "small"), ("serial", "large"),
           ("assembly", "small"), ("assembly", "large"),
           ("distribution", "small"), ("layered_dual", "small"),
           ("layered_dual", "large"), ("planted", "large")),
    "R3": (("serial", "small"), ("serial", "large"),
           ("assembly", "small"), ("distribution", "small"),
           ("distribution", "large"), ("layered_dual", "large"),
           ("planted", "small"), ("planted", "large")),
    "R4": (("serial", "small"), ("assembly", "small"),
           ("assembly", "large"), ("distribution", "large"),
           ("layered_dual", "small"), ("layered_dual", "large"),
           ("planted", "small"), ("planted", "large")),
}
# Base timing, family, size and commitment. Rotation moves timing and
# recomputes the inventory boundary; odd rotations flip commitment.
BOUNDARY = (
    ("inventory", 0, "serial", "small", "finite"),
    ("inventory", 1, "assembly", "large", "horizon"),
    ("inventory", 2, "distribution", "small", "finite"),
    ("inventory", 3, "layered_dual", "large", "horizon"),
    ("fee", 0, "planted", "small", "horizon"),
    ("fee", 1, "serial", "large", "finite"),
    ("fee", 2, "assembly", "small", "horizon"),
    ("fee", 3, "distribution", "large", "finite"),
)


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode()


def _base_row(candidate: int, slot_id: str, regime: str, category: str,
              family: str, size: str, timing: tuple[int, int, int],
              inventory: str | int, fee: int, commitment: str,
              *, alternate_source_count: int = 0,
              alternate_stock_per_source: int | None = None) -> dict:
    short, lead, long = timing
    return {
        "candidate_index": candidate,
        "slot_id": slot_id,
        "case_id": f"v3-c{candidate}-{slot_id}",
        "regime": regime,
        "category": category,
        "family": family,
        "size": size,
        "short_duration": short,
        "qualification_lead": lead,
        "long_duration": long,
        "inventory_cover": inventory,
        "qualification_fee": fee,
        "commitment": commitment,
        "alternate_source_count": alternate_source_count,
        "alternate_stock_per_source": alternate_stock_per_source,
        "spot_capacity": 1 if regime == "R4" else 0,
        "spot_price": "1.2" if regime == "R4" else None,
    }


def _public_r1_slots(public_manifest: Path) -> tuple[tuple[str, str, str], ...]:
    raw = public_manifest.read_bytes()
    if sha256(raw).hexdigest() != V1_PUBLIC_MANIFEST_SHA256:
        raise ValueError("public structural slot index differs from frozen hash")
    manifest = json.loads(raw)
    rows = manifest.get("networks")
    if not isinstance(rows, list) or len(rows) != 24:
        raise ValueError("public structural slot index must have 24 rows")
    slots = tuple((item["id"], item["family"], item["size"]) for item in rows)
    if len(set(slot for slot, _, _ in slots)) != 24:
        raise ValueError("duplicate public structural slot")
    return slots


def build_allocation(public_manifest: Path) -> dict:
    """Return 3×56 public factor rows; this function builds no case."""
    slots = _public_r1_slots(public_manifest)
    candidates = []
    for candidate in (0, 1, 2):
        rows = []
        for i, (slot, family, size) in enumerate(slots):
            block = i // 4
            timing_index = (i + candidate) % 4
            inventory = INTERIOR_INVENTORY[timing_index][block]
            commitment = "finite" if (block + candidate) % 2 == 0 else "horizon"
            fee = (1, 3, 6)[(block + candidate) % 3]
            rows.append(_base_row(
                candidate, f"r1-interior-{slot}", "R1", "interior",
                family, size, R1_TIMINGS[timing_index], inventory, fee,
                commitment,
            ))
        for category, base_timing, family, size, base_commitment in BOUNDARY:
            timing_index = (base_timing + candidate) % 4
            short, lead, long = R1_TIMINGS[timing_index]
            if category == "inventory":
                inventory, fee = short + lead, 1
            else:
                inventory, fee = long - 1, 20
            commitment = (base_commitment if candidate % 2 == 0 else
                          ("horizon" if base_commitment == "finite" else "finite"))
            rows.append(_base_row(
                candidate, f"r1-{category}-t{base_timing + 1}", "R1",
                f"{category}_closed", family, size,
                R1_TIMINGS[timing_index], inventory, fee, commitment,
            ))
        for regime, timings in (("R2", R2_TIMINGS), ("R3", R3_TIMINGS),
                                ("R4", R1_TIMINGS)):
            for j, (family, size) in enumerate(CONTRAST_FAMILY_SIZE[regime]):
                timing = timings[(j + candidate) % len(timings)]
                commitment = "finite" if (j // 4 + candidate) % 2 == 0 else "horizon"
                alt_count = (1 if j % 2 == 0 else 2) if regime == "R4" else 0
                alt_stock = (2 if (j // 2) % 2 == 0 else 20) if regime == "R4" else None
                rows.append(_base_row(
                    candidate, f"{regime.lower()}-{j}", regime, "contrast",
                    family, size, timing, CONTRAST_INVENTORY[j],
                    CONTRAST_FEE[j], commitment,
                    alternate_source_count=alt_count,
                    alternate_stock_per_source=alt_stock,
                ))
        if len(rows) != 56 or len({row["case_id"] for row in rows}) != 56:
            raise AssertionError("v3 candidate must contain 56 unique rows")
        candidates.append({"candidate_index": candidate, "cases": rows})
    allocation = {
        "schema": 1,
        "status": "proposal_only_allocation_unapproved",
        "study_version": "strategic-notices-v3",
        "g0_protocol_sha256": G0_PROTOCOL_SHA256,
        "v1_public_structural_slot_sha256": V1_PUBLIC_MANIFEST_SHA256,
        "public_master_seed": PUBLIC_MASTER_SEED,
        "horizon": HORIZON,
        "primary_price": 1,
        "backup_price": "1.25",
        "backup_minimum": 1,
        "net_shortage_value": 20,
        "candidate_order": [0, 1, 2],
        "cases_per_candidate": 56,
        "confirmation_allocation": "same 56-slot factor and family-size counts with independent owner-held seeds; no case IDs or private data here",
        "confirmation_case_access": False,
        "candidates": candidates,
    }
    validate_allocation(allocation)
    return allocation


def validate_allocation(allocation: dict) -> None:
    if (allocation.get("status") != "proposal_only_allocation_unapproved"
            or allocation.get("candidate_order") != [0, 1, 2]
            or allocation.get("cases_per_candidate") != 56
            or allocation.get("confirmation_case_access") is not False):
        raise ValueError("v3 allocation scope mismatch")
    all_ids = []
    expected_regime = {"R1": 32, "R2": 8, "R3": 8, "R4": 8}
    expected_families = {"serial": 12, "assembly": 12, "distribution": 10,
                         "layered_dual": 11, "planted": 11}
    for candidate in allocation["candidates"]:
        rows = candidate["cases"]
        if len(rows) != 56 or Counter(row["regime"] for row in rows) != expected_regime:
            raise ValueError("v3 regime allocation mismatch")
        if Counter(row["family"] for row in rows) != expected_families:
            raise ValueError("v3 family allocation mismatch")
        if Counter(row["size"] for row in rows) != {"small": 28, "large": 28}:
            raise ValueError("v3 size allocation mismatch")
        if Counter(row["commitment"] for row in rows) != {"finite": 28, "horizon": 28}:
            raise ValueError("v3 commitment allocation mismatch")
        if Counter(row["short_duration"] for row in rows) != {1: 28, 2: 28}:
            raise ValueError("v3 short-duration allocation mismatch")
        if Counter(row["qualification_lead"] for row in rows) != {2: 24, 3: 24, 6: 8}:
            raise ValueError("v3 lead allocation mismatch")
        if Counter(row["long_duration"] for row in rows) != {
            3: 14, 4: 24, 5: 10, 6: 4, 8: 4,
        }:
            raise ValueError("v3 long-duration allocation mismatch")
        if Counter(row["inventory_cover"] for row in rows) != {
            "0.25": 17, 1: 17, 2: 1, 3: 17, 4: 3, 5: 1,
        }:
            raise ValueError("v3 inventory allocation mismatch")
        if Counter(row["qualification_fee"] for row in rows) != {
            1: 21, 3: 17, 6: 14, 20: 4,
        }:
            raise ValueError("v3 fee allocation mismatch")
        if Counter(row["category"] for row in rows) != {
            "interior": 24, "inventory_closed": 4, "fee_closed": 4,
            "contrast": 24,
        }:
            raise ValueError("v3 category allocation mismatch")
        interior = [row for row in rows if row["category"] == "interior"]
        if Counter(row["inventory_cover"] for row in interior) != {
            "0.25": 8, 1: 8, 3: 8,
        } or Counter(row["qualification_fee"] for row in interior) != {
            1: 8, 3: 8, 6: 8,
        }:
            raise ValueError("v3 R1 interior factor balance mismatch")
        if Counter(row["alternate_source_count"] for row in rows if row["regime"] == "R4") != {
            1: 4, 2: 4,
        }:
            raise ValueError("v3 R4 alternative allocation mismatch")
        if Counter(row["alternate_stock_per_source"] for row in rows
                   if row["regime"] == "R4") != {2: 4, 20: 4}:
            raise ValueError("v3 R4 ready-stock allocation mismatch")
        for row in rows:
            if row["candidate_index"] != candidate["candidate_index"]:
                raise ValueError("candidate ID mismatch")
            if row["regime"] != "R4" and (row["spot_capacity"] != 0
                                              or row["alternate_source_count"] != 0):
                raise ValueError("ready critical source outside R4")
            all_ids.append(row["case_id"])
    if len(all_ids) != 168 or len(set(all_ids)) != 168:
        raise ValueError("duplicate or missing v3 case ID")


def write_proposal(public_manifest: Path, output: Path) -> str:
    if output.exists():
        raise FileExistsError("v3 allocation proposal already exists")
    data = canonical(build_allocation(public_manifest))
    output.write_bytes(data)
    return sha256(data).hexdigest()
