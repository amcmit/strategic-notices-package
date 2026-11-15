"""All-case private-file integrity gate for an owner-approved v3 opening.

Importing this module does not inspect a private volume. The caller must
provide its mounted path after approval; no seed is read or reconstructed.
"""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from stat import S_ISREG

from .allocation_v3 import canonical
from .confirmation_structure_v3 import (
    LANE_COSTS, LANE_LEADS, ORDINARY_CAPACITIES, READY_CAPACITIES,
    design_structure_digests, no_disruption_feasible, structure_digest,
)
from .confirmation_v3 import FACTOR_KEYS, _selected_rows
from .confirmation_v3_r2 import _summary, _validate_summary, verify_metadata
from .core import Demand, Lane, Order, Recipe, Stock
from .core_v3 import ContractV3, NetworkV3
from .networks_v3 import V3PublicCase, build_public_case


PUBLIC_SHA256 = "c6c9056671382246ea5ad56855731f9f5f054f628ba01faf198ce14758721bab"


def decode_case(value: dict) -> V3PublicCase:
    """Decode only the frozen case schema, preserving tuple semantics."""
    if set(value) != set(V3PublicCase.__dataclass_fields__):
        raise ValueError("private case schema differs")
    n = value["network"]
    if set(n) != set(NetworkV3.__dataclass_fields__):
        raise ValueError("private network schema differs")
    network = NetworkV3(
        horizon=n["horizon"], sites=tuple(n["sites"]), products=tuple(n["products"]),
        primary_site=n["primary_site"], primary_product=n["primary_product"],
        initial_stock=tuple(Stock(**x) for x in n["initial_stock"]),
        recipes=tuple(Recipe(x["site"], x["output"], tuple(tuple(y) for y in x["inputs"]),
                             x["capacity"], x["unit_cost"]) for x in n["recipes"]),
        lanes=tuple(Lane(**x) for x in n["lanes"]),
        critical_site=n["critical_site"], critical_product=n["critical_product"],
        spot_site=n["spot_site"], spot_product=n["spot_product"],
        initial_outstanding=tuple(Order(**x) for x in n["initial_outstanding"]),
    )
    if set(value["contract"]) != set(ContractV3.__dataclass_fields__):
        raise ValueError("private contract schema differs")
    return V3PublicCase(
        value["candidate_index"], value["case_id"], value["slot_id"],
        value["regime"], value["category"], value["family"], value["size"],
        network, ContractV3(**value["contract"]),
        tuple(value["short_capacity"]), tuple(value["long_capacity"]),
        tuple(tuple(Demand(**x) for x in period) for period in value["baseline_demand"]),
    )


def _check_structure(case: V3PublicCase, allocation_row: dict, metadata_row: dict) -> None:
    """Check the actual case, rather than trusting its public summary."""
    base = build_public_case(allocation_row)
    network, template = case.network, base.network
    if (case.contract != base.contract or case.short_capacity != base.short_capacity
            or case.long_capacity != base.long_capacity or case.candidate_index != 0
            or case.case_id != metadata_row["case_id"]
            or case.slot_id != metadata_row["slot_id"]
            or (case.regime, case.category, case.family, case.size) !=
               (metadata_row["regime"], metadata_row["category"],
                metadata_row["family"], metadata_row["size"])):
        raise ValueError("private allocated economics or identifiers differ")
    if (network.horizon != template.horizon or network.products != template.products
            or network.primary_site != template.primary_site
            or network.primary_product != template.primary_product
            or network.critical_site != template.critical_site
            or network.critical_product != template.critical_product
            or network.spot_site != template.spot_site
            or network.spot_product != template.spot_product
            or network.initial_outstanding != template.initial_outstanding
            or [(r.site, r.output, r.inputs, r.capacity) for r in network.recipes] !=
               [(r.site, r.output, r.inputs, r.capacity) for r in template.recipes]
            or any(r.unit_cost not in LANE_COSTS for r in network.recipes)):
        raise ValueError("private fixed network fields differ")
    sites = list(network.sites)
    if sites[:len(template.sites)] != list(template.sites):
        raise ValueError("private family template sites differ")
    extra_sites = sites[len(template.sites):]
    outlets = [name for name in extra_sites if name.startswith("outlet") or name == "retail"]
    hubs = [name for name in extra_sites if name.startswith("transit_extra")]
    if (extra_sites != outlets + hubs or
            outlets != (["retail"] if case.family == "serial" and outlets else
                        [f"outlet{j}" for j in range(len(outlets))]) or
            hubs != [f"transit_extra{j}" for j in range(len(hubs))]):
        raise ValueError("private additional site names or order differ")
    stock = {(x.site, x.product): x.quantity for x in network.initial_stock}
    template_stock = {(x.site, x.product): x.quantity for x in template.initial_stock}
    if (len(stock) != len(network.initial_stock)
            or any(stock.get(key) != value for key, value in template_stock.items())
            or set(stock) != set(template_stock) | {(name, "finished" if case.family == "assembly"
                                                     else "critical") for name in outlets}):
        raise ValueError("private fixed inventory differs")
    if any(stock[(name, "finished" if case.family == "assembly" else "critical")]
           not in (0.25, 0.5) for name in outlets):
        raise ValueError("private additional outlet stock outside range")
    if len(network.lanes) != len(template.lanes) + len(outlets) + 2 * len(hubs):
        raise ValueError("private lane count differs from declared structure")
    expected_edges = [(x.origin, x.destination, x.product) for x in template.lanes]
    product = "finished" if case.family == "assembly" else "critical"
    expected_edges += [("buyer", name, product) for name in outlets]
    if [(x.origin, x.destination, x.product) for x in network.lanes[:len(expected_edges)]] != expected_edges:
        raise ValueError("private family or new-outlet routing differs")
    targets = {x.site for x in case.baseline_demand[0] if x.site != "buyer"}
    for j, hub in enumerate(hubs):
        first, second = network.lanes[len(expected_edges) + 2*j:len(expected_edges) + 2*j + 2]
        if ((first.origin, first.destination, first.product) != ("buyer", hub, product)
                or second.origin != hub or second.destination not in targets
                or second.product != product):
            raise ValueError("private extra transit route differs")
    for lane in network.lanes:
        allowed = READY_CAPACITIES if lane.origin.startswith("ready") else ORDINARY_CAPACITIES
        if (lane.lead not in LANE_LEADS or lane.lead > case.contract.qualification_lead
                or lane.capacity not in allowed or lane.unit_cost not in LANE_COSTS):
            raise ValueError("private lane lead, capacity or cost outside range")
    if len(case.baseline_demand) != network.horizon or any(
            period != case.baseline_demand[0] for period in case.baseline_demand):
        raise ValueError("private demand path differs from stationary multiplier 1.0")
    demand = case.baseline_demand[0]
    quantities = {x.site: x.quantity for x in demand}
    expected_demand_sites = {x.site for x in base.baseline_demand[0]} | set(outlets)
    if (len({(x.site, x.product) for x in demand}) != len(demand)
            or sum(x.quantity for x in demand) != 1.0
            or any(x.product != product or x.site not in network.sites for x in demand)
            or set(quantities) != expected_demand_sites
            or any(quantities[name] not in (0.125, 0.25) for name in outlets)
            or (case.family in ("serial", "distribution") and any(
                quantities[x.site] not in (0.125, 0.25)
                for x in base.baseline_demand[0] if x.site != "buyer"))
            or (case.family in ("layered_dual", "planted") and
                quantities["retail"] not in (0.375, 0.5, 0.625))):
        raise ValueError("private demand shape differs")
    summary = _summary(case)
    if summary != metadata_row["structure_summary"]:
        raise ValueError("private structural summary differs from public declaration")
    _validate_summary(metadata_row)
    if not no_disruption_feasible(case):
        raise ValueError("private no-disruption witness is infeasible")


def audit_private_set(root: Path, private_root: Path, metadata: dict) -> dict[str, V3PublicCase]:
    """Bind approved public metadata, then audit *all* 56 private cases."""
    root = root.resolve()
    public_path = root / "configs/v3-confirmation-public.json"
    if (verify_metadata(root, public_path) != PUBLIC_SHA256
            or metadata != json.loads(public_path.read_bytes())):
        raise ValueError("public metadata or approved public hash differs")
    return audit_case_set(root, private_root, metadata)


def audit_case_set(root: Path, private_root: Path, metadata: dict) -> dict[str, V3PublicCase]:
    """Pure case-set audit also accepts public-seed test fixtures."""
    root, private_root = root.resolve(), private_root.expanduser()
    if (private_root.is_symlink() or not private_root.is_dir()
            or private_root.resolve().is_relative_to(root)):
        raise ValueError("private case directory must be a mounted external directory")
    rows = _selected_rows(root)
    if len(rows) != 56 or len(metadata["cases"]) != 56:
        raise ValueError("confirmation allocation count differs")
    expected_names = {item["case_id"] + ".json" for item in metadata["cases"]}
    if {x.name for x in private_root.iterdir()} != expected_names:
        raise ValueError("private file set has a missing or extra case")
    design_signatures = design_structure_digests(root)
    cases: dict[str, V3PublicCase] = {}
    signatures: set[str] = set()
    for row, public in zip(rows, metadata["cases"]):
        path = private_root / (public["case_id"] + ".json")
        if path.is_symlink() or not S_ISREG(path.stat().st_mode):
            raise ValueError(f"private case is not a regular file: {public['case_id']}")
        raw = path.read_bytes()
        value = json.loads(raw)
        if raw != canonical(value) or sha256(raw).hexdigest() != public["content_sha256"]:
            raise ValueError(f"private case content hash differs: {public['case_id']}")
        case = decode_case(value)
        if canonical(asdict(case)) != raw:
            raise ValueError(f"private case decoded form differs: {public['case_id']}")
        if public["factors"] != {key: row[key] for key in FACTOR_KEYS}:
            raise ValueError(f"private factor bundle differs: {public['case_id']}")
        _check_structure(case, row, public)
        signature = structure_digest(case)
        if (signature != public["structure_sha256"] or signature in design_signatures
                or signature in signatures):
            raise ValueError(f"private structural signature differs or duplicates: {public['case_id']}")
        signatures.add(signature)
        cases[case.case_id] = case
    return cases
