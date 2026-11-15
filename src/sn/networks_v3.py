"""Versioned v3 *public design* generator; no confirmation or solver entry point.

Generation requires an owner approval receipt naming the exact allocation
hash. The module may build isolated in-memory unit fixtures before approval.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
from random import Random

from .allocation_v3 import (
    HORIZON, PUBLIC_MASTER_SEED, build_allocation, canonical,
    validate_allocation,
)
from .core import Action, Demand, Lane, Notice, Order, Production, Realization, Recipe, Shipment, Stock
from .core_v3 import ContractV3, NetworkV3, initial_state_v3, step_v3


SOURCE_FILES = ("allocation_v3.py", "core_v3.py", "networks_v3.py")


@dataclass(frozen=True)
class V3PublicCase:
    candidate_index: int
    case_id: str
    slot_id: str
    regime: str
    category: str
    family: str
    size: str
    network: NetworkV3
    contract: ContractV3
    short_capacity: tuple[float, ...]
    long_capacity: tuple[float, ...]
    baseline_demand: tuple[tuple[Demand, ...], ...]


def _seed(master: int, slot_id: str) -> int:
    raw = canonical((master, "v3-public-topology", slot_id))
    return int.from_bytes(sha256(raw).digest()[:8], "big")


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def source_hashes() -> dict[str, str]:
    root = Path(__file__).parent
    return {name: _digest(root / name) for name in SOURCE_FILES}


def _topology(row: dict, master_seed: int) -> tuple[NetworkV3, tuple[tuple[Demand, ...], ...]]:
    """Put the critical supplier and qualified backup at the part-use site.

    Other family-specific products and outlets compete for that critical
    part. One pre-horizon accepted order due at zero makes the no-outage
    baseline supply feasible without changing the disruption inventory
    factor. Under an outage it can fail in both truthful and false cells.
    """
    rng = Random(_seed(master_seed, row["slot_id"]))
    family, size = row["family"], row["size"]
    inventory = float(row["inventory_cover"])
    sites = ["buyer"]
    products = ["critical"]
    stock = [Stock("buyer", "critical", inventory)] if inventory else []
    lanes: list[Lane] = []
    recipes: list[Recipe] = []
    demand: list[Demand] = []
    cheap = (0.05, 0.1, 0.15)[rng.randrange(3)]

    if family == "serial":
        if size == "small":
            demand = [Demand("buyer", "critical", 1)]
        else:
            sites.append("retail")
            stock.append(Stock("retail", "critical", 0.25))
            lanes.append(Lane("buyer", "retail", "critical", 1, 0.25, cheap))
            demand = [Demand("buyer", "critical", 0.75),
                      Demand("retail", "critical", 0.25)]
    elif family == "assembly":
        products.extend(("component", "finished"))
        stock.append(Stock("buyer", "component", HORIZON))
        inputs: list[tuple[str, float]] = [("critical", 1), ("component", 1)]
        if size == "large":
            products.append("component2")
            stock.append(Stock("buyer", "component2", HORIZON))
            inputs.append(("component2", 1))
        recipes.append(Recipe("buyer", "finished", tuple(inputs), 1, cheap))
        demand = [Demand("buyer", "finished", 1)]
    elif family == "distribution":
        sites.append("retail0")
        stock.append(Stock("retail0", "critical", 0.25))
        lanes.append(Lane("buyer", "retail0", "critical", 1, 0.25, cheap))
        if size == "small":
            demand = [Demand("buyer", "critical", 0.75),
                      Demand("retail0", "critical", 0.25)]
        else:
            sites.append("retail1")
            stock.append(Stock("retail1", "critical", 0.5))
            lanes.append(Lane("buyer", "retail1", "critical", 2, 0.25, cheap + 0.05))
            demand = [Demand("buyer", "critical", 0.5),
                      Demand("retail0", "critical", 0.25),
                      Demand("retail1", "critical", 0.25)]
    elif family == "layered_dual":
        sites.extend(("hub", "retail"))
        stock.append(Stock("retail", "critical", 0.75))
        lanes.extend((
            Lane("buyer", "retail", "critical", 1, 0.25, cheap + 0.05),
            Lane("buyer", "hub", "critical", 1, 0.25, cheap),
            Lane("hub", "retail", "critical", 1, 0.25, cheap),
        ))
        if size == "large":
            sites.append("hub2")
            lanes.extend((
                Lane("buyer", "hub2", "critical", 1, 0.25, cheap),
                Lane("hub2", "retail", "critical", 1, 0.25, cheap),
            ))
        demand = [Demand("buyer", "critical", 0.5),
                  Demand("retail", "critical", 0.5)]
    elif family == "planted":
        sites.extend(("bypass", "retail"))
        stock.append(Stock("retail", "critical", 1.0))
        lanes.extend((
            Lane("buyer", "retail", "critical", 1, 0.25, cheap + 0.1),
            Lane("buyer", "bypass", "critical", 1, 0.25, cheap),
            Lane("bypass", "retail", "critical", 2, 0.25, cheap),
        ))
        if size == "large":
            sites.append("crossdock")
            lanes.extend((
                Lane("buyer", "crossdock", "critical", 1, 0.25, cheap),
                Lane("crossdock", "retail", "critical", 1, 0.25, cheap),
            ))
        demand = [Demand("buyer", "critical", 0.5),
                  Demand("retail", "critical", 0.5)]
    else:
        raise ValueError("unknown v3 structural family")

    if row["regime"] == "R4":
        count = row["alternate_source_count"]
        quantity = row["alternate_stock_per_source"]
        if count not in (1, 2) or quantity not in (2, 20):
            raise ValueError("invalid R4 ready-source allocation")
        for j in range(count):
            name = f"ready{j}"
            sites.append(name)
            stock.append(Stock(name, "critical", quantity))
            lanes.append(Lane(name, "buyer", "critical", 1, 1, cheap + 0.05))
    elif row["alternate_source_count"] != 0 or row["alternate_stock_per_source"] is not None:
        raise ValueError("ready critical source outside R4")

    network = NetworkV3(
        horizon=HORIZON, sites=tuple(sites), products=tuple(products),
        primary_site="buyer", primary_product="critical",
        initial_stock=tuple(stock), recipes=tuple(recipes), lanes=tuple(lanes),
        critical_site="buyer", critical_product="critical",
        spot_site="buyer" if row["regime"] == "R4" else None,
        spot_product="critical" if row["regime"] == "R4" else None,
        initial_outstanding=(Order(0, 1),),
    )
    if abs(sum(item.quantity for item in demand) - 1) > 1e-9:
        raise AssertionError("v3 baseline demand must be one critical unit per period")
    return network, tuple(tuple(demand) for _ in range(HORIZON))


def build_public_case(row: dict, *, master_seed: int = PUBLIC_MASTER_SEED) -> V3PublicCase:
    """Build one in-memory public case; fixtures may call this before approval."""
    if type(master_seed) is not int or master_seed != PUBLIC_MASTER_SEED:
        raise ValueError("v3 public topology seed differs from proposed allocation")
    if row["case_id"] != f"v3-c{row['candidate_index']}-{row['slot_id']}":
        raise ValueError("v3 case ID differs from allocation row")
    if row["commitment"] not in ("finite", "horizon"):
        raise ValueError("unknown v3 backup commitment")
    network, demand = _topology(row, master_seed)
    short = row["short_duration"]
    long = row["long_duration"]
    contract = ContractV3(
        order_lead=1, revelation_period=short,
        primary_price=1, backup_price=1.25, backup_minimum=1,
        qualification_lead=row["qualification_lead"],
        qualification_cost=row["qualification_fee"],
        backup_commitment_periods=(long if row["commitment"] == "finite" else None),
        spot_capacity=row["spot_capacity"],
        spot_price=float(row["spot_price"]) if row["spot_price"] is not None else 6,
        net_shortage_value=20, demand_rule="lost_sales",
    )
    capacity = lambda duration: tuple(0.0 if t < duration else 1.0
                                      for t in range(HORIZON))
    return V3PublicCase(
        row["candidate_index"], row["case_id"], row["slot_id"],
        row["regime"], row["category"], row["family"], row["size"],
        network, contract, capacity(short), capacity(long), demand,
    )


def no_disruption_feasible(case: V3PublicCase) -> bool:
    """Construct and audit a no-outage physical witness; no planner solve."""
    network = case.network
    state = initial_state_v3(network)
    schedule = (1.0,) * HORIZON
    notice = Notice(schedule, "no-disruption witness")
    family, size = case.family, case.size

    def lane(origin: str, destination: str) -> int:
        matches = [index for index, item in enumerate(network.lanes)
                   if (item.origin, item.destination) == (origin, destination)]
        if len(matches) != 1:
            raise ValueError("v3 no-disruption witness lacks a unique route")
        return matches[0]

    try:
        for t in range(HORIZON):
            shipments = []
            production = ()
            if family == "assembly":
                production = (Production("buyer", "finished", 1),)
            elif family == "serial" and size == "large":
                if t + 1 < HORIZON:
                    shipments.append(Shipment(lane("buyer", "retail"), 0.25))
            elif family == "distribution":
                if t + 1 < HORIZON:
                    shipments.append(Shipment(lane("buyer", "retail0"), 0.25))
                if size == "large" and t + 2 < HORIZON:
                    shipments.append(Shipment(lane("buyer", "retail1"), 0.25))
            elif family == "layered_dual":
                if t + 1 < HORIZON:
                    shipments.append(Shipment(lane("buyer", "retail"), 0.25))
                if t + 2 < HORIZON:
                    shipments.append(Shipment(lane("buyer", "hub"), 0.25))
                if 1 <= t and t + 1 < HORIZON:
                    shipments.append(Shipment(lane("hub", "retail"), 0.25))
            elif family == "planted":
                if t + 1 < HORIZON:
                    shipments.append(Shipment(lane("buyer", "retail"), 0.25))
                if t + 3 < HORIZON:
                    shipments.append(Shipment(lane("buyer", "bypass"), 0.25))
                if 1 <= t and t + 2 < HORIZON:
                    shipments.append(Shipment(lane("bypass", "retail"), 0.25))
            action = Action(primary_order=1 if t + 1 < HORIZON else 0,
                            production=production, shipments=tuple(shipments))
            state, row = step_v3(
                network, case.contract, notice, state, action,
                Realization(1, case.baseline_demand[t]),
                revealed_schedule=schedule if t == case.contract.revelation_period else None,
            )
            if row.lost_sales or row.primary_shortfall or row.backlog:
                return False
    except ValueError:
        return False
    return True


def load_allocation(allocation_path: Path, public_manifest: Path,
                    approved_sha256: str) -> dict:
    raw = allocation_path.read_bytes()
    if len(approved_sha256) != 64 or sha256(raw).hexdigest() != approved_sha256:
        raise ValueError("v3 allocation differs from owner-approved hash")
    allocation = json.loads(raw)
    if canonical(allocation) != raw or allocation != build_allocation(public_manifest):
        raise ValueError("v3 allocation differs from declared public rule")
    validate_allocation(allocation)
    return allocation


def _approved_hash(receipt_path: Path) -> str:
    raw = receipt_path.read_bytes()
    receipt = json.loads(raw)
    if canonical(receipt) != raw:
        raise ValueError("v3 allocation approval receipt is noncanonical")
    digest = receipt.get("allocation_sha256")
    if (not isinstance(digest, str) or len(digest) != 64
            or receipt != {
                "schema": 1, "kind": "v3-allocation-approval",
                "owner_approved": True,
                "scope": "public-case-generation-only",
                "allocation_sha256": digest,
            }):
        raise ValueError("v3 allocation approval receipt lacks exact scope and hash")
    return digest


def _manifest(cases: tuple[V3PublicCase, ...], allocation_path: Path) -> dict:
    return {
        "schema": 1, "status": "public_design_only_no_planner_solves",
        "allocation_sha256": _digest(allocation_path),
        "public_master_seed": PUBLIC_MASTER_SEED,
        "source_sha256": source_hashes(),
        "case_count": len(cases),
        "cases": [{
            "candidate_index": case.candidate_index,
            "case_id": case.case_id,
            "slot_id": case.slot_id,
            "regime": case.regime,
            "category": case.category,
            "family": case.family,
            "size": case.size,
            "content_sha256": sha256(canonical(asdict(case))).hexdigest(),
            "no_disruption_feasible": no_disruption_feasible(case),
        } for case in cases],
        "confirmation_case_access": False,
        "new_mip_solves": 0,
    }


def write_public_design(repository_root: Path, allocation_path: Path,
                        public_manifest: Path, approval_receipt: Path,
                        output_root: Path) -> dict:
    repository_root = repository_root.resolve()
    output_root = output_root.resolve()
    if output_root != repository_root / "configs" / "v3-design":
        raise ValueError("v3 design output must be configs/v3-design")
    if output_root.exists():
        raise FileExistsError("v3 public design output already exists")
    allocation = load_allocation(
        allocation_path, public_manifest, _approved_hash(approval_receipt)
    )
    cases = tuple(build_public_case(row)
                  for candidate in allocation["candidates"]
                  for row in candidate["cases"])
    if len(cases) != 168:
        raise ValueError("v3 public design requires three 56-case rotations")
    manifest = _manifest(cases, allocation_path)
    output_root.mkdir(parents=True, exist_ok=False)
    for case in cases:
        path = output_root / "cases" / f"candidate-{case.candidate_index}" / f"{case.case_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical(asdict(case)))
    (output_root / "manifest.json").write_bytes(canonical(manifest))
    return {"case_count": len(cases),
            "manifest_sha256": _digest(output_root / "manifest.json"),
            "no_disruption_infeasible_case_ids": [row["case_id"] for row in manifest["cases"]
                                                    if not row["no_disruption_feasible"]],
            "new_mip_solves": 0}


def verify_public_design(repository_root: Path, allocation_path: Path,
                         public_manifest: Path, approval_receipt: Path,
                         output_root: Path) -> dict:
    repository_root = repository_root.resolve()
    output_root = output_root.resolve()
    if output_root != repository_root / "configs" / "v3-design":
        raise ValueError("v3 design output must be configs/v3-design")
    allocation = load_allocation(
        allocation_path, public_manifest, _approved_hash(approval_receipt)
    )
    cases = tuple(build_public_case(row)
                  for candidate in allocation["candidates"]
                  for row in candidate["cases"])
    expected_manifest = _manifest(cases, allocation_path)
    expected = canonical(expected_manifest)
    manifest_path = output_root / "manifest.json"
    if manifest_path.read_bytes() != expected:
        raise ValueError("v3 public design manifest differs from regenerated inputs")
    expected_paths = {manifest_path}
    for case in cases:
        path = output_root / "cases" / f"candidate-{case.candidate_index}" / f"{case.case_id}.json"
        expected_paths.add(path)
        if path.read_bytes() != canonical(asdict(case)):
            raise ValueError(f"v3 public case content mismatch: {case.case_id}")
    if set(output_root.rglob("*.json")) != expected_paths:
        raise ValueError("v3 public design contains missing or extra JSON files")
    return {"case_count": len(cases), "manifest_sha256": _digest(manifest_path),
            "verified_content_hashes": len(cases),
            "no_disruption_infeasible_case_ids": [row["case_id"] for row in expected_manifest["cases"]
                                                    if not row["no_disruption_feasible"]],
            "new_mip_solves": 0}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sn.networks_v3")
    parser.add_argument("command", choices=("generate-public", "verify-public"))
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--allocation", type=Path, required=True)
    parser.add_argument("--public-slot-manifest", type=Path, required=True)
    parser.add_argument("--approval-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    operation = write_public_design if args.command == "generate-public" else verify_public_design
    print(json.dumps(operation(args.repository_root, args.allocation,
                               args.public_slot_manifest, args.approval_receipt,
                               args.output), sort_keys=True))


if __name__ == "__main__":
    main()
