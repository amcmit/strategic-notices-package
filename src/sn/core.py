"""Deterministic physical and contractual transitions for M1.

The planner supplies feasible actions. This module only applies them and
records dated obligations, product flows, operating costs and supplier facts.
It never uses a future realization to choose an action.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, inf

TOL = 1e-9


def _nonnegative(value: float, name: str) -> None:
    if not isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and nonnegative")


@dataclass(frozen=True)
class Stock:
    site: str
    product: str
    quantity: float

    def __post_init__(self) -> None:
        _nonnegative(self.quantity, "stock quantity")


@dataclass(frozen=True)
class Demand:
    site: str
    product: str
    quantity: float

    def __post_init__(self) -> None:
        _nonnegative(self.quantity, "demand")


@dataclass(frozen=True)
class Recipe:
    site: str
    output: str
    inputs: tuple[tuple[str, float], ...]
    capacity: float
    unit_cost: float = 0

    def __post_init__(self) -> None:
        _nonnegative(self.capacity, "recipe capacity")
        _nonnegative(self.unit_cost, "recipe cost")
        if not self.inputs or len({product for product, _ in self.inputs}) != len(self.inputs):
            raise ValueError("recipe requires distinct components")
        for _, coefficient in self.inputs:
            if not isfinite(coefficient) or coefficient <= 0:
                raise ValueError("bill-of-materials coefficients must be positive")


@dataclass(frozen=True)
class Lane:
    origin: str
    destination: str
    product: str
    lead: int
    capacity: float
    unit_cost: float = 0

    def __post_init__(self) -> None:
        if self.lead < 1:
            raise ValueError("transport lead must be at least one period")
        _nonnegative(self.capacity, "lane capacity")
        _nonnegative(self.unit_cost, "transport cost")


@dataclass(frozen=True)
class CoreNetwork:
    horizon: int
    sites: tuple[str, ...]
    products: tuple[str, ...]
    primary_site: str
    primary_product: str
    initial_stock: tuple[Stock, ...] = ()
    recipes: tuple[Recipe, ...] = ()
    lanes: tuple[Lane, ...] = ()

    def __post_init__(self) -> None:
        if self.horizon < 1 or not self.sites or not self.products:
            raise ValueError("positive horizon, sites and products required")
        if len(set(self.sites)) != len(self.sites) or len(set(self.products)) != len(self.products):
            raise ValueError("sites and products must be unique")
        self.check_key(self.primary_site, self.primary_product)
        for item in self.initial_stock:
            self.check_key(item.site, item.product)
        if len({(x.site, x.product) for x in self.initial_stock}) != len(self.initial_stock):
            raise ValueError("duplicate initial stock key")
        for recipe in self.recipes:
            self.check_key(recipe.site, recipe.output)
            for product, _ in recipe.inputs:
                self.check_key(recipe.site, product)
        if len({(x.site, x.output) for x in self.recipes}) != len(self.recipes):
            raise ValueError("duplicate recipe output")
        for lane in self.lanes:
            self.check_key(lane.origin, lane.product)
            self.check_key(lane.destination, lane.product)

    def check_key(self, site: str, product: str) -> None:
        if site not in self.sites or product not in self.products:
            raise ValueError(f"unknown site/product: {site}/{product}")


@dataclass(frozen=True)
class Contract:
    order_lead: int = 1
    revelation_period: int = 2
    primary_price: float = 1
    backup_price: float = 2
    backup_minimum: float = 1
    qualification_lead: int = 2
    qualification_cost: float = 2
    spot_price: float = 6
    spot_capacity: float = inf
    backlog_cost: float = 0

    def __post_init__(self) -> None:
        if self.order_lead < 1 or self.qualification_lead < 1 or self.revelation_period < 0:
            raise ValueError("invalid timing parameter")
        for name in ("primary_price", "backup_price", "backup_minimum", "qualification_cost",
                     "spot_price", "backlog_cost"):
            _nonnegative(getattr(self, name), name)
        if self.spot_capacity != inf:
            _nonnegative(self.spot_capacity, "spot capacity")


@dataclass(frozen=True)
class Notice:
    announced_capacity: tuple[float, ...]
    text: str

    def __post_init__(self) -> None:
        for value in self.announced_capacity:
            _nonnegative(value, "announced capacity")


@dataclass(frozen=True)
class Order:
    due: int
    quantity: float


@dataclass(frozen=True)
class Transit:
    due: int
    site: str
    product: str
    quantity: float


@dataclass(frozen=True)
class State:
    period: int
    inventory: tuple[Stock, ...]
    backlog: tuple[Stock, ...] = ()
    outstanding: tuple[Order, ...] = ()
    pipeline: tuple[Transit, ...] = ()
    qualified_at: int | None = None
    revealed_schedule: tuple[float, ...] | None = None

    def stock(self, site: str, product: str) -> float:
        return sum(x.quantity for x in self.inventory if (x.site, x.product) == (site, product))

    def outstanding_for(self, due: int) -> float:
        return sum(x.quantity for x in self.outstanding if x.due == due)

    def at_period(self, period: int) -> State:
        """Inspection helper; production execution must use step for time changes."""
        return State(period, self.inventory, self.backlog, self.outstanding,
                     self.pipeline, self.qualified_at, self.revealed_schedule)


@dataclass(frozen=True)
class Production:
    site: str
    product: str
    quantity: float


@dataclass(frozen=True)
class Shipment:
    lane_index: int
    quantity: float


@dataclass(frozen=True)
class Action:
    qualify: bool = False
    primary_order: float = 0
    spot_order: float = 0
    production: tuple[Production, ...] = ()
    shipments: tuple[Shipment, ...] = ()


@dataclass(frozen=True)
class Realization:
    primary_capacity: float
    demand: tuple[Demand, ...] = ()


@dataclass(frozen=True)
class PeriodLedger:
    period: int
    opening_stock: tuple[Stock, ...]
    opening_backlog: tuple[Stock, ...]
    primary_due: float
    primary_delivered: float
    primary_shortfall: float
    accepted_new: float
    accepted_due: int | None
    backup_delivered: float
    spot_delivered: float
    new_demand: tuple[Stock, ...]
    arrivals: tuple[Stock, ...]
    produced: tuple[Stock, ...]
    components_consumed: tuple[Stock, ...]
    departures: tuple[Stock, ...]
    served: tuple[Stock, ...]
    backlog: tuple[Stock, ...]
    closing_stock: tuple[Stock, ...]
    primary_cost: float
    backup_cost: float
    spot_cost: float
    qualification_fee: float
    production_cost: float
    transport_cost: float
    backlog_charge: float
    operating_cost: float


def _stocks(data: dict[tuple[str, str], float]) -> tuple[Stock, ...]:
    return tuple(Stock(site, product, quantity) for (site, product), quantity
                 in sorted(data.items()) if quantity > TOL)


def _add(data: dict[tuple[str, str], float], key: tuple[str, str], amount: float) -> None:
    data[key] = data.get(key, 0.0) + amount
    if data[key] < -TOL:
        raise ValueError("insufficient stock")
    if abs(data[key]) <= TOL:
        data.pop(key, None)


def initial_state(network: CoreNetwork) -> State:
    return State(0, tuple(sorted((x for x in network.initial_stock if x.quantity > TOL),
                                 key=lambda x: (x.site, x.product))))


def step(
    network: CoreNetwork, contract: Contract, notice: Notice, state: State,
    action: Action, realization: Realization,
    *, revealed_schedule: tuple[float, ...] | None = None,
) -> tuple[State, PeriodLedger]:
    """Apply one period atomically; invalid actions leave the input state intact."""
    period = state.period
    if not 0 <= period < network.horizon:
        raise ValueError("period outside horizon")
    if len(notice.announced_capacity) != network.horizon:
        raise ValueError("notice schedule length differs from horizon")
    _nonnegative(realization.primary_capacity, "realized capacity")
    _nonnegative(action.primary_order, "new primary order")
    _nonnegative(action.spot_order, "spot order")
    if action.spot_order > contract.spot_capacity + TOL:
        raise ValueError("spot capacity exceeded")
    if action.qualify and state.qualified_at is not None:
        raise ValueError("backup already qualified")
    if state.revealed_schedule is not None:
        if revealed_schedule is not None and revealed_schedule != state.revealed_schedule:
            raise ValueError("revealed schedule cannot change after disclosure")
        revealed_schedule = state.revealed_schedule
    if period >= contract.revelation_period and revealed_schedule is None:
        raise ValueError("corrected schedule required from revelation onward")
    if revealed_schedule is not None:
        if period < contract.revelation_period:
            raise ValueError("actual schedule cannot be disclosed before revelation")
        if len(revealed_schedule) != network.horizon:
            raise ValueError("revealed schedule length differs from horizon")
        for value in revealed_schedule:
            _nonnegative(value, "revealed capacity")
        if abs(revealed_schedule[period] - realization.primary_capacity) > TOL:
            raise ValueError("revealed and realized current capacities disagree")

    inventory = {(x.site, x.product): x.quantity for x in state.inventory}
    backlog = {(x.site, x.product): x.quantity for x in state.backlog}
    arriving = state.pipeline
    arrivals: dict[tuple[str, str], float] = {}
    next_pipeline: list[Transit] = []
    for item in arriving:
        if item.due == period:
            network.check_key(item.site, item.product)
            _add(inventory, (item.site, item.product), item.quantity)
            _add(arrivals, (item.site, item.product), item.quantity)
        else:
            if item.due < period:
                raise ValueError("overdue shipment in state")
            next_pipeline.append(item)

    due = sum(item.quantity for item in state.outstanding if item.due == period)
    outstanding = [item for item in state.outstanding if item.due > period]
    if any(item.due < period for item in state.outstanding):
        raise ValueError("overdue purchase order in state")
    primary_delivered = min(due, realization.primary_capacity)
    shortfall = due - primary_delivered
    primary_key = (network.primary_site, network.primary_product)
    _add(inventory, primary_key, primary_delivered)

    backup = 0.0
    if state.qualified_at is not None and period >= state.qualified_at + contract.qualification_lead:
        backup = contract.backup_minimum
        _add(inventory, primary_key, backup)
    _add(inventory, primary_key, action.spot_order)

    recipe_actions: dict[tuple[str, str], float] = {}
    for item in action.production:
        network.check_key(item.site, item.product)
        _nonnegative(item.quantity, "production quantity")
        _add(recipe_actions, (item.site, item.product), item.quantity)
    recipe_map = {(x.site, x.output): x for x in network.recipes}
    if set(recipe_actions) - set(recipe_map):
        raise ValueError("production has no recipe")
    produced: dict[tuple[str, str], float] = {}
    component_needs: dict[tuple[str, str], float] = {}
    production_cost = 0.0
    # All recipe inputs come from stock available before production. This
    # prevents an action's tuple or product-name order from creating a free
    # same-period assembly stage.
    for key, quantity in sorted(recipe_actions.items()):
        recipe = recipe_map[key]
        if quantity > recipe.capacity + TOL:
            raise ValueError("recipe capacity exceeded")
        for product, coefficient in recipe.inputs:
            _add(component_needs, (recipe.site, product), coefficient * quantity)
        production_cost += recipe.unit_cost * quantity
    for key, quantity in component_needs.items():
        if inventory.get(key, 0) + TOL < quantity:
            raise ValueError("insufficient component inventory")
    for key, quantity in component_needs.items():
        _add(inventory, key, -quantity)
    for key, quantity in sorted(recipe_actions.items()):
        _add(inventory, key, quantity)
        _add(produced, key, quantity)

    lane_actions: dict[int, float] = {}
    for shipment in action.shipments:
        if not 0 <= shipment.lane_index < len(network.lanes):
            raise ValueError("unknown transport lane")
        _nonnegative(shipment.quantity, "shipment quantity")
        lane_actions[shipment.lane_index] = lane_actions.get(shipment.lane_index, 0) + shipment.quantity
    departures: dict[tuple[str, str], float] = {}
    transport_cost = 0.0
    for index, quantity in sorted(lane_actions.items()):
        lane = network.lanes[index]
        if quantity > lane.capacity + TOL:
            raise ValueError("lane capacity exceeded")
        if period + lane.lead >= network.horizon and quantity > TOL:
            raise ValueError("shipment arrives beyond horizon")
        key = (lane.origin, lane.product)
        if inventory.get(key, 0) + TOL < quantity:
            raise ValueError("insufficient stock for shipment")
        _add(inventory, key, -quantity)
        _add(departures, key, quantity)
        if quantity > TOL:
            next_pipeline.append(Transit(period + lane.lead, lane.destination, lane.product, quantity))
        transport_cost += lane.unit_cost * quantity

    requested: dict[tuple[str, str], float] = {}
    for item in realization.demand:
        network.check_key(item.site, item.product)
        _add(requested, (item.site, item.product), item.quantity)
    served: dict[tuple[str, str], float] = {}
    for key in sorted(set(requested) | set(backlog)):
        total = requested.get(key, 0) + backlog.get(key, 0)
        quantity = min(total, inventory.get(key, 0))
        _add(inventory, key, -quantity)
        _add(served, key, quantity)
        remaining = total - quantity
        if remaining > TOL:
            backlog[key] = remaining
        else:
            backlog.pop(key, None)

    delivery_date = period + contract.order_lead
    accepted_new = 0.0
    if action.primary_order > TOL:
        if delivery_date >= network.horizon:
            raise ValueError("primary order due beyond horizon")
        schedule = revealed_schedule if revealed_schedule is not None else notice.announced_capacity
        already_accepted = sum(item.quantity for item in outstanding if item.due == delivery_date)
        accepted_new = min(action.primary_order, max(0.0, schedule[delivery_date] - already_accepted))
        if accepted_new > TOL:
            outstanding.append(Order(delivery_date, accepted_new))

    primary_cost = contract.primary_price * primary_delivered
    backup_cost = contract.backup_price * backup
    spot_cost = contract.spot_price * action.spot_order
    qualification_fee = contract.qualification_cost if action.qualify else 0.0
    backlog_charge = contract.backlog_cost * sum(backlog.values())
    operating_cost = sum((primary_cost, backup_cost, spot_cost, qualification_fee,
                          production_cost, transport_cost, backlog_charge))
    next_state = State(
        period + 1, _stocks(inventory), _stocks(backlog),
        tuple(sorted(outstanding, key=lambda x: (x.due, x.quantity))),
        tuple(sorted(next_pipeline, key=lambda x: (x.due, x.site, x.product, x.quantity))),
        period if action.qualify else state.qualified_at,
        revealed_schedule,
    )
    ledger = PeriodLedger(
        period, state.inventory, state.backlog, due, primary_delivered, shortfall, accepted_new,
        delivery_date if accepted_new > TOL else None,
        backup, action.spot_order, _stocks(requested), _stocks(arrivals),
        _stocks(produced), _stocks(component_needs), _stocks(departures),
        _stocks(served), _stocks(backlog),
        next_state.inventory, primary_cost, backup_cost, spot_cost,
        qualification_fee, production_cost, transport_cost, backlog_charge,
        operating_cost,
    )
    return next_state, ledger


def audit_step(network: CoreNetwork, before: State, after: State, row: PeriodLedger) -> None:
    """Independently check per-product conservation and the period cost sum."""
    if (row.period, before.period, after.period) != (before.period, before.period, before.period + 1):
        raise ValueError("ledger period mismatch")
    if row.opening_stock != before.inventory or row.closing_stock != after.inventory:
        raise ValueError("stock snapshot mismatch")
    if row.opening_backlog != before.backlog or row.backlog != after.backlog:
        raise ValueError("backlog snapshot mismatch")

    def values(items: tuple[Stock, ...], key: tuple[str, str]) -> float:
        return sum(item.quantity for item in items if (item.site, item.product) == key)

    primary = (network.primary_site, network.primary_product)
    for site in network.sites:
        for product in network.products:
            key = (site, product)
            source = row.primary_delivered + row.backup_delivered + row.spot_delivered if key == primary else 0
            expected = (
                values(row.opening_stock, key) + source + values(row.arrivals, key)
                + values(row.produced, key) - values(row.components_consumed, key)
                - values(row.departures, key) - values(row.served, key)
            )
            if abs(expected - values(row.closing_stock, key)) > TOL:
                raise ValueError(f"material balance failed for {key}")
            backlog = values(row.opening_backlog, key) + values(row.new_demand, key) - values(row.served, key)
            if abs(backlog - values(row.backlog, key)) > TOL:
                raise ValueError(f"backlog balance failed for {key}")
    if abs(row.primary_due - row.primary_delivered - row.primary_shortfall) > TOL:
        raise ValueError("commitment balance failed")
    if abs(before.outstanding_for(row.period) - row.primary_due) > TOL:
        raise ValueError("due obligation differs from opening commitments")
    if (row.accepted_new > TOL) != (row.accepted_due is not None):
        raise ValueError("accepted order lacks its delivery date")
    due_dates = {item.due for item in before.outstanding} | \
        {item.due for item in after.outstanding}
    if row.accepted_due is not None:
        if row.accepted_due <= row.period:
            raise ValueError("accepted delivery date is not future")
        due_dates.add(row.accepted_due)
    for due_date in due_dates:
        expected = before.outstanding_for(due_date)
        if due_date == row.period:
            expected -= row.primary_due
        if due_date == row.accepted_due:
            expected += row.accepted_new
        if abs(expected - after.outstanding_for(due_date)) > TOL:
            raise ValueError("outstanding commitment balance failed")
    pipeline_before = sum(item.quantity for item in before.pipeline)
    pipeline_after = sum(item.quantity for item in after.pipeline)
    arrivals = sum(item.quantity for item in row.arrivals)
    departures = sum(item.quantity for item in row.departures)
    if abs(pipeline_before + departures - arrivals - pipeline_after) > TOL:
        raise ValueError("transport pipeline balance failed")
    parts = (row.primary_cost, row.backup_cost, row.spot_cost, row.qualification_fee,
             row.production_cost, row.transport_cost, row.backlog_charge)
    if abs(sum(parts) - row.operating_cost) > TOL:
        raise ValueError("operating cost balance failed")


def supplier_payoff(
    ledgers: tuple[PeriodLedger, ...], *, margin: float, penalty: float,
    continuation: float = 0, signal_cost: float = 0,
) -> float:
    """Reprice an unchanged physical path without rerunning transitions."""
    if not ledgers:
        raise ValueError("at least one ledger required")
    for name, value in (("margin", margin), ("penalty", penalty),
                        ("continuation", continuation), ("signal cost", signal_cost)):
        _nonnegative(value, name)
    delivered = sum(row.primary_delivered for row in ledgers)
    shortfall = sum(row.primary_shortfall for row in ledgers)
    final = ledgers[-1]
    external = final.primary_delivered + final.backup_delivered + final.spot_delivered
    final_share = final.primary_delivered / external if external > TOL else 0.0
    return margin * delivered - penalty * shortfall + continuation * final_share - signal_cost
