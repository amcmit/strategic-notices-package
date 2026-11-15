"""Exact closed-form G0 benchmark for the proposed v3 single-buyer model.

This module has no network, MIP, or case-generator dependency. It is a
review candidate: its tests and enumeration have not been run.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Literal


Commitment = Literal["finite", "horizon"]
Exact = int | str | Fraction
ZERO = Fraction(0)


def exact(value: Exact) -> Fraction:
    """Reject binary floating-point inputs so every comparison is exact."""
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError("use int, decimal string, or Fraction, not bool/float")
    if not isinstance(value, (int, str, Fraction)):
        raise TypeError("unsupported exact quantity")
    return Fraction(value)


@dataclass(frozen=True)
class WindowCase:
    horizon: int
    short_duration: int
    long_duration: int
    lead: int
    inventory: Exact
    primary_price: Exact
    backup_price: Exact
    net_shortage: Exact
    fee: Exact
    commitment: Commitment

    def __post_init__(self) -> None:
        for name in ("horizon", "short_duration", "long_duration", "lead"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
        if not (0 < self.short_duration < self.long_duration <= self.horizon):
            raise ValueError("require 0 < short < long <= horizon")
        if not (0 < self.lead < self.horizon):
            raise ValueError("lead must be positive and less than horizon")
        if self.commitment not in ("finite", "horizon"):
            raise ValueError("commitment must be finite or horizon")
        for name in ("inventory", "primary_price", "backup_price",
                     "net_shortage", "fee"):
            object.__setattr__(self, name, exact(getattr(self, name)))
        if not (ZERO <= self.inventory <= self.horizon):
            raise ValueError("inventory must be between zero and horizon")
        if self.primary_price <= ZERO or self.net_shortage <= ZERO:
            raise ValueError("primary price and net shortage must be positive")
        if self.backup_price < self.primary_price or self.fee < ZERO:
            raise ValueError("backup price must be at least primary; fee >= 0")


@dataclass(frozen=True)
class FormulaPath:
    duration: int
    qualification_date: int | None
    paid_backup: Fraction
    backup_before_recovery: Fraction
    lost_sales: Fraction
    primary_delivered: Fraction
    avoided_lost_sales: Fraction
    surplus_backup: Fraction
    buyer_cost: Fraction
    saving: Fraction


def formula_path(case: WindowCase, duration: int,
                 qualification_date: int | None) -> FormulaPath:
    """Closed-form values for a fixed date; None means never qualify."""
    if isinstance(duration, bool) or not isinstance(duration, int):
        raise TypeError("duration must be an integer")
    if not (0 < duration <= case.horizon):
        raise ValueError("duration outside horizon")
    if qualification_date is not None:
        if isinstance(qualification_date, bool) or not isinstance(qualification_date, int):
            raise TypeError("qualification date must be an integer or None")
        if not (0 <= qualification_date < case.horizon - case.lead):
            raise ValueError("qualification has no in-horizon delivery")

    lost_without = max(ZERO, Fraction(duration) - case.inventory)
    primary_without = max(
        ZERO, Fraction(case.horizon) - lost_without - case.inventory
    )
    cost_without = (case.primary_price + case.net_shortage) * lost_without
    cost_without += case.primary_price * primary_without
    if qualification_date is None:
        return FormulaPath(duration, None, ZERO, ZERO, lost_without,
                           primary_without, ZERO, ZERO, cost_without, ZERO)

    arrival = qualification_date + case.lead
    paid = Fraction(case.horizon - arrival)
    if case.commitment == "finite":
        paid = min(Fraction(case.long_duration), paid)
    before_recovery = max(
        ZERO, Fraction(min(duration, arrival + int(paid)) - arrival)
    )
    lost = max(ZERO, Fraction(duration) - case.inventory - before_recovery)
    avoided = lost_without - lost
    primary = max(ZERO, Fraction(case.horizon) - lost - case.inventory - paid)
    surplus = max(ZERO, paid - avoided - primary_without)
    cost = (case.primary_price + case.net_shortage) * lost
    cost += case.primary_price * primary + case.fee + case.backup_price * paid
    return FormulaPath(duration, qualification_date, paid, before_recovery,
                       lost, primary, avoided, surplus, cost,
                       cost_without - cost)


def best_date(case: WindowCase, duration: int, earliest_date: int = 0) -> FormulaPath:
    """Skip a zero tie; among profitable equal-cost dates choose latest."""
    if isinstance(earliest_date, bool) or not isinstance(earliest_date, int):
        raise TypeError("earliest_date must be an integer")
    if earliest_date < 0:
        raise ValueError("earliest_date must be nonnegative")
    best = formula_path(case, duration, None)
    for date in range(earliest_date, case.horizon - case.lead):
        row = formula_path(case, duration, date)
        if row.buyer_cost < best.buyer_cost or (
            row.buyer_cost == best.buyer_cost and best.qualification_date is not None
        ):
            best = row
    return best


@dataclass(frozen=True)
class WindowResult:
    truthful_long: FormulaPath
    literal_short: FormulaPath
    after_revelation: FormulaPath
    max_truth_saving: Fraction
    max_delayed_saving: Fraction | None
    backup_suppression_window: bool


def window_result(case: WindowCase) -> WindowResult:
    truth = best_date(case, case.long_duration)
    short = best_date(case, case.short_duration)
    delayed = best_date(case, case.long_duration, case.short_duration)
    max_truth = max(
        formula_path(case, case.long_duration, date).saving
        for date in range(case.horizon - case.lead)
    )
    delayed_dates = range(case.short_duration, case.horizon - case.lead)
    max_delayed = max(
        (formula_path(case, case.long_duration, date).saving
         for date in delayed_dates),
        default=None,
    )
    in_window = (short.qualification_date is None
                 and truth.qualification_date is not None
                 and delayed.qualification_date is None)
    return WindowResult(truth, short, delayed, max_truth, max_delayed,
                        in_window)


@dataclass(frozen=True)
class FiniteR1Corollary:
    avoided_lost_sales_at_zero: Fraction
    avoided_loss_value: Fraction
    fee_and_premium: Fraction
    in_window: bool


def finite_r1_corollary(case: WindowCase) -> FiniteR1Corollary:
    """One-inequality R1 test, only under its sufficient assumptions."""
    ds, dl, lead, horizon = (case.short_duration, case.long_duration,
                            case.lead, case.horizon)
    if case.commitment != "finite":
        raise ValueError("corollary requires finite K = long duration")
    if not (ds <= lead < dl <= ds + lead):
        raise ValueError("corollary requires R1 timing and lead >= short")
    if horizon < (ds - 1) + lead + dl:
        raise ValueError("pre-revelation commitments would be truncated")
    if Fraction(horizon) < Fraction(dl) + max(Fraction(dl), case.inventory):
        raise ValueError("no-surplus sufficient horizon bound fails")
    avoided = min(max(ZERO, Fraction(dl) - case.inventory), Fraction(dl - lead))
    lhs = case.net_shortage * avoided
    rhs = case.fee + (case.backup_price - case.primary_price) * dl
    return FiniteR1Corollary(avoided, lhs, rhs, lhs > rhs)


@dataclass(frozen=True)
class SupplierComparison:
    primary_difference: Fraction
    shortfall_difference: Fraction
    contradiction_difference: int
    gain: Fraction
    critical_penalty: Fraction | None


def supplier_comparison(*, primary_truth: Exact, primary_false: Exact,
                        shortfall_truth: Exact, shortfall_false: Exact,
                        margin: Exact, kappa: Exact, reliance: Exact,
                        fixed_cost: Exact, truth_contradicted: bool = False,
                        false_contradicted: bool = True) -> SupplierComparison:
    """Reprice fixed ledger quantities; no buyer-policy recomputation."""
    values = [exact(v) for v in (primary_truth, primary_false,
                                shortfall_truth, shortfall_false, margin,
                                kappa, reliance, fixed_cost)]
    pt, pf, st, sf, r, penalty, lam, fee = values
    if min(pt, pf, st, sf, r, penalty, lam, fee) < ZERO:
        raise ValueError("quantities and rates must be nonnegative")
    if not isinstance(truth_contradicted, bool) or not isinstance(false_contradicted, bool):
        raise TypeError("contradiction indicators must be bool")
    delta_primary = pf - pt
    delta_shortfall = sf - st
    delta_contradiction = int(false_contradicted) - int(truth_contradicted)
    numerator = r * delta_primary - fee * delta_contradiction
    gain = numerator - lam * penalty * delta_shortfall
    critical = None if lam == ZERO or delta_shortfall == ZERO else (
        numerator / (lam * delta_shortfall)
    )
    return SupplierComparison(delta_primary, delta_shortfall,
                              delta_contradiction, gain, critical)
