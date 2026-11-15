"""One declared projection boundary for v3 single-solve executed actions.

The planner validates the projected action with the physical core, then the
rolling transition executes that same action. Physical capacities are never
raised. Raw solver values and correction magnitudes remain available to the
engineering record.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import inf, isfinite

from .core import Action, Production, Shipment
from .core_v3 import ContractV3, NetworkV3


PRIMAL_FEASIBILITY_TOLERANCE = 1e-7
MIP_FEASIBILITY_TOLERANCE = 1e-8
HANDOFF_MULTIPLE = 2.0
MAX_PROJECTION = HANDOFF_MULTIPLE * max(
    PRIMAL_FEASIBILITY_TOLERANCE, MIP_FEASIBILITY_TOLERANCE)


@dataclass(frozen=True)
class Correction:
    coordinate: str
    raw: float
    projected: float

    @property
    def absolute_size(self) -> float:
        return abs(self.projected - self.raw)


@dataclass(frozen=True)
class Handoff:
    action: Action
    corrections: tuple[Correction, ...]

    @property
    def max_correction(self) -> float:
        return max((item.absolute_size for item in self.corrections), default=0.0)

    @property
    def correction_l1(self) -> float:
        return sum(item.absolute_size for item in self.corrections)


def project_action(
    network: NetworkV3, contract: ContractV3, period: int,
    raw: Action, *, accepted_order_limit: float,
) -> Handoff:
    """Clip only small bound violations; reject all larger violations.

    This applies only to continuous executed controls. Coupled inventory and
    assembly feasibility remain hard checks in the physical core.
    """
    if type(raw.qualify) is not bool:
        raise ValueError("qualification must be a binary action")
    if len(raw.production) != len(network.recipes) or len(raw.shipments) != len(network.lanes):
        raise ValueError("raw controls must include one entry per recipe and lane")
    corrections: list[Correction] = []

    def clip(name: str, value: float, upper: float) -> float:
        if not isfinite(value) or upper < 0 or (not isfinite(upper) and upper != inf):
            raise ValueError(f"invalid hand-off bound or control: {name}")
        if value < -MAX_PROJECTION or value > upper + MAX_PROJECTION:
            raise ValueError(
                f"hand-off violation exceeds {MAX_PROJECTION:g}: {name}; "
                f"raw={value!r}, upper={upper!r}")
        projected = min(max(value, 0.0), upper)
        if projected != value:
            corrections.append(Correction(name, value, projected))
        return projected

    order = clip("primary_order", raw.primary_order, accepted_order_limit)
    spot = clip("spot_order", raw.spot_order, contract.spot_capacity)
    production = []
    for index, (control, recipe) in enumerate(zip(raw.production, network.recipes)):
        if (control.site, control.product) != (recipe.site, recipe.output):
            raise ValueError("raw production order differs from network recipes")
        value = clip(f"production/{index}", control.quantity, recipe.capacity)
        if value > 0:
            production.append(Production(recipe.site, recipe.output, value))
    shipments = []
    for index, (control, lane) in enumerate(zip(raw.shipments, network.lanes)):
        if control.lane_index != index:
            raise ValueError("raw shipment order differs from network lanes")
        upper = lane.capacity if period + lane.lead < network.horizon else 0.0
        value = clip(f"shipment/{index}", control.quantity, upper)
        if value > 0:
            shipments.append(Shipment(index, value))
    return Handoff(
        Action(raw.qualify, order, spot, tuple(production), tuple(shipments)),
        tuple(corrections),
    )
