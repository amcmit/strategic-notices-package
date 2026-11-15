"""Deterministic structural development networks for the G1 coverage gate.

These are proposed fixtures, not confirmation networks or scientific outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from random import Random

from .core import CoreNetwork, Demand, Lane, Recipe, Stock

FAMILIES = ("serial", "assembly", "distribution", "layered_dual", "planted")
SIZES = ("small", "large")


@dataclass(frozen=True)
class NetworkCase:
    identifier: str
    family: str
    size: str
    replicate: int
    seed: int
    network: CoreNetwork
    demand_site: str
    demand_product: str
    alternative_sources: tuple[str, ...]
    demand_start: int
    secondary_demand_site: str | None = None
    primary_capacity: float = 1.0
    demand_per_period: float = 1.0


def _seed(master: int, *parts: object) -> int:
    raw = json.dumps((master, *parts), separators=(",", ":")).encode()
    return int.from_bytes(sha256(raw).digest()[:8], "big")


def build_case(family: str, size: str, replicate: int, *, seed: int) -> NetworkCase:
    if family not in FAMILIES or size not in SIZES or replicate not in range(4):
        raise ValueError("unknown family, size or replicate")
    rng = Random(seed)
    n = 3 if size == "small" else 5
    spine = [f"stage{i}" for i in range(n)]
    # The slot profile is public and frozen. Private seeds change weights but
    # cannot be selected after observing a confirmation outcome.
    slot = (FAMILIES.index(family) * len(SIZES) + SIZES.index(size)) * 4 + replicate
    position_level = (slot + slot // 4) % 3
    buffer_level = (slot // 3 + slot) % 3
    position = (0, n // 2, n - 1)[position_level]
    primary = spine[position]
    demand_site = spine[-1]
    assembly = family == "assembly"
    primary_product = "raw" if assembly else "unit"
    demand_product = "finished" if assembly else "unit"
    products = ("raw", "component", "finished") if assembly else ("unit",)
    sites = list(spine)
    lanes: list[Lane] = []
    for index in range(n - 1):
        lanes.append(Lane(spine[index], spine[index + 1], primary_product,
                          lead=1, capacity=2, unit_cost=rng.choice((0.1, 0.2))))

    alternatives = []
    stocks: list[Stock] = [Stock(demand_site, demand_product, (0, 2, 4)[buffer_level])]
    alt_count = 0 if family == "serial" else (slot // 3 + 2 * slot) % 3
    high_slack = bool((slot // 3) % 2 == 0)
    alt_capacity = (1.5 if high_slack else 0.5) if alt_count == 1 else \
        (1.0 if high_slack else 0.5)
    for j in range(alt_count):
        alt = f"alternate{j}"
        sites.append(alt)
        alternatives.append(alt)
        stocks.append(Stock(alt, primary_product, 20))
        lanes.append(Lane(alt, demand_site, primary_product,
                          lead=1, capacity=alt_capacity, unit_cost=0.3))

    recipes: list[Recipe] = []
    if assembly:
        stocks.append(Stock(demand_site, "component", 20))
        recipes.append(Recipe(demand_site, "finished",
                              (("raw", 1), ("component", 1)), capacity=2,
                              unit_cost=0.2))
        # A distinct convergent component route is needed for assembly.
        component_source = "component_source"
        sites.append(component_source)
        stocks.append(Stock(component_source, "component", 10))
        lanes.append(Lane(component_source, demand_site, "component",
                          lead=1, capacity=1, unit_cost=0.2))
    elif family == "distribution":
        side = "other_store"
        sites.append(side)
        lanes.append(Lane(demand_site, side, "unit", lead=1, capacity=1, unit_cost=0.2))
    elif family == "layered_dual":
        side = "parallel_tier"
        sites.append(side)
        lanes.extend((Lane(spine[0], side, "unit", 1, 1, 0.2),
                      Lane(side, demand_site, "unit", 2, 1, 0.2)))
    elif family == "planted":
        side = "bypass"
        sites.append(side)
        lanes.extend((Lane(spine[0], side, "unit", 1, 0.5, 0.2),
                      Lane(side, demand_site, "unit", 2, 0.5, 0.2)))

    network = CoreNetwork(
        horizon=10, sites=tuple(sites), products=products,
        primary_site=primary, primary_product=primary_product,
        initial_stock=tuple(stocks), recipes=tuple(recipes), lanes=tuple(lanes),
    )
    return NetworkCase(
        f"{family}-{size}-{replicate}", family, size, replicate, seed, network,
        demand_site, demand_product, tuple(alternatives), n + 1,
        "other_store" if family == "distribution" else None,
    )


def _path_features(case: NetworkCase) -> tuple[int, int]:
    graph: dict[str, list[tuple[str, int]]] = {}
    for lane in case.network.lanes:
        graph.setdefault(lane.origin, []).append((lane.destination, lane.lead))
    # The staged networks are DAGs. Enumerating simple paths avoids depending
    # on a graph package, and provides a direct check that the primary reaches
    # the demand site.
    paths: list[tuple[int, int]] = []

    def visit(site: str, seen: frozenset[str], hops: int, lead: int) -> None:
        if site == case.demand_site:
            paths.append((hops, lead))
            return
        for destination, duration in graph.get(site, []):
            if destination not in seen:
                visit(destination, seen | {destination}, hops + 1, lead + duration)

    visit(case.network.primary_site, frozenset({case.network.primary_site}), 0, 0)
    if not paths:
        raise ValueError("supplier cannot reach demand")
    return min(paths, key=lambda item: item[1])


def numeric_features(case: NetworkCase) -> dict[str, float]:
    _, lead = _path_features(case)
    spine_depth = 2 if case.size == "small" else 4
    position = int(case.network.primary_site.removeprefix("stage")) / spine_depth
    buffer = sum(item.quantity for item in case.network.initial_stock
                 if item.site == case.demand_site and item.product == case.demand_product)
    alt_slack = sum(lane.capacity for lane in case.network.lanes
                    if lane.origin in case.alternative_sources
                    and lane.product == case.network.primary_product)
    return {
        "substitutability": 1 + len(case.alternative_sources),
        "position": position,
        "buffer": buffer / case.demand_per_period,
        "slack": alt_slack / case.primary_capacity,
        "lead_time": lead,
    }


def feature_levels(case: NetworkCase) -> dict[str, str]:
    f = numeric_features(case)
    return {
        "substitutability": ("sole", "dual", "multi")[int(f["substitutability"]) - 1],
        "position": "upstream" if f["position"] < 1 / 3 else
                    "downstream" if f["position"] > 2 / 3 else "middle",
        "buffer": "low" if f["buffer"] < 1 else
                  "high" if f["buffer"] > 3 else "middle",
        "slack": "high" if f["slack"] > 1 else "low",
        "lead_time": "short" if f["lead_time"] <= 1 else "long",
    }


def design_manifest(master_seed: int) -> tuple[NetworkCase, ...]:
    """Exactly 24 public development cases across ten family-size strata."""
    cases = []
    for stratum, (family, size) in enumerate((f, s) for f in FAMILIES for s in SIZES):
        count = 3 if stratum in (0, 3, 6, 9) else 2
        for replicate in range(count):
            cases.append(build_case(family, size, replicate,
                                    seed=_seed(master_seed, family, size, replicate)))
    return tuple(cases)


def case_demand_schedule(case: NetworkCase) -> tuple[tuple[Demand, ...], ...]:
    """Deterministic baseline demand path for a synthetic case."""
    periods = []
    for t in range(case.network.horizon):
        demand = []
        if t >= case.demand_start:
            main_quantity = 0.75 if case.secondary_demand_site else 1.0
            demand.append(Demand(case.demand_site, case.demand_product,
                                 main_quantity * case.demand_per_period))
        if case.secondary_demand_site is not None and t >= case.demand_start + 1:
            demand.append(Demand(case.secondary_demand_site, case.demand_product,
                                 0.25 * case.demand_per_period))
        periods.append(tuple(demand))
    return tuple(periods)
