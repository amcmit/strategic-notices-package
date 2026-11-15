"""Versioned finite-commitment contract and physical transition for v2.

The v1 transition remains the independent conservation and obligation engine.
This adapter changes only whether mandatory backup supply is active at a
given period, and audits the resulting delivery and charge explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .core import (
    Action, Contract, CoreNetwork, Notice, PeriodLedger, Realization, State,
    TOL, audit_step, step,
)


@dataclass(frozen=True)
class ContractV2(Contract):
    # None reproduces v1's through-horizon mandatory commitment.
    backup_commitment_periods: int | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        length = self.backup_commitment_periods
        if length is not None and (type(length) is not int or length < 1):
            raise ValueError("backup commitment must be a positive integer or through horizon")

    def backup_active_at(self, qualified_at: int | None, period: int) -> bool:
        if qualified_at is None:
            return False
        activation = qualified_at + self.qualification_lead
        return period >= activation and (
            self.backup_commitment_periods is None
            or period < activation + self.backup_commitment_periods
        )


def step_v2(
    network: CoreNetwork, contract: ContractV2, notice: Notice, state: State,
    action: Action, realization: Realization,
    *, revealed_schedule: tuple[float, ...] | None = None,
) -> tuple[State, PeriodLedger]:
    """Apply one v2 period, including expiry of mandatory delivery and pay."""
    if not isinstance(contract, ContractV2):
        raise TypeError("v2 transition requires ContractV2")
    effective = contract if contract.backup_active_at(state.qualified_at, state.period) \
        else replace(contract, backup_minimum=0)
    after, ledger = step(
        network, effective, notice, state, action, realization,
        revealed_schedule=revealed_schedule,
    )
    audit_step_v2(network, contract, state, after, ledger, action)
    return after, ledger


def audit_step_v2(
    network: CoreNetwork, contract: ContractV2, before: State,
    after: State, ledger: PeriodLedger, action: Action,
) -> None:
    """Check v1 balances plus the v2 commitment and qualification semantics."""
    audit_step(network, before, after, ledger)
    expected = contract.backup_minimum if contract.backup_active_at(
        before.qualified_at, before.period) else 0.0
    if abs(ledger.backup_delivered - expected) > TOL:
        raise ValueError("v2 mandatory backup delivery differs from commitment")
    if abs(ledger.backup_cost - contract.backup_price * expected) > TOL:
        raise ValueError("v2 backup payment differs from commitment")
    if abs(ledger.qualification_fee - (contract.qualification_cost if action.qualify else 0)) > TOL:
        raise ValueError("v2 qualification fee mismatch")
    if action.qualify and (before.qualified_at is not None or after.qualified_at != before.period):
        raise ValueError("v2 qualification is not one-time")
    if not action.qualify and after.qualified_at != before.qualified_at:
        raise ValueError("v2 qualification date changed without action")
