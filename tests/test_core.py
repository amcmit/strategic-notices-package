import pytest

from sn.core import (
    Action, Contract, CoreNetwork, Demand, Lane, Notice, Production, Realization,
    Recipe, Shipment, Stock, audit_step, initial_state, step, supplier_payoff,
)


def basic_network(*, initial=0, horizon=6):
    return CoreNetwork(
        horizon=horizon, sites=("shop",), products=("unit",),
        primary_site="shop", primary_product="unit",
        initial_stock=(Stock("shop", "unit", initial),),
    )


def basic_notice():
    return Notice((1, 0, 1, 1, 1, 1), "Minor: recovery at period 2")


def test_commitment_survives_disclosure_and_new_acceptance_is_corrected():
    net, contract, notice = basic_network(initial=1), Contract(), basic_notice()
    state = initial_state(net)
    before = state
    state, first = step(net, contract, notice, state, Action(primary_order=1),
                        Realization(1, (Demand("shop", "unit", 1),)))
    audit_step(net, before, state, first)
    assert first.accepted_new == 0  # Announced outage for delivery in period 1.
    before = state
    state, second = step(net, contract, notice, state, Action(primary_order=1, spot_order=1),
                         Realization(0, (Demand("shop", "unit", 1),)))
    audit_step(net, before, state, second)
    assert second.accepted_new == 1
    assert state.outstanding_for(2) == 1
    actual = (1, 0, 0, 0, 1, 1)
    before = state
    state, third = step(net, contract, notice, state, Action(primary_order=1, spot_order=1),
                        Realization(0, (Demand("shop", "unit", 1),)),
                        revealed_schedule=actual)
    audit_step(net, before, state, third)
    assert (third.primary_due, third.primary_delivered, third.primary_shortfall) == (1, 0, 1)
    assert third.accepted_new == 0
    assert state.outstanding_for(3) == 0
    state, fourth = step(net, contract, notice, state, Action(primary_order=1, spot_order=1),
                         Realization(0, (Demand("shop", "unit", 1),)))
    assert fourth.accepted_new == 1
    state, fifth = step(net, contract, notice, state, Action(),
                        Realization(1, (Demand("shop", "unit", 1),)))
    assert fifth.primary_delivered == 1
    assert state.stock("shop", "unit") == 0
    assert state.revealed_schedule == actual
    with pytest.raises(ValueError, match="cannot change"):
        step(net, contract, notice, state, Action(), Realization(1),
             revealed_schedule=(1, 0, 1, 1, 1, 1))


def test_aggregate_acceptance_capacity_and_no_post_horizon_order():
    net, contract = basic_network(horizon=3), Contract()
    state = initial_state(net)
    state, first = step(net, contract, notice=Notice((1, 1, 1), ""), state=state,
                        action=Action(primary_order=2), realization=Realization(1))
    assert first.accepted_new == 1
    assert state.outstanding_for(1) == 1
    with pytest.raises(ValueError, match="horizon"):
        step(net, contract, Notice((1, 1, 1), ""),
             initial_state(net).at_period(2), Action(primary_order=1), Realization(1),
             revealed_schedule=(1, 1, 1))


def test_mandatory_backup_delivery_is_paid_and_excess_is_inventoried():
    net = basic_network(horizon=4)
    contract = Contract(backup_minimum=2, qualification_lead=2, backup_price=3,
                        qualification_cost=5)
    notice = Notice((1, 1, 1, 1), "major")
    state = initial_state(net)
    state, zero = step(net, contract, notice, state, Action(qualify=True), Realization(1))
    assert zero.operating_cost == 5
    assert state.qualified_at == 0
    state, one = step(net, contract, notice, state, Action(), Realization(1))
    assert one.backup_delivered == 0
    with pytest.raises(ValueError, match="corrected schedule required"):
        step(net, contract, notice, state, Action(), Realization(1))
    state, two = step(net, contract, notice, state, Action(),
                      Realization(1, (Demand("shop", "unit", 1),)),
                      revealed_schedule=(1, 1, 1, 1))
    assert two.backup_delivered == 2
    assert two.operating_cost == 6
    assert state.stock("shop", "unit") == 1
    with pytest.raises(ValueError, match="qualified"):
        step(net, contract, notice, state, Action(qualify=True), Realization(1))


def test_product_recipe_and_shipping_preserve_quantities():
    net = CoreNetwork(
        horizon=4, sites=("plant", "store"), products=("part", "finished"),
        primary_site="plant", primary_product="part",
        initial_stock=(Stock("plant", "part", 2),),
        recipes=(Recipe("plant", "finished", (("part", 2),), capacity=1, unit_cost=4),),
        lanes=(Lane("plant", "store", "finished", lead=2, capacity=1, unit_cost=1),),
    )
    state = initial_state(net)
    state, first = step(net, Contract(), Notice((1, 1, 1, 1), ""), state,
                        Action(production=(Production("plant", "finished", 1),),
                               shipments=(Shipment(0, 1),)), Realization(1))
    assert state.stock("plant", "part") == 0
    assert state.stock("plant", "finished") == 0
    assert first.production_cost == 4
    assert first.transport_cost == 1
    assert len(state.pipeline) == 1
    state, _ = step(net, Contract(), Notice((1, 1, 1, 1), ""), state,
                    Action(), Realization(1))
    state, arrival = step(net, Contract(), Notice((1, 1, 1, 1), ""), state,
                          Action(), Realization(1, (Demand("store", "finished", 1),)),
                          revealed_schedule=(1, 1, 1, 1))
    assert arrival.arrivals == (Stock("store", "finished", 1),)
    assert arrival.served == (Stock("store", "finished", 1),)
    assert state.stock("store", "finished") == 0


def test_infeasible_assembly_and_transport_fail_without_mutating_state():
    net = CoreNetwork(
        horizon=3, sites=("plant", "store"), products=("part", "finished"),
        primary_site="plant", primary_product="part",
        initial_stock=(Stock("plant", "part", 1),),
        recipes=(Recipe("plant", "finished", (("part", 2),), capacity=1),),
        lanes=(Lane("plant", "store", "finished", lead=1, capacity=1),),
    )
    state = initial_state(net)
    notice = Notice((1, 1, 1), "")
    with pytest.raises(ValueError, match="component"):
        step(net, Contract(), notice, state,
             Action(production=(Production("plant", "finished", 1),)), Realization(1))
    with pytest.raises(ValueError, match="stock"):
        step(net, Contract(), notice, state,
             Action(shipments=(Shipment(0, 1),)), Realization(1))
    assert state.stock("plant", "part") == 1


def test_production_is_simultaneous_and_ledger_balances_components():
    net = CoreNetwork(
        horizon=3, sites=("plant",), products=("raw", "intermediate", "final"),
        primary_site="plant", primary_product="raw",
        initial_stock=(Stock("plant", "raw", 2),),
        recipes=(
            Recipe("plant", "intermediate", (("raw", 2),), capacity=1),
            Recipe("plant", "final", (("intermediate", 1),), capacity=1),
        ),
    )
    state = initial_state(net)
    with pytest.raises(ValueError, match="component"):
        step(net, Contract(), Notice((1, 1, 1), ""), state,
             Action(production=(Production("plant", "intermediate", 1),
                                Production("plant", "final", 1))), Realization(1))
    assert state.stock("plant", "raw") == 2
    next_state, ledger = step(net, Contract(), Notice((1, 1, 1), ""), state,
                              Action(production=(Production("plant", "intermediate", 1),)),
                              Realization(1))
    audit_step(net, state, next_state, ledger)
    assert ledger.opening_stock == (Stock("plant", "raw", 2),)
    assert ledger.closing_stock == (Stock("plant", "intermediate", 1),)
    assert next_state.inventory == ledger.closing_stock


def test_supplier_penalty_reuses_ledger_and_text_does_not_change_contract():
    net, contract = basic_network(horizon=3), Contract()
    a, b = Notice((1, 1, 1), "short"), Notice((1, 1, 1), "maybe short")
    assert a != b  # Exact-input caches must retain the wording distinction.
    state = initial_state(net)
    state, _ = step(net, contract, a, state, Action(primary_order=1), Realization(1))
    x, led_a = step(net, contract, a, state, Action(spot_order=1),
                    Realization(0, (Demand("shop", "unit", 1),)))
    y, led_b = step(net, contract, b, state, Action(spot_order=1),
                    Realization(0, (Demand("shop", "unit", 1),)))
    assert (x, led_a) == (y, led_b)
    assert supplier_payoff((led_a,), margin=3, penalty=5) == -5
    assert supplier_payoff((led_a,), margin=3, penalty=7) == -7
    assert led_a.operating_cost == 6
