"""Independent period enumerator for the proposed v3 G0 analytical gate.

It imports no ``sn`` module and uses no closed-form cost or window formula.
It has not been executed. Quantities are exact rational numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Literal


Raw = int | str | Fraction
Mode = Literal["finite", "horizon"]
Z = Fraction(0)
ONE = Fraction(1)


def q(value: Raw) -> Fraction:
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError("reference inputs must be exact, not bool or float")
    if not isinstance(value, (int, str, Fraction)):
        raise TypeError("unsupported reference input")
    return Fraction(value)


@dataclass(frozen=True)
class ReferenceCase:
    horizon: int
    short_duration: int
    long_duration: int
    lead: int
    inventory: Raw
    primary_price: Raw
    backup_price: Raw
    net_shortage: Raw
    fee: Raw
    commitment: Mode

    def __post_init__(self) -> None:
        for field in ("horizon", "short_duration", "long_duration", "lead"):
            item = getattr(self, field)
            if isinstance(item, bool) or not isinstance(item, int):
                raise TypeError(f"{field} must be int")
        if not (0 < self.short_duration < self.long_duration <= self.horizon):
            raise ValueError("invalid durations")
        if not (0 < self.lead < self.horizon):
            raise ValueError("invalid lead")
        if self.commitment not in ("finite", "horizon"):
            raise ValueError("invalid commitment")
        for field in ("inventory", "primary_price", "backup_price",
                      "net_shortage", "fee"):
            object.__setattr__(self, field, q(getattr(self, field)))
        if not (Z <= self.inventory <= self.horizon):
            raise ValueError("invalid inventory")
        if self.primary_price <= Z or self.net_shortage <= Z:
            raise ValueError("invalid price or shortage")
        if self.backup_price < self.primary_price or self.fee < Z:
            raise ValueError("invalid backup price or fee")


@dataclass(frozen=True)
class PeriodRecord:
    period: int
    backup_received: Fraction
    primary_delivered: Fraction
    lost_sales: Fraction
    inventory_end: Fraction


@dataclass(frozen=True)
class EnumeratedPath:
    recovery: int
    qualification_date: int | None
    periods: tuple[PeriodRecord, ...]
    paid_backup: Fraction
    lost_sales: Fraction
    primary_delivered: Fraction
    inventory_end: Fraction
    buyer_cost: Fraction


def simulate(case: ReferenceCase, recovery: int,
             qualification_date: int | None) -> EnumeratedPath:
    """Execute each period: arrival, stock use, optional primary, lost sale."""
    if isinstance(recovery, bool) or not isinstance(recovery, int):
        raise TypeError("recovery must be int")
    if not (0 < recovery <= case.horizon):
        raise ValueError("recovery outside horizon")
    if qualification_date is not None:
        if isinstance(qualification_date, bool) or not isinstance(qualification_date, int):
            raise TypeError("qualification date must be int or None")
        if not (0 <= qualification_date < case.horizon - case.lead):
            raise ValueError("qualification has no in-horizon delivery")

    stock = case.inventory
    paid = Z
    lost_total = Z
    primary_total = Z
    buyer_cost = Z
    records: list[PeriodRecord] = []
    if qualification_date is None:
        backup_start = case.horizon
        backup_end = case.horizon
    else:
        backup_start = qualification_date + case.lead
        if case.commitment == "finite":
            backup_end = min(case.horizon, backup_start + case.long_duration)
        else:
            backup_end = case.horizon

    for period in range(case.horizon):
        if period == qualification_date:
            buyer_cost += case.fee
        backup = ONE if backup_start <= period < backup_end else Z
        stock += backup
        paid += backup
        buyer_cost += case.backup_price * backup

        from_stock = min(ONE, stock)
        stock -= from_stock
        unmet = ONE - from_stock
        primary = unmet if period >= recovery else Z
        lost = unmet - primary
        primary_total += primary
        lost_total += lost
        buyer_cost += case.primary_price * primary
        buyer_cost += (case.primary_price + case.net_shortage) * lost
        records.append(PeriodRecord(period, backup, primary, lost, stock))

    return EnumeratedPath(recovery, qualification_date, tuple(records),
                          paid, lost_total, primary_total, stock, buyer_cost)


def choose(case: ReferenceCase, recovery: int, earliest_date: int = 0) -> EnumeratedPath:
    """Enumerate all allowed dates and never; skip zero ties, then wait."""
    if isinstance(earliest_date, bool) or not isinstance(earliest_date, int):
        raise TypeError("earliest_date must be int")
    if earliest_date < 0:
        raise ValueError("earliest_date must be nonnegative")
    winner = simulate(case, recovery, None)
    for date in range(earliest_date, case.horizon - case.lead):
        path = simulate(case, recovery, date)
        if path.buyer_cost < winner.buyer_cost or (
            path.buyer_cost == winner.buyer_cost and winner.qualification_date is not None
        ):
            winner = path
    return winner


@dataclass(frozen=True)
class EnumeratedWindow:
    truthful_long: EnumeratedPath
    literal_short: EnumeratedPath
    after_revelation: EnumeratedPath
    maximum_truth_saving: Fraction
    maximum_delayed_saving: Fraction | None
    backup_suppression_window: bool


def classify(case: ReferenceCase) -> EnumeratedWindow:
    no_backup = simulate(case, case.long_duration, None)
    truthful = choose(case, case.long_duration)
    short = choose(case, case.short_duration)
    delayed = choose(case, case.long_duration, case.short_duration)
    all_savings = [
        no_backup.buyer_cost - simulate(case, case.long_duration, date).buyer_cost
        for date in range(case.horizon - case.lead)
    ]
    delayed_savings = all_savings[case.short_duration:]
    maximum_delayed = max(delayed_savings) if delayed_savings else None
    in_window = (short.qualification_date is None
                 and truthful.qualification_date is not None
                 and delayed.qualification_date is None)
    return EnumeratedWindow(truthful, short, delayed,
                            max(all_savings), maximum_delayed, in_window)


@dataclass(frozen=True)
class OrderRecord:
    placed_at: int
    due_at: int
    accepted: Fraction
    delivered: Fraction
    shortfall: Fraction


def one_order(*, short_duration: int, actual_duration: int,
              reported_duration: int, placed_at: int, due_at: int,
              requested: Raw = 1) -> OrderRecord:
    """One lead-one order, accepted before revelation under the report."""
    if due_at != placed_at + 1 or placed_at < 0:
        raise ValueError("reference purchase-order lead must equal one")
    if short_duration <= 0 or actual_duration <= short_duration - 1:
        raise ValueError("invalid type-revelation dates")
    amount = q(requested)
    if not (Z <= amount <= ONE):
        raise ValueError("single-buyer order must be in [0,1]")
    schedule = reported_duration if placed_at < short_duration else actual_duration
    accepted = amount if due_at >= schedule else Z
    available = ONE if due_at >= actual_duration else Z
    delivered = min(accepted, available)
    return OrderRecord(placed_at, due_at, accepted, delivered,
                       accepted - delivered)


def ledger_gain(*, primary_truth: Raw, primary_false: Raw,
                shortfall_truth: Raw, shortfall_false: Raw,
                margin: Raw, penalty: Raw, reliance: Raw,
                fixed_cost: Raw, truth_contradicted: bool = False,
                false_contradicted: bool = True) -> tuple[Fraction, Fraction | None]:
    """Gain from two ledger totals; root comes from gain at 0 and 1."""
    pt, pf, st, sf, r, k, lam, fee = (
        q(x) for x in (primary_truth, primary_false, shortfall_truth,
                       shortfall_false, margin, penalty, reliance, fixed_cost)
    )
    if min(pt, pf, st, sf, r, k, lam, fee) < Z:
        raise ValueError("quantities and rates must be nonnegative")
    if not isinstance(truth_contradicted, bool) or not isinstance(false_contradicted, bool):
        raise TypeError("contradiction indicators must be bool")
    truth_fixed = fee if truth_contradicted else Z
    false_fixed = fee if false_contradicted else Z

    def difference(at_penalty: Fraction) -> Fraction:
        truth_payoff = r * pt - lam * at_penalty * st - truth_fixed
        false_payoff = r * pf - lam * at_penalty * sf - false_fixed
        return false_payoff - truth_payoff

    intercept = difference(Z)
    slope = difference(ONE) - intercept
    critical = None if lam == Z or sf == st else -intercept / slope
    return difference(k), critical
