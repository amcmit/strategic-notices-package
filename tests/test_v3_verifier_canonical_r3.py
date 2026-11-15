"""Synthetic tuple/list regression for the versioned, no-MIP verifier fix."""

import json
from dataclasses import asdict
from hashlib import sha256
from types import SimpleNamespace

import pytest

from sn.allocation_v3 import canonical
from sn.core import Action, Demand, Notice, Realization
from sn.core_v3 import ContractV3, NetworkV3, initial_state_v3, step_v3
from sn.verify_false_notice_v3_r2 import _replay as old_replay
from sn.verify_false_notice_v3_r3 import _replay as new_replay


def test_canonical_replay_accepts_saved_json_lists_without_changing_values():
    network = NetworkV3(
        1, ("buyer",), ("critical",), "buyer", "critical",
        critical_site="buyer", critical_product="critical",
    )
    contract = ContractV3(revelation_period=0, spot_capacity=0)
    demand = ((Demand("buyer", "critical", 1),),)
    case = SimpleNamespace(network=network, contract=contract,
                           short_capacity=(0.0,), long_capacity=(0.0,),
                           baseline_demand=demand)
    action = Action()
    final, ledger = step_v3(
        network, contract, Notice(case.short_capacity, "minor"),
        initial_state_v3(network), action, Realization(0.0, demand[0]),
        revealed_schedule=case.long_capacity,
    )
    saved_ledger = json.loads(canonical(asdict(ledger)))
    assert asdict(ledger) != saved_ledger  # tuple in memory, list in JSON
    assert canonical(asdict(ledger)) == canonical(saved_ledger)
    saved = {"status": "completed", "periods": [{
        "period": 0, "action": asdict(action), "ledger": saved_ledger,
        "certificate": {"status": 0, "optimal": True, "primal": 0.0, "dual": 0.0},
    }], "final_state_sha256": sha256(canonical(asdict(final))).hexdigest()}
    with pytest.raises(ValueError, match="false-notice ledger replay differs"):
        old_replay(case, saved, cell="C10")
    assert new_replay(case, saved, cell="C10") == (ledger,)
