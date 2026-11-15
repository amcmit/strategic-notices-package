"""Public-seed-testable structural draws for owner-held v3 cases.

The frozen allocation controls economics and the buyer critical-part buffer.
This module varies nuisance network structure only. It performs no planner MIP.
"""

from __future__ import annotations

from dataclasses import asdict, replace
from hashlib import sha256
from itertools import product
import json
from pathlib import Path
from random import Random

from .allocation_v3 import HORIZON, canonical
from .core import Action, Demand, Lane, Notice, Production, Realization, Shipment, Stock
from .core_v3 import initial_state_v3, step_v3
from .networks_v3 import V3PublicCase


GRID = 0.125
ORDINARY_CAPACITIES = (0.25, 0.375, 0.5, 0.625, 0.75)
READY_CAPACITIES = (0.75, 1.0, 1.25)
LANE_LEADS = (1, 2)
LANE_COSTS = (0.05, 0.1, 0.15, 0.2, 0.25)
ROUTE_LIMIT = 4


def _normalized(value):
    """Treat float roundoff as the same physical structure."""
    if isinstance(value, float):
        return round(value, 8)
    if isinstance(value, dict):
        return {key: _normalized(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalized(item) for item in value]
    return value


def structure_digest(case: V3PublicCase) -> str:
    """Physical network and demand signature, excluding ID and contract text."""
    return sha256(canonical(_normalized({
        "network": asdict(case.network),
        "demand": [asdict(x) for x in case.baseline_demand[0]],
    }))).hexdigest()


def design_structure_digests(root: Path) -> set[str]:
    """Read all 168 frozen public cases; never generate new cases."""
    design = root / "configs/v3-design"
    saved = json.loads((design / "manifest.json").read_bytes())
    if len(saved["cases"]) != 168:
        raise ValueError("frozen public design is incomplete")
    result = set()
    for row in saved["cases"]:
        path = design / "cases" / f"candidate-{row['candidate_index']}" / f"{row['case_id']}.json"
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != row["content_sha256"]:
            raise ValueError("public design case hash changed")
        value = json.loads(raw)
        signature = sha256(canonical(_normalized({
            "network": value["network"],
            "demand": value["baseline_demand"][0],
        }))).hexdigest()
        result.add(signature)
    return result


def _rng(master: int, slot_id: str, attempt: int) -> Random:
    return Random(int.from_bytes(sha256(canonical((
        master, "v3-structural-draw", slot_id, attempt))).digest(), "big"))


def _draw_case(base: V3PublicCase, row: dict, master: int, attempt: int) -> V3PublicCase:
    rng = _rng(master, row["slot_id"], attempt)
    net = base.network
    sites = list(net.sites)
    stocks = list(net.initial_stock)
    lanes = []
    for lane in net.lanes:
        ready = lane.origin.startswith("ready")
        lanes.append(replace(
            lane, lead=rng.choice(LANE_LEADS),
            capacity=rng.choice(READY_CAPACITIES if ready else ORDINARY_CAPACITIES),
            unit_cost=rng.choice(LANE_COSTS),
        ))
    family, size = row["family"], row["size"]
    product_name = "finished" if family == "assembly" else "critical"
    demand = {(item.site, item.product): item.quantity
              for item in base.baseline_demand[0]}

    def add_outlet(name: str) -> None:
        sites.append(name)
        quantity = rng.choice((0.125, 0.25))
        demand[("buyer", product_name)] -= quantity
        demand[(name, product_name)] = quantity
        stocks.append(Stock(name, product_name, rng.choice((0.25, 0.5))))
        lanes.append(Lane("buyer", name, product_name, rng.choice(LANE_LEADS),
                          rng.choice(ORDINARY_CAPACITIES), rng.choice(LANE_COSTS)))

    if family == "serial":
        if size == "small" and rng.randrange(2):
            add_outlet("retail")
        elif size == "large":
            quantity = rng.choice((0.125, 0.25))
            demand[("buyer", "critical")] = 1.0 - quantity
            demand[("retail", "critical")] = quantity
    elif family == "assembly":
        number = rng.randrange(2 if size == "small" else 3)
        for j in range(number):
            add_outlet(f"outlet{j}")
    elif family == "distribution":
        targets = ["retail0"] + (["retail1"] if size == "large" else [])
        for site in targets:
            demand[(site, "critical")] = rng.choice((0.125, 0.25))
        demand[("buyer", "critical")] = 1.0 - sum(
            demand[(site, "critical")] for site in targets)
    elif family in ("layered_dual", "planted"):
        quantity = rng.choice((0.375, 0.5, 0.625))
        demand[("buyer", "critical")] = 1.0 - quantity
        demand[("retail", "critical")] = quantity
    else:
        raise ValueError("unknown v3 family")

    outlets = [site for (site, product) in demand
               if site != "buyer" and demand[(site, product)] > 0]
    if outlets:
        maximum_extra = 1 if size == "small" else 2
        for j in range(rng.randrange(maximum_extra + 1)):
            hub = f"transit_extra{j}"
            sites.append(hub)
            target = rng.choice(outlets)
            lanes.extend((
                Lane("buyer", hub, product_name, rng.choice(LANE_LEADS),
                     rng.choice(ORDINARY_CAPACITIES), rng.choice(LANE_COSTS)),
                Lane(hub, target, product_name, rng.choice(LANE_LEADS),
                     rng.choice(ORDINARY_CAPACITIES), rng.choice(LANE_COSTS)),
            ))
    if any(lane.lead > row["qualification_lead"] for lane in lanes):
        raise ValueError("transport lane lead exceeds fixed critical-site lead")
    changed = replace(net, sites=tuple(sites), initial_stock=tuple(stocks),
                      lanes=tuple(lanes))
    period_demand = tuple(Demand(site, product, amount)
                          for (site, product), amount in sorted(demand.items())
                          if amount > 0)
    if abs(sum(item.quantity for item in period_demand) - 1.0) > 1e-9:
        raise ValueError("drawn demand split does not total one")
    return replace(base, network=changed,
                   baseline_demand=tuple(period_demand for _ in range(HORIZON)))


def _paths(case: V3PublicCase, target: str, product_name: str) -> list[tuple[int, ...]]:
    lanes = case.network.lanes
    found = []

    def visit(site: str, seen: frozenset[str], path: tuple[int, ...]) -> None:
        if site == target:
            found.append(path)
            return
        if len(path) >= ROUTE_LIMIT:
            return
        for index, lane in enumerate(lanes):
            if (lane.origin == site and lane.product == product_name
                    and lane.destination not in seen):
                visit(lane.destination, seen | {lane.destination}, path + (index,))

    visit("buyer", frozenset({"buyer"}), ())
    return sorted(found, key=lambda path: (sum(lanes[i].lead for i in path), path))


def _compositions(total: int, count: int):
    if count == 1:
        yield (total,)
    else:
        for first in range(total + 1):
            for rest in _compositions(total - first, count - 1):
                yield (first,) + rest


def _route_plan(case: V3PublicCase) -> list[tuple[tuple[int, ...], float]] | None:
    """Enumerate quarter/eighth-unit stationary route flows, no optimizer."""
    net = case.network
    demand = [item for item in case.baseline_demand[0] if item.site != "buyer"]
    stock = {(item.site, item.product): item.quantity for item in net.initial_stock}
    choices = []
    for item in demand:
        paths = _paths(case, item.site, item.product)
        if not paths:
            return None
        total = round(item.quantity / GRID)
        if abs(total * GRID - item.quantity) > 1e-9:
            return None
        options = []
        for units in _compositions(total, len(paths)):
            path_flows = [(path, amount * GRID)
                          for path, amount in zip(paths, units) if amount]
            max_lead = max(sum(net.lanes[i].lead for i in path)
                           for path, _ in path_flows)
            if all(stock.get((item.site, item.product), 0.0)
                   + sum(flow * max(0, t + 1 - sum(net.lanes[i].lead for i in path))
                         for path, flow in path_flows)
                   >= item.quantity * (t + 1) - 1e-9
                   for t in range(max_lead)):
                options.append(path_flows)
        if not options:
            return None
        options.sort(key=lambda flows: (
            sum(flow * sum(net.lanes[i].lead for i in path) for path, flow in flows),
            tuple((path, flow) for path, flow in flows)))
        choices.append(options)
    for combination in product(*choices):
        flows = [row for group in combination for row in group]
        usage = {index: sum(flow for path, flow in flows if index in path)
                 for index in range(len(net.lanes))}
        if all(usage[index] <= lane.capacity + 1e-9
               for index, lane in enumerate(net.lanes)):
            return flows
    return None


def no_disruption_feasible(case: V3PublicCase) -> bool:
    """Construct and replay a full-horizon no-outage witness, with no MIP."""
    flows = _route_plan(case)
    if flows is None:
        return False
    shipments = [dict() for _ in range(HORIZON)]
    for path, flow in flows:
        total_lead = sum(case.network.lanes[i].lead for i in path)
        for start in range(HORIZON - total_lead):
            elapsed = 0
            for index in path:
                period = start + elapsed
                shipments[period][index] = shipments[period].get(index, 0.0) + flow
                elapsed += case.network.lanes[index].lead
    schedule = (1.0,) * HORIZON
    notice = Notice(schedule, "public-seed no-disruption witness")
    state = initial_state_v3(case.network)
    try:
        for t in range(HORIZON):
            action = Action(
                primary_order=1 if t + 1 < HORIZON else 0,
                production=(Production("buyer", "finished", 1),)
                if case.family == "assembly" else (),
                shipments=tuple(Shipment(index, quantity)
                                for index, quantity in sorted(shipments[t].items())),
            )
            state, ledger = step_v3(
                case.network, case.contract, notice, state, action,
                Realization(1, case.baseline_demand[t]),
                revealed_schedule=schedule if t == case.contract.revelation_period else None,
            )
            if (ledger.lost_sales or ledger.primary_shortfall or ledger.backlog):
                return False
    except ValueError:
        return False
    return True


def draw_novel_case(base: V3PublicCase, row: dict, master: int,
                    design_signatures: set[str], accepted: set[str],
                    *, maximum_attempts: int = 64) -> tuple[V3PublicCase, int]:
    """Reject infeasible/duplicate draws; stop after a fixed attempt cap."""
    for attempt in range(maximum_attempts):
        candidate = _draw_case(base, row, master, attempt)
        signature = structure_digest(candidate)
        if signature in design_signatures or signature in accepted:
            continue
        if not no_disruption_feasible(candidate):
            continue
        accepted.add(signature)
        return candidate, attempt + 1
    raise ValueError(f"no feasible novel structure within {maximum_attempts} attempts: {row['case_id']}")
