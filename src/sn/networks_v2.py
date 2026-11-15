"""Versioned generator for approved v2 *public design* cases only.

This module has no confirmation generator or owner-sealing entry point. It
does not call a planner. A fixed public seed per structural slot keeps lane
costs common across candidate factor assignments.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
from pathlib import Path

from .core import CoreNetwork, Demand, Stock
from .core_v2 import ContractV2
from .networks import NetworkCase, _seed, build_case, case_demand_schedule
from .suite import canonical, validate_case

APPROVED_ALLOCATION_SHA256 = "bed8cdc18ef9e5d6051789c8d482154baafd16ff984df656b9f4dc190174c919"
V1_DESIGN_MANIFEST_SHA256 = "25a9adf84c8a4bcb8aa653456ef24e4304a1e9be87931695f5d7fad5a473f3f3"
PUBLIC_MASTER_SEED = 20261112
SOURCE_FILES = ("core.py", "core_v2.py", "networks.py", "networks_v2.py", "suite.py")


@dataclass(frozen=True)
class V2DesignCase:
    candidate_index: int
    slot_id: str
    case: NetworkCase
    contract: ContractV2
    short_capacity: tuple[float, ...]
    long_capacity: tuple[float, ...]
    baseline_demand: tuple[tuple[Demand, ...], ...]
    long_outage_length: int
    main_buffer_cover: int
    alternate_stock_per_source: int | None


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def source_hashes() -> dict[str, str]:
    root = Path(__file__).parent
    return {name: _digest(root / name) for name in SOURCE_FILES}


def load_allocation(allocation_path: Path, v1_design_path: Path) -> tuple[dict, dict]:
    """Read only the approved public allocation and frozen v1 design index."""
    raw = allocation_path.read_bytes()
    if sha256(raw).hexdigest() != APPROVED_ALLOCATION_SHA256:
        raise ValueError("v2 allocation differs from owner-approved SHA-256")
    allocation = json.loads(raw)
    if canonical(allocation) != raw or allocation.get("status") != "proposal_only_no_execution":
        raise ValueError("v2 allocation is noncanonical or has unexpected status")
    design_raw = v1_design_path.read_bytes()
    if sha256(design_raw).hexdigest() != V1_DESIGN_MANIFEST_SHA256:
        raise ValueError("v1 public design index differs from frozen SHA-256")
    design = json.loads(design_raw)
    if canonical(design) != design_raw or design.get("count") != 24:
        raise ValueError("v1 public design index is malformed")
    if (allocation.get("candidate_order") != [0, 1, 2]
            or allocation.get("design_count_per_candidate") != 24
            or allocation.get("v1_design_manifest_sha256") != V1_DESIGN_MANIFEST_SHA256
            or allocation.get("confirmation_case_access") is not False
            or allocation.get("new_mip_solves_now") != 0):
        raise ValueError("v2 allocation scope differs from approved public design")
    return allocation, design


def _capacity(case: NetworkCase, duration: int) -> tuple[float, ...]:
    onset = case.demand_start - 1
    return tuple(0.0 if onset <= period < onset + duration else case.primary_capacity
                 for period in range(case.network.horizon))


def build_public_case(row: dict, candidate_index: int, *, master_seed: int) -> V2DesignCase:
    """Regenerate one public structural slot and apply only its frozen factors."""
    slot_id = row["slot_id"]
    family, size, replicate_text = slot_id.rsplit("-", 2)
    replicate = int(replicate_text)
    if (row["family"], row["size"], row["v2_case_id"]) != (
        family, size, "v2-" + slot_id
    ):
        raise ValueError(f"factor row identity mismatch: {slot_id}")
    seed = _seed(master_seed, "v2-public-design", slot_id)
    base = build_case(family, size, replicate, seed=seed)
    if base.identifier != slot_id or len(base.alternative_sources) != row["alternative_source_count"]:
        raise ValueError(f"structural slot mismatch: {slot_id}")
    buffer = row["main_buffer_cover"]
    stock_per_source = row["alternate_stock_per_source"]
    duration = row["long_outage_length"]
    commitment = row["backup_commitment_periods"]
    if (type(buffer) is not int or buffer not in (0, 1, 3)
            or type(duration) is not int or duration not in (3, 4, 5)
            or commitment not in (2, 4, "through_horizon")
            or (stock_per_source is None) != (not base.alternative_sources)
            or (stock_per_source is not None and
                (type(stock_per_source) is not int or stock_per_source not in (0, 2, 20)))):
        raise ValueError(f"factor level mismatch: {slot_id}")
    changed: list[Stock] = []
    buffer_count = alt_count = 0
    for item in base.network.initial_stock:
        if (item.site, item.product) == (base.demand_site, base.demand_product):
            changed.append(replace(item, quantity=float(buffer) * base.demand_per_period))
            buffer_count += 1
        elif item.site in base.alternative_sources and item.product == base.network.primary_product:
            changed.append(replace(item, quantity=float(stock_per_source)))
            alt_count += 1
        else:
            changed.append(item)
    if buffer_count != 1 or alt_count != len(base.alternative_sources):
        raise ValueError(f"buffer or alternate-stock source missing: {slot_id}")
    network: CoreNetwork = replace(base.network, initial_stock=tuple(changed))
    case = replace(base, identifier=row["v2_case_id"], network=network)
    contract = ContractV2(
        revelation_period=case.demand_start, spot_capacity=2, backlog_cost=20,
        backup_commitment_periods=None if commitment == "through_horizon" else commitment,
    )
    return V2DesignCase(
        candidate_index, slot_id, case, contract,
        _capacity(case, 1), _capacity(case, duration), case_demand_schedule(case),
        duration, buffer, stock_per_source,
    )


def build_public_design(allocation: dict, design: dict, *, master_seed: int = PUBLIC_MASTER_SEED
                        ) -> tuple[V2DesignCase, ...]:
    if type(master_seed) is not int or master_seed != PUBLIC_MASTER_SEED:
        raise ValueError("v2 public master seed differs from frozen engineering choice")
    expected_ids = [row["id"] for row in design["networks"]]
    cases = []
    for expected_candidate, candidate in enumerate(allocation["candidates"]):
        if (candidate["candidate_index"] != expected_candidate
                or [row["slot_id"] for row in candidate["design"]] != expected_ids):
            raise ValueError("v2 candidate or public design slot order mismatch")
        for row in candidate["design"]:
            generated = build_public_case(row, expected_candidate, master_seed=master_seed)
            if not validate_case(generated.case):
                raise ValueError(f"no-disruption feasibility failed: {generated.case.identifier}")
            cases.append(generated)
    if len(cases) != 72:
        raise ValueError("v2 public design must contain 72 candidate cases")
    return tuple(cases)


def _manifest(cases: tuple[V2DesignCase, ...], allocation_path: Path,
              design_path: Path) -> dict:
    rows = []
    for item in cases:
        rows.append({
            "candidate_index": item.candidate_index,
            "slot_id": item.slot_id,
            "case_id": item.case.identifier,
            "content_sha256": sha256(canonical(asdict(item))).hexdigest(),
            "family": item.case.family,
            "size": item.case.size,
            "no_disruption_feasible": True,
            "long_outage_length": item.long_outage_length,
            "backup_commitment_periods": (item.contract.backup_commitment_periods
                                          if item.contract.backup_commitment_periods is not None
                                          else "through_horizon"),
            "main_buffer_cover": item.main_buffer_cover,
            "alternate_stock_per_source": item.alternate_stock_per_source,
        })
    return {
        "schema": 1, "status": "public_design_only_no_planner_solves",
        "allocation_sha256": _digest(allocation_path),
        "v1_design_manifest_sha256": _digest(design_path),
        "public_master_seed": PUBLIC_MASTER_SEED,
        "source_sha256": source_hashes(),
        "case_count": len(rows), "candidates": [0, 1, 2],
        "cases": rows, "confirmation_case_access": False,
        "new_mip_solves": 0,
    }


def write_public_design(repository_root: Path, allocation_path: Path,
                        design_path: Path, output_root: Path) -> dict:
    repository_root = repository_root.resolve()
    output_root = output_root.resolve()
    if output_root != repository_root / "configs" / "v2-design":
        raise ValueError("v2 public design output must be configs/v2-design")
    if output_root.exists():
        raise FileExistsError("v2 public design output already exists")
    allocation, design = load_allocation(allocation_path, design_path)
    cases = build_public_design(allocation, design)
    manifest = _manifest(cases, allocation_path, design_path)
    output_root.mkdir(parents=True, exist_ok=False)
    for item in cases:
        path = output_root / "cases" / f"candidate-{item.candidate_index}" / f"{item.case.identifier}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical(asdict(item)))
    (output_root / "manifest.json").write_bytes(canonical(manifest))
    return {"case_count": len(cases), "manifest_sha256": _digest(output_root / "manifest.json"),
            "new_mip_solves": 0}


def verify_public_design(repository_root: Path, allocation_path: Path,
                         design_path: Path, output_root: Path) -> dict:
    repository_root = repository_root.resolve()
    output_root = output_root.resolve()
    if output_root != repository_root / "configs" / "v2-design":
        raise ValueError("v2 public design output must be configs/v2-design")
    allocation, design = load_allocation(allocation_path, design_path)
    cases = build_public_design(allocation, design)
    expected_manifest = canonical(_manifest(cases, allocation_path, design_path))
    manifest_path = output_root / "manifest.json"
    if manifest_path.read_bytes() != expected_manifest:
        raise ValueError("v2 public design manifest differs from regenerated inputs")
    expected_paths = {manifest_path}
    for item in cases:
        path = output_root / "cases" / f"candidate-{item.candidate_index}" / f"{item.case.identifier}.json"
        expected_paths.add(path)
        if path.read_bytes() != canonical(asdict(item)):
            raise ValueError(f"v2 public case content mismatch: {item.case.identifier}")
    if set(output_root.rglob("*.json")) != expected_paths:
        raise ValueError("v2 public design contains missing or extra JSON case files")
    return {"case_count": len(cases), "manifest_sha256": _digest(manifest_path),
            "verified_content_hashes": len(cases), "new_mip_solves": 0}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.networks_v2")
    parser.add_argument("command", choices=("generate-public", "verify-public"))
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--allocation", type=Path, required=True)
    parser.add_argument("--v1-design-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    operation = write_public_design if args.command == "generate-public" else verify_public_design
    print(json.dumps(operation(args.repository_root, args.allocation,
                               args.v1_design_manifest, args.output), sort_keys=True))


if __name__ == "__main__":
    main()
