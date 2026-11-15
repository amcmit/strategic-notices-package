import pytest

from sn.toy import (
    ToyContract,
    ToyNotice,
    enumerate_qualification,
    simulate_toy,
    supplier_payoff,
)


@pytest.mark.parametrize(
    ("notice", "never", "now", "choice"),
    [("minor", 10, 16, None), ("major", 20, 16, 0)],
)
def test_hand_calculated_planner_costs(notice, never, now, choice):
    contract = ToyContract()
    costs = enumerate_qualification(ToyNotice.named(notice), contract)
    assert costs[None] == never
    assert costs[0] == now
    assert min(costs, key=lambda q: (costs[q], q is not None, q or 0)) == choice


@pytest.mark.parametrize(
    ("actual", "notice", "delivered", "shortfall", "cost"),
    [
        ("short", "minor", 4, 0, 10),
        ("long", "minor", 2, 1, 20),
        ("short", "major", 0, 0, 16),
        ("long", "major", 0, 0, 16),
    ],
)
def test_toy_outcomes(actual, notice, delivered, shortfall, cost):
    result = simulate_toy(actual, ToyNotice.named(notice), ToyContract())
    assert sum(row.primary_delivered for row in result.periods) == delivered
    assert sum(row.primary_shortfall for row in result.periods) == shortfall
    assert result.planner_cost == cost
    assert all(row.primary_delivered + row.backup_delivered + row.spot + row.initial_inventory == 1 for row in result.periods)


def test_long_type_incentive_threshold_and_penalty_reuse():
    minor = simulate_toy("long", ToyNotice.named("minor"), ToyContract())
    major = simulate_toy("long", ToyNotice.named("major"), ToyContract())
    assert supplier_payoff(minor, margin=3, penalty=5) > supplier_payoff(major, margin=3, penalty=5)
    assert supplier_payoff(minor, margin=3, penalty=6) == supplier_payoff(major, margin=3, penalty=6)
    assert supplier_payoff(minor, margin=3, penalty=7) < supplier_payoff(major, margin=3, penalty=7)
    assert minor.planner_cost == 20  # Changing the settled penalty does not change the path.


def test_text_never_changes_acceptance_or_physical_state():
    a = ToyNotice.named("minor", text="Recovery expected soon.")
    b = ToyNotice.named("minor", text="We think recovery may be soon.")
    assert simulate_toy("long", a, ToyContract()) == simulate_toy("long", b, ToyContract())


def test_disclosure_changes_only_future_acceptance():
    result = simulate_toy("long", ToyNotice.named("minor"), ToyContract())
    assert result.periods[2].primary_accepted == 1
    assert result.periods[2].primary_shortfall == 1
    assert result.periods[3].primary_accepted == 0
    assert result.periods[4].primary_delivered == 1
