"""Proposed G0 acceptance tests. Deliberately unrun pending owner review."""

from __future__ import annotations

import resource
import sys
from fractions import Fraction
from itertools import product
from time import monotonic

from sn.analytical_window_v3 import (
    WindowCase,
    finite_r1_corollary,
    formula_path,
    supplier_comparison,
    window_result,
)
from sn_g0_reference_v3 import (
    ReferenceCase,
    choose,
    classify,
    ledger_gain,
    one_order,
    simulate,
)


G0_WALL_SECONDS = 900
G0_PEAK_RSS_MIB = 512
_SUITE_STARTED = monotonic()


def _budget_check() -> None:
    if monotonic() - _SUITE_STARTED > G0_WALL_SECONDS:
        raise RuntimeError("G0 analytical wall cap reached")
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_mib = peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024
    if peak_mib > G0_PEAK_RSS_MIB:
        raise RuntimeError("G0 analytical peak RSS cap reached")


def _pair(**values: object) -> tuple[WindowCase, ReferenceCase]:
    return WindowCase(**values), ReferenceCase(**values)


def test_lost_sales_hand_toy_all_dates_and_ledger() -> None:
    _budget_check()
    closed, reference = _pair(
        horizon=6, short_duration=2, long_duration=3, lead=2,
        inventory=2, primary_price=1, backup_price="1.25",
        net_shortage=20, fee=2, commitment="finite",
    )
    rows = {
        3: {None: ("24", 1, 3), 0: ("6.75", 0, 1),
            1: ("26.75", 1, 0), 2: ("26.50", 1, 1),
            3: ("26.25", 1, 2)},
        2: {None: ("4", 0, 4), 0: ("6.75", 0, 1),
            1: ("6.75", 0, 1), 2: ("6.50", 0, 2),
            3: ("6.25", 0, 3)},
    }
    for duration, dates in rows.items():
        for date, (cost, lost, primary) in dates.items():
            formula = formula_path(closed, duration, date)
            periods = simulate(reference, duration, date)
            assert formula.buyer_cost == periods.buyer_cost == Fraction(cost)
            assert formula.lost_sales == periods.lost_sales == lost
            assert formula.primary_delivered == periods.primary_delivered == primary
            assert formula.paid_backup == periods.paid_backup

    closed_window = window_result(closed)
    enumerated_window = classify(reference)
    assert closed_window.backup_suppression_window
    assert enumerated_window.backup_suppression_window
    assert closed_window.truthful_long.qualification_date == 0
    assert closed_window.literal_short.qualification_date is None
    assert closed_window.after_revelation.qualification_date is None
    assert closed_window.max_truth_saving == Fraction("17.25")
    assert closed_window.max_delayed_saving == Fraction("-2.25")
    assert finite_r1_corollary(closed).in_window

    false_order = one_order(short_duration=2, actual_duration=3,
                            reported_duration=2, placed_at=1, due_at=2)
    truth_order = one_order(short_duration=2, actual_duration=3,
                            reported_duration=3, placed_at=1, due_at=2)
    assert (false_order.accepted, false_order.delivered, false_order.shortfall) == (1, 0, 1)
    assert truth_order.shortfall == 0
    closed_gain = supplier_comparison(
        primary_truth=1, primary_false=3,
        shortfall_truth=truth_order.shortfall,
        shortfall_false=false_order.shortfall,
        margin=3, kappa=5, reliance=1, fixed_cost=0,
    )
    enumerated_gain = ledger_gain(
        primary_truth=1, primary_false=3,
        shortfall_truth=truth_order.shortfall,
        shortfall_false=false_order.shortfall,
        margin=3, penalty=5, reliance=1, fixed_cost=0,
    )
    assert closed_gain.gain == enumerated_gain[0] == 1
    assert closed_gain.critical_penalty == enumerated_gain[1] == 6


def test_toy_no_damages_fixed_cost_and_undefined_thresholds() -> None:
    _budget_check()
    for reliance, fixed_cost, expected_gain, expected_root in (
        (0, 0, 6, None), (1, 2, -1, 4),
    ):
        formula = supplier_comparison(
            primary_truth=1, primary_false=3,
            shortfall_truth=0, shortfall_false=1,
            margin=3, kappa=5, reliance=reliance, fixed_cost=fixed_cost,
        )
        reference = ledger_gain(
            primary_truth=1, primary_false=3,
            shortfall_truth=0, shortfall_false=1,
            margin=3, penalty=5, reliance=reliance, fixed_cost=fixed_cost,
        )
        assert (formula.gain, formula.critical_penalty) == reference
        assert reference == (expected_gain, expected_root)

    for shortfall_truth, shortfall_false in ((1, 1), (1, 0)):
        formula = supplier_comparison(
            primary_truth=1, primary_false=3,
            shortfall_truth=shortfall_truth, shortfall_false=shortfall_false,
            margin=3, kappa=5, reliance=1, fixed_cost=0,
        )
        reference = ledger_gain(
            primary_truth=1, primary_false=3,
            shortfall_truth=shortfall_truth, shortfall_false=shortfall_false,
            margin=3, penalty=5, reliance=1, fixed_cost=0,
        )
        assert (formula.gain, formula.critical_penalty) == reference
        assert (formula.critical_penalty is None) == (shortfall_truth == shortfall_false)
        if (shortfall_truth, shortfall_false) == (1, 0):
            assert reference == (11, -6)

    overreported = one_order(short_duration=2, actual_duration=2,
                             reported_duration=3, placed_at=1, due_at=2)
    assert overreported.accepted == 0
    assert supplier_comparison(
        primary_truth=4, primary_false=4,
        shortfall_truth=0, shortfall_false=0,
        margin=3, kappa=5, reliance=1, fixed_cost=2,
        false_contradicted=True,
    ).gain == -2


def test_corollary_equality_boundary_and_lower_h_fee() -> None:
    _budget_check()
    common = dict(horizon=20, short_duration=2, long_duration=4, lead=2,
                  inventory=1, primary_price=1, backup_price="1.25",
                  commitment="finite")
    for fee, net_shortage, expected in (
        ("4.99", "3", True),
        ("5", "3", False),
        ("5.01", "3", False),
        ("3", "3", True),
        ("6", "3", False),
    ):
        closed, reference = _pair(**common, fee=fee, net_shortage=net_shortage)
        corollary = finite_r1_corollary(closed)
        assert corollary.in_window is expected
        assert corollary.in_window == classify(reference).backup_suppression_window

    for inventory, fee in ((3, 1), (2, 20)):
        closed, reference = _pair(
            horizon=20, short_duration=1, long_duration=3, lead=2,
            inventory=inventory, primary_price=1, backup_price="1.25",
            net_shortage=20, fee=fee, commitment="finite",
        )
        assert not finite_r1_corollary(closed).in_window
        assert not classify(reference).backup_suppression_window


def test_exhaustive_exact_period_enumeration() -> None:
    _budget_check()
    checked = 0
    corollaries = 0
    for horizon, short, long, lead in product(
        (6, 8, 20), (1, 2), (3, 4, 5, 6, 8), (1, 2, 3, 6)
    ):
        if long > horizon or lead >= horizon:
            continue
        for inventory, fee, commitment, backup_price in product(
            (0, "0.25", 1, 2, 3, 4, 5),
            (0, 1, 3, 6, 20),
            ("finite", "horizon"),
            (1, "1.25"),
        ):
            _budget_check()
            values = dict(horizon=horizon, short_duration=short,
                          long_duration=long, lead=lead,
                          inventory=inventory, primary_price=1,
                          backup_price=backup_price, net_shortage=20,
                          fee=fee, commitment=commitment)
            closed, reference = _pair(**values)
            checked += 1
            for duration in (short, long):
                no_backup = simulate(reference, duration, None)
                for date in (None, *range(horizon - lead)):
                    formula = formula_path(closed, duration, date)
                    enumerated = simulate(reference, duration, date)
                    assert formula.buyer_cost == enumerated.buyer_cost
                    assert formula.paid_backup == enumerated.paid_backup
                    assert formula.lost_sales == enumerated.lost_sales
                    assert formula.primary_delivered == enumerated.primary_delivered
                    assert formula.avoided_lost_sales == no_backup.lost_sales - enumerated.lost_sales
                    assert formula.surplus_backup == enumerated.inventory_end - no_backup.inventory_end
                    assert formula.saving == no_backup.buyer_cost - enumerated.buyer_cost
                best = choose(reference, duration)
                assert best.qualification_date == (
                    window_result(closed).literal_short.qualification_date
                    if duration == short else window_result(closed).truthful_long.qualification_date
                )

            closed_window = window_result(closed)
            enumerated_window = classify(reference)
            assert closed_window.truthful_long.qualification_date == enumerated_window.truthful_long.qualification_date
            assert closed_window.literal_short.qualification_date == enumerated_window.literal_short.qualification_date
            assert closed_window.after_revelation.qualification_date == enumerated_window.after_revelation.qualification_date
            assert closed_window.max_truth_saving == enumerated_window.maximum_truth_saving
            assert closed_window.max_delayed_saving == enumerated_window.maximum_delayed_saving
            assert closed_window.backup_suppression_window == enumerated_window.backup_suppression_window

            if (commitment == "finite" and short <= lead < long <= short + lead
                    and horizon >= (short - 1) + lead + long
                    and Fraction(horizon) >= Fraction(long) + max(Fraction(long), Fraction(inventory))):
                corollaries += 1
                assert finite_r1_corollary(closed).in_window == enumerated_window.backup_suppression_window
    assert checked == 14560
    assert corollaries > 0
