"""Versioned v3 contract and physical transition.

Primary demand expires as lost sales. The optional backlog mode has an
explicit per-demand-key catch-up cap. Mandatory backup and spot supply enter
at declared critical-part sites. V1 and v2 modules are unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite
from typing import Literal

from .core import (
    Action, CoreNetwork, Notice, Order, PeriodLedger, Realization, State, Stock,
    TOL, Transit, _nonnegative, audit_step, step,
)
from .core_v2 import ContractV2


DemandRule = Literal["lost_sales", "backlog_catchup"]


@dataclass(frozen=True, kw_only=True)
class NetworkV3(CoreNetwork):
    critical_site: str
    critical_product: str
    spot_site: str | None = None
    spot_product: str | None = None
    initial_outstanding: tuple[Order, ...] = ()

    def __post_init__(self) -> None:
        super().__post_init__()
        self.check_key(self.critical_site, self.critical_product)
        if (self.spot_site is None) != (self.spot_product is None):
            raise ValueError("spot site and product must be set together")
        if self.spot_site is not None:
            self.check_key(self.spot_site, self.spot_product)
        if any(order.due != 0 for order in self.initial_outstanding):
            raise ValueError("v3 pre-horizon accepted orders must be due at period zero")
        for order in self.initial_outstanding:
            _nonnegative(order.quantity, "v3 pre-horizon accepted order")

    @property
    def backup_key(self) -> tuple[str, str]:
        return self.critical_site, self.critical_product

    @property
    def spot_key(self) -> tuple[str, str]:
        return (self.spot_site, self.spot_product) if self.spot_site is not None else self.backup_key


def initial_state_v3(network: NetworkV3) -> State:
    """Include immutable pre-horizon accepted orders, when a case declares them."""
    inventory = tuple(sorted((item for item in network.initial_stock
                              if item.quantity > TOL),
                             key=lambda item: (item.site, item.product)))
    return State(0, inventory, outstanding=network.initial_outstanding)


@dataclass(frozen=True)
class ContractV3(ContractV2):
    net_shortage_value: float = 20.0
    demand_rule: DemandRule = "lost_sales"
    catchup_capacity: float | None = None
    liability_multiplier: float = 1.0
    fixed_contradiction_cost: float = 0.0

    def __post_init__(self) -> None:
        super().__post_init__()
        for name in ("net_shortage_value", "liability_multiplier",
                     "fixed_contradiction_cost"):
            _nonnegative(getattr(self, name), name)
        if self.demand_rule == "lost_sales":
            if self.catchup_capacity is not None:
                raise ValueError("lost-sales rule cannot set catch-up capacity")
        elif self.demand_rule == "backlog_catchup":
            if self.catchup_capacity is None:
                raise ValueError("backlog rule requires catch-up capacity")
            _nonnegative(self.catchup_capacity, "catch-up capacity")
        else:
            raise ValueError("unknown v3 demand rule")


@dataclass(frozen=True)
class PeriodLedgerV3(PeriodLedger):
    lost_sales: tuple[Stock, ...]
    lost_sales_charge: float
    pre_horizon_order_shortfall: float


def _stock_map(items: tuple[Stock, ...]) -> dict[tuple[str, str], float]:
    result: dict[tuple[str, str], float] = {}
    for item in items:
        key = item.site, item.product
        result[key] = result.get(key, 0.0) + item.quantity
    return result


def _stocks(values: dict[tuple[str, str], float]) -> tuple[Stock, ...]:
    if any(value < -TOL or not isfinite(value) for value in values.values()):
        raise ValueError("invalid v3 stock quantity")
    return tuple(Stock(site, product, value)
                 for (site, product), value in sorted(values.items())
                 if value > TOL)


def _add(values: dict[tuple[str, str], float], key: tuple[str, str], amount: float) -> None:
    values[key] = values.get(key, 0.0) + amount
    if values[key] < -TOL:
        raise ValueError("v3 source correction exceeds recorded arrivals")
    if abs(values[key]) <= TOL:
        values.pop(key, None)


def step_v3(
    network: NetworkV3, contract: ContractV3, notice: Notice, state: State,
    action: Action, realization: Realization,
    *, revealed_schedule: tuple[float, ...] | None = None,
) -> tuple[State, PeriodLedgerV3]:
    """Apply one period with declared source sites and expiring demand."""
    if not isinstance(network, NetworkV3) or not isinstance(contract, ContractV3):
        raise TypeError("v3 step requires NetworkV3 and ContractV3")
    _nonnegative(action.spot_order, "spot order")
    if action.spot_order > contract.spot_capacity + TOL:
        raise ValueError("spot capacity exceeded")
    if contract.demand_rule == "lost_sales" and state.backlog:
        raise ValueError("lost-sales state cannot carry backlog")

    backup = contract.backup_minimum if contract.backup_active_at(
        state.qualified_at, state.period) else 0.0
    artificial = []
    if backup > TOL:
        artificial.append(Transit(state.period, *network.backup_key, backup))
    if action.spot_order > TOL:
        artificial.append(Transit(state.period, *network.spot_key, action.spot_order))

    deferred: dict[tuple[str, str], float] = {}
    if contract.demand_rule == "backlog_catchup":
        available_backlog = {}
        for key, amount in _stock_map(state.backlog).items():
            eligible = min(amount, contract.catchup_capacity)
            if eligible > TOL:
                available_backlog[key] = eligible
            if amount - eligible > TOL:
                deferred[key] = amount - eligible
        prepared_backlog = _stocks(available_backlog)
    else:
        prepared_backlog = ()

    prepared = replace(state, backlog=prepared_backlog,
                       pipeline=state.pipeline + tuple(artificial))
    if state.period == 0 and abs(
        state.outstanding_for(0)
        - sum(order.quantity for order in network.initial_outstanding)
    ) > TOL:
        raise ValueError("period-zero outstanding orders differ from pre-horizon ledger")
    effective = replace(contract, backup_minimum=0.0, spot_capacity=0.0,
                        backlog_cost=(contract.backlog_cost
                                      if contract.demand_rule == "backlog_catchup" else 0.0))
    raw_after, raw_row = step(
        network, effective, notice, prepared, replace(action, spot_order=0.0),
        realization, revealed_schedule=revealed_schedule,
    )
    audit_step(network, prepared, raw_after, raw_row)

    real_arrivals = _stock_map(raw_row.arrivals)
    _add(real_arrivals, network.backup_key, -backup)
    _add(real_arrivals, network.spot_key, -action.spot_order)
    if contract.demand_rule == "lost_sales":
        lost = raw_row.backlog
        closing_backlog = ()
        lost_charge = (contract.primary_price + contract.net_shortage_value) * sum(
            item.quantity for item in lost
        )
        backlog_charge = 0.0
    else:
        lost = ()
        combined = _stock_map(raw_row.backlog)
        for key, amount in deferred.items():
            combined[key] = combined.get(key, 0.0) + amount
        closing_backlog = _stocks(combined)
        lost_charge = 0.0
        backlog_charge = contract.backlog_cost * sum(item.quantity for item in closing_backlog)

    after = replace(raw_after, backlog=closing_backlog)
    backup_cost = contract.backup_price * backup
    spot_cost = contract.spot_price * action.spot_order
    cost = (raw_row.operating_cost - raw_row.backlog_charge
            + backup_cost + spot_cost + lost_charge + backlog_charge)
    fields = dict(raw_row.__dict__)
    fields.update(
        opening_backlog=state.backlog, arrivals=_stocks(real_arrivals),
        backup_delivered=backup, spot_delivered=action.spot_order,
        backlog=closing_backlog, backup_cost=backup_cost, spot_cost=spot_cost,
        backlog_charge=backlog_charge, operating_cost=cost,
    )
    pre_horizon_shortfall = raw_row.primary_shortfall if state.period == 0 else 0.0
    row = PeriodLedgerV3(**fields, lost_sales=lost,
                         lost_sales_charge=lost_charge,
                         pre_horizon_order_shortfall=pre_horizon_shortfall)
    audit_step_v3(network, contract, state, after, row, action)
    return after, row


def audit_step_v3(
    network: NetworkV3, contract: ContractV3, before: State,
    after: State, row: PeriodLedgerV3, action: Action,
) -> None:
    """Check transformed v3 balances without artificial arrivals."""
    if (row.period, after.period) != (before.period, before.period + 1):
        raise ValueError("v3 period mismatch")
    if row.opening_stock != before.inventory or row.closing_stock != after.inventory:
        raise ValueError("v3 inventory snapshot mismatch")
    if row.opening_backlog != before.backlog or row.backlog != after.backlog:
        raise ValueError("v3 backlog snapshot mismatch")

    opening = _stock_map(row.opening_stock)
    arrivals = _stock_map(row.arrivals)
    produced = _stock_map(row.produced)
    consumed = _stock_map(row.components_consumed)
    departed = _stock_map(row.departures)
    served = _stock_map(row.served)
    closing = _stock_map(row.closing_stock)
    demand = _stock_map(row.new_demand)
    opening_backlog = _stock_map(row.opening_backlog)
    closing_backlog = _stock_map(row.backlog)
    lost = _stock_map(row.lost_sales)
    for site in network.sites:
        for product in network.products:
            key = site, product
            supply = 0.0
            if key == (network.primary_site, network.primary_product):
                supply += row.primary_delivered
            if key == network.backup_key:
                supply += row.backup_delivered
            if key == network.spot_key:
                supply += row.spot_delivered
            physical = (opening.get(key, 0.0) + arrivals.get(key, 0.0)
                        + produced.get(key, 0.0) + supply
                        - consumed.get(key, 0.0) - departed.get(key, 0.0)
                        - served.get(key, 0.0))
            if abs(physical - closing.get(key, 0.0)) > TOL:
                raise ValueError(f"v3 material balance failed for {key}")
            demand_balance = (opening_backlog.get(key, 0.0)
                              + demand.get(key, 0.0) - served.get(key, 0.0)
                              - lost.get(key, 0.0))
            if abs(demand_balance - closing_backlog.get(key, 0.0)) > TOL:
                raise ValueError(f"v3 demand balance failed for {key}")

    if contract.demand_rule == "lost_sales" and (after.backlog or before.backlog):
        raise ValueError("lost-sales transition carried backlog")
    if contract.demand_rule == "backlog_catchup":
        for key, amount in opening_backlog.items():
            catch = max(0.0, served.get(key, 0.0) - demand.get(key, 0.0))
            if catch > min(amount, contract.catchup_capacity) + TOL:
                raise ValueError("backlog catch-up capacity exceeded")
    if abs(row.primary_due - row.primary_delivered - row.primary_shortfall) > TOL:
        raise ValueError("v3 accepted-order balance failed")
    expected_pre_horizon_shortfall = row.primary_shortfall if before.period == 0 else 0.0
    if abs(row.pre_horizon_order_shortfall - expected_pre_horizon_shortfall) > TOL:
        raise ValueError("v3 pre-horizon shortfall column mismatch")
    if abs(before.outstanding_for(before.period) - row.primary_due) > TOL:
        raise ValueError("v3 due-order snapshot mismatch")
    if abs(row.backup_delivered - (contract.backup_minimum if contract.backup_active_at(
            before.qualified_at, before.period) else 0.0)) > TOL:
        raise ValueError("v3 mandatory backup delivery mismatch")
    if abs(row.backup_cost - contract.backup_price * row.backup_delivered) > TOL:
        raise ValueError("v3 backup payment mismatch")
    if abs(row.spot_cost - contract.spot_price * row.spot_delivered) > TOL:
        raise ValueError("v3 spot payment mismatch")
    if abs(row.qualification_fee - (contract.qualification_cost if action.qualify else 0)) > TOL:
        raise ValueError("v3 qualification fee mismatch")
    if action.qualify and (before.qualified_at is not None or after.qualified_at != before.period):
        raise ValueError("v3 qualification is not one-time")
    if not action.qualify and after.qualified_at != before.qualified_at:
        raise ValueError("v3 qualification date changed without action")
    if abs(row.lost_sales_charge - (contract.primary_price + contract.net_shortage_value)
           * sum(item.quantity for item in row.lost_sales)) > TOL:
        raise ValueError("v3 lost-sales charge mismatch")
    if abs(row.backlog_charge - contract.backlog_cost
           * sum(item.quantity for item in row.backlog)) > TOL:
        raise ValueError("v3 backlog charge mismatch")
    parts = (row.primary_cost, row.backup_cost, row.spot_cost,
             row.qualification_fee, row.production_cost, row.transport_cost,
             row.backlog_charge, row.lost_sales_charge)
    if abs(sum(parts) - row.operating_cost) > TOL:
        raise ValueError("v3 operating-cost balance failed")
    opening_pipe = sum(item.quantity for item in before.pipeline)
    closing_pipe = sum(item.quantity for item in after.pipeline)
    if abs(opening_pipe + sum(x.quantity for x in row.departures)
           - sum(x.quantity for x in row.arrivals) - closing_pipe) > TOL:
        raise ValueError("v3 transport-pipeline balance failed")


def supplier_payoff_v3(
    contract: ContractV3, notice: Notice, actual_schedule: tuple[float, ...],
    ledgers: tuple[PeriodLedgerV3, ...], *, margin: float, penalty: float,
) -> float:
    """Ledger-only supplier payoff; all contradicted notices incur F."""
    if not ledgers or len(notice.announced_capacity) != len(actual_schedule):
        raise ValueError("complete ledgers and schedules required")
    _nonnegative(margin, "margin")
    _nonnegative(penalty, "penalty")
    sold = sum(row.primary_delivered for row in ledgers)
    shortfall = sum(row.primary_shortfall - row.pre_horizon_order_shortfall
                    for row in ledgers)
    contradicted = notice.announced_capacity != actual_schedule
    return (margin * sold - contract.liability_multiplier * penalty * shortfall
            - (contract.fixed_contradiction_cost if contradicted else 0.0))
