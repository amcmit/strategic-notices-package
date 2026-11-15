"""Six-period, hand-solvable G0 contract and planner oracle.

This is a deliberately small literal-belief policy, not the later scenario-tree
planner or a scientific equilibrium solver. A notice has a binding schedule and
separate text. The planner chooses its initial qualification date by enumerating
all dates, then the physical loop executes accepted purchase orders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Type = Literal["short", "long"]
NoticeName = Literal["minor", "major"]


@dataclass(frozen=True)
class ToyNotice:
    name: NoticeName
    announced_recovery: int
    text: str = field(compare=False)

    @classmethod
    def named(cls, name: NoticeName, text: str | None = None) -> ToyNotice:
        if name not in ("minor", "major"):
            raise ValueError("unknown notice")
        return cls(name, 2 if name == "minor" else 4, text or name)


@dataclass(frozen=True)
class ToyContract:
    horizon: int = 6
    onset: int = 1
    order_lead: int = 1
    qualification_lead: int = 2
    qualification_cost: int = 2
    primary_price: int = 1
    backup_price: int = 2
    spot_price: int = 6
    demand: int = 1
    initial_inventory: int = 1
    capacity: int = 1
    backup_minimum: int = 1

    def __post_init__(self) -> None:
        if (
            self.horizon != 6 or self.onset != 1 or self.order_lead != 1
            or self.qualification_lead != 2 or self.demand != 1
            or self.initial_inventory != 1 or self.capacity != 1
            or self.backup_minimum != 1
        ):
            raise ValueError("M0b oracle only supports its frozen six-period topology")
        if min(self.qualification_cost, self.primary_price, self.backup_price, self.spot_price) < 0:
            raise ValueError("costs must be nonnegative")
        if not self.primary_price < self.backup_price < self.spot_price:
            raise ValueError("the oracle assumes primary < backup < spot")


@dataclass(frozen=True)
class PeriodLedger:
    period: int
    initial_inventory: int
    primary_accepted: int
    primary_delivered: int
    primary_shortfall: int
    backup_delivered: int
    spot: int
    new_primary_commitment: int
    operating_cost: int


@dataclass(frozen=True)
class ToyOutcome:
    actual_type: Type
    notice_name: NoticeName
    qualification_at: int | None
    periods: tuple[PeriodLedger, ...]

    @property
    def planner_cost(self) -> int:
        return sum(row.operating_cost for row in self.periods)


def qualification_cost_for(
    notice: ToyNotice, contract: ToyContract, qualify_at: int | None
) -> int:
    """Independent full-date enumeration under literal announced recovery.

    Initial inventory covers period 0. From period 1, one unit of demand is
    supplied from backup when its minimum is active, else primary after the
    announced recovery, else spot. This static oracle is independent of the
    order/commitment execution loop below.
    """
    if qualify_at is not None and not 0 <= qualify_at < contract.horizon:
        raise ValueError("qualification date outside horizon")
    total = contract.qualification_cost if qualify_at is not None else 0
    for period in range(1, contract.horizon):
        if qualify_at is not None and period >= qualify_at + contract.qualification_lead:
            total += contract.backup_price
        elif period >= notice.announced_recovery:
            total += contract.primary_price
        else:
            total += contract.spot_price
    return total


def enumerate_qualification(notice: ToyNotice, contract: ToyContract) -> dict[int | None, int]:
    return {date: qualification_cost_for(notice, contract, date)
            for date in (None, *range(contract.horizon))}


def literal_qualification(notice: ToyNotice, contract: ToyContract) -> int | None:
    costs = enumerate_qualification(notice, contract)
    # Prefer no qualification on exact cost ties, then the earliest date.
    return min(costs, key=lambda date: (costs[date], date is not None, date or 0))


def simulate_toy(actual_type: Type, notice: ToyNotice, contract: ToyContract) -> ToyOutcome:
    if actual_type not in ("short", "long"):
        raise ValueError("unknown duration type")
    if (notice.name, notice.announced_recovery) not in (("minor", 2), ("major", 4)):
        raise ValueError("toy notices require their canonical binding schedules")

    actual_recovery = 2 if actual_type == "short" else 4
    qualify_at = literal_qualification(notice, contract)
    outstanding: dict[int, int] = {}
    inventory = contract.initial_inventory
    records: list[PeriodLedger] = []

    for period in range(contract.horizon):
        due = outstanding.pop(period, 0)
        realized_capacity = 0 if contract.onset <= period < actual_recovery else contract.capacity
        primary_delivered = min(due, realized_capacity)
        shortfall = due - primary_delivered
        backup = int(qualify_at is not None and period >= qualify_at + contract.qualification_lead)
        backup *= contract.backup_minimum
        from_inventory = min(inventory, max(0, contract.demand - backup - primary_delivered))
        inventory -= from_inventory
        spot = contract.demand - backup - primary_delivered - from_inventory
        if spot < 0:
            raise AssertionError("mandatory backup exceeds demand in this oracle")
        charge = (
            contract.primary_price * primary_delivered
            + contract.backup_price * backup
            + contract.spot_price * spot
            + (contract.qualification_cost if period == qualify_at else 0)
        )

        # Capacity is publicly observed at the start of the period. The first
        # diagnostic difference is period 2. Existing commitments still settle.
        known_recovery = actual_recovery if period >= 2 else notice.announced_recovery
        delivery_date = period + contract.order_lead
        new_order = 0
        backup_due_next = (
            qualify_at is not None
            and delivery_date >= qualify_at + contract.qualification_lead
        )
        if (
            delivery_date < contract.horizon
            and not backup_due_next
            and delivery_date >= known_recovery
        ):
            # The one-unit per-date capacity is shared by all outstanding
            # purchase orders. The toy has one order opportunity per period.
            new_order = min(contract.demand, contract.capacity - outstanding.get(delivery_date, 0))
            outstanding[delivery_date] = outstanding.get(delivery_date, 0) + new_order

        records.append(PeriodLedger(
            period, from_inventory, due, primary_delivered, shortfall,
            backup, spot, new_order, charge,
        ))

    return ToyOutcome(actual_type, notice.name, qualify_at, tuple(records))


def supplier_payoff(
    outcome: ToyOutcome, *, margin: float, penalty: float, continuation: float = 0,
    signal_cost: float = 0,
) -> float:
    if min(margin, penalty, continuation, signal_cost) < 0:
        raise ValueError("economic coefficients must be nonnegative")
    delivered = sum(row.primary_delivered for row in outcome.periods)
    shortfall = sum(row.primary_shortfall for row in outcome.periods)
    final = outcome.periods[-1]
    share = final.primary_delivered / (
        final.primary_delivered + final.backup_delivered + final.spot
    )
    return margin * delivered - penalty * shortfall + continuation * share - signal_cost
