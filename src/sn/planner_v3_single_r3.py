"""R3 v3 rolling planner with one MIP per period.

The optimization is over the declared physical core, with a shared action
history until type revelation. Future demand is the caller's conditional mean;
only the first action is executed before the next observation and re-solve.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import inf, isfinite, sqrt
from typing import Literal
import warnings

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix
import scipy
from scipy.optimize._highspy import _core as highs_core

from .core import (
    Action, Demand, Notice, Production,
    Realization, Shipment, State, TOL,
)
from .core_v3 import ContractV3, NetworkV3, PeriodLedgerV3, initial_state_v3, step_v3
from .handoff_v3_single import (
    Handoff, MIP_FEASIBILITY_TOLERANCE, PRIMAL_FEASIBILITY_TOLERANCE,
    project_action,
)

POSTSOLVE_TOL = PRIMAL_FEASIBILITY_TOLERANCE
REQUESTED_MIP_FEAS_TOL = MIP_FEASIBILITY_TOLERANCE
REQUESTED_MIP_ABS_GAP = 0.0
TIE_EPSILON = 1e-3


def _objective_gap_tolerance(primal: float, dual: float) -> float:
    """Numerical closure for a status-0 MIP, in objective units.

    HiGHS is asked for zero MIP gaps, but its primal and dual feasibility
    tolerance is 1e-7. A final reported bound difference below that relative
    numerical scale is not evidence that the solver stopped early.
    """
    return PRIMAL_FEASIBILITY_TOLERANCE * max(1.0, abs(primal), abs(dual))


@dataclass(frozen=True)
class Scenario:
    name: str
    probability: float
    capacity: tuple[float, ...]


@dataclass(frozen=True)
class PlanningProblem:
    network: NetworkV3
    contract: ContractV3
    notice: Notice
    state: State
    scenarios: tuple[Scenario, ...]
    observed_capacity: float
    observed_demand: tuple[Demand, ...]
    demand_forecast: tuple[tuple[Demand, ...], ...]
    time_limit: float = 30.0
    presolve: bool = False
    threads: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.network, NetworkV3) or not isinstance(self.contract, ContractV3):
            raise TypeError("v3 planner requires NetworkV3 and ContractV3")
        if self.contract.demand_rule == "lost_sales" and self.state.backlog:
            raise ValueError("lost-sales planner state cannot contain backlog")
        if not isinstance(self.presolve, bool) or type(self.threads) is not int or self.threads not in (1, 2):
            raise ValueError("v3 presolve must be bool and threads must be 1 or 2")
        horizon, now = self.network.horizon, self.state.period
        if not 0 <= now < horizon:
            raise ValueError("state period outside horizon")
        if len(self.notice.announced_capacity) != horizon or len(self.demand_forecast) != horizon:
            raise ValueError("notice and demand forecast must span the horizon")
        if not isfinite(self.observed_capacity) or self.observed_capacity < 0:
            raise ValueError("observed capacity must be finite and nonnegative")
        if _demand_by_key(self.observed_demand) != _demand_by_key(self.demand_forecast[now]):
            raise ValueError("current forecast must equal observed demand")
        if not self.scenarios or len({x.name for x in self.scenarios}) != len(self.scenarios):
            raise ValueError("scenarios must be nonempty and uniquely named")
        if not isfinite(self.time_limit) or self.time_limit <= 0:
            raise ValueError("positive finite time limit required")
        if abs(sum(x.probability for x in self.scenarios) - 1) > 1e-9:
            raise ValueError("scenario probabilities must sum to one")
        for scenario in self.scenarios:
            if not isfinite(scenario.probability) or scenario.probability <= 0:
                raise ValueError("scenario probabilities must be positive")
            if len(scenario.capacity) != horizon or any(
                not isfinite(x) or x < 0 for x in scenario.capacity
            ):
                raise ValueError("scenario capacities must span the horizon")
            if abs(scenario.capacity[now] - self.observed_capacity) > 1e-9:
                raise ValueError("scenario conflicts with observed current capacity")
            if self.state.revealed_schedule is not None and tuple(scenario.capacity) != self.state.revealed_schedule:
                raise ValueError("scenario conflicts with revealed schedule")
        if now >= self.contract.revelation_period and len(self.scenarios) != 1:
            raise ValueError("only the revealed type remains after revelation")
        if len(self.scenarios) > 1:
            for period in range(now, min(horizon, self.contract.revelation_period)):
                if len({x.capacity[period] for x in self.scenarios}) != 1:
                    raise ValueError("types disagree before the stated revelation period")
        for period in self.demand_forecast:
            for item in period:
                self.network.check_key(item.site, item.product)


@dataclass(frozen=True)
class SolverCertificate:
    phase: str
    status: int
    message: str
    optimal: bool
    primal: float | None
    dual: float | None
    relative_gap: float | None
    nodes: int | None
    requested_relative_gap: float
    requested_mip_absolute_gap: float
    requested_mip_feasibility_tolerance: float
    requested_threads: int
    requested_presolve: bool
    requested_random_seed: int
    solver_name: str
    model_variables: int
    model_constraints: int
    model_integer_variables: int
    model_nonzeros: int
    time_limit_seconds: float
    checked_feasibility_tolerance: float
    checked_integrality_tolerance: float
    checked_primal_dual_absolute_tolerance: float
    scipy_version: str
    highs_version: str
    solver_tolerance_note: str


class PlannerFailure(RuntimeError):
    def __init__(self, certificate: SolverCertificate):
        self.certificate = certificate
        super().__init__(f"{certificate.phase} solve not certified: {certificate.message}")


@dataclass(frozen=True)
class PlanResult:
    action: Action
    expected_cost: float
    combined_objective: float
    primary_cost_loss_bound: float
    certificate: SolverCertificate
    handoff: Handoff


class _Model:
    def __init__(self) -> None:
        self.names: list[tuple] = []
        self.cost: list[float] = []
        self.lower: list[float] = []
        self.upper: list[float] = []
        self.integer: list[int] = []
        self.rows: list[dict[int, float]] = []
        self.row_lower: list[float] = []
        self.row_upper: list[float] = []

    def var(self, name: tuple, *, cost: float = 0, upper: float = inf,
            integer: bool = False) -> int:
        index = len(self.names)
        self.names.append(name)
        self.cost.append(cost)
        self.lower.append(0)
        self.upper.append(upper)
        self.integer.append(int(integer))
        return index

    def row(self, coefficients: dict[int, float], *, lower: float = -inf,
            upper: float = inf) -> None:
        compact = {index: value for index, value in coefficients.items() if abs(value) > 1e-12}
        self.rows.append(compact)
        self.row_lower.append(lower)
        self.row_upper.append(upper)

    def matrix(self):
        indices = [(row, column, value) for row, terms in enumerate(self.rows)
                   for column, value in terms.items()]
        if indices:
            r, c, data = zip(*indices)
        else:
            r, c, data = (), (), ()
        return coo_matrix((data, (r, c)), shape=(len(self.rows), len(self.names))).tocsr()


def _add(terms: dict[int, float], index: int, amount: float) -> None:
    terms[index] = terms.get(index, 0.0) + amount


def _demand_by_key(period: tuple[Demand, ...]) -> dict[tuple[str, str], float]:
    result: dict[tuple[str, str], float] = {}
    for item in period:
        key = (item.site, item.product)
        result[key] = result.get(key, 0.0) + item.quantity
    return result


def _certificate(result, phase: str, time_limit: float, model: _Model,
                 matrix, *, presolve: bool, threads: int) -> SolverCertificate:
    def finite_or_none(value):
        return float(value) if value is not None and isfinite(value) else None

    return SolverCertificate(
        phase=phase, status=int(result.status), message=str(result.message),
        optimal=bool(result.success and result.status == 0),
        primal=finite_or_none(getattr(result, "fun", None)),
        dual=finite_or_none(getattr(result, "mip_dual_bound", None)),
        relative_gap=finite_or_none(getattr(result, "mip_gap", None)),
        nodes=(int(result.mip_node_count) if getattr(result, "mip_node_count", None) is not None else None),
        requested_relative_gap=0.0,
        requested_mip_absolute_gap=REQUESTED_MIP_ABS_GAP,
        time_limit_seconds=time_limit,
        requested_mip_feasibility_tolerance=REQUESTED_MIP_FEAS_TOL,
        requested_threads=threads, requested_presolve=presolve,
        requested_random_seed=0,
        solver_name="HiGHS via scipy.optimize.milp",
        model_variables=len(model.names), model_constraints=matrix.shape[0],
        model_integer_variables=sum(model.integer), model_nonzeros=matrix.nnz,
        checked_feasibility_tolerance=POSTSOLVE_TOL,
        checked_integrality_tolerance=REQUESTED_MIP_FEAS_TOL,
        checked_primal_dual_absolute_tolerance=(
            _objective_gap_tolerance(float(result.fun), float(result.mip_dual_bound))
            if (getattr(result, "fun", None) is not None
                and getattr(result, "mip_dual_bound", None) is not None
                and isfinite(result.fun) and isfinite(result.mip_dual_bound))
            else PRIMAL_FEASIBILITY_TOLERANCE),
        scipy_version=scipy.__version__,
        highs_version=(f"{highs_core.HIGHS_VERSION_MAJOR}."
                       f"{highs_core.HIGHS_VERSION_MINOR}."
                       f"{highs_core.HIGHS_VERSION_PATCH}"),
        solver_tolerance_note="HiGHS relative/absolute gap zero, MIP feasibility 1e-8, primal/dual feasibility 1e-7, thread and seed options are forwarded through SciPy; status-0 reported bound closure uses 1e-7 times max(1, abs(primal), abs(dual)); residual checks include calculated dot-product roundoff.",
    )


def _solve(model: _Model, objective: np.ndarray, time_limit: float, phase: str,
           *, presolve: bool = False, threads: int = 1):
    """One HiGHS solve; accept its optimal status and audited tolerances."""
    matrix = model.matrix()
    row_lower = np.asarray(model.row_lower, dtype=float)
    row_upper = np.asarray(model.row_upper, dtype=float)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Unrecognized options detected.*",
                                category=RuntimeWarning)
        result = milp(
            c=objective, integrality=np.asarray(model.integer),
            bounds=Bounds(model.lower, model.upper),
            constraints=LinearConstraint(matrix, row_lower, row_upper),
            options={
                "mip_rel_gap": 0.0, "mip_abs_gap": REQUESTED_MIP_ABS_GAP,
                "mip_feasibility_tolerance": REQUESTED_MIP_FEAS_TOL,
                "primal_feasibility_tolerance": PRIMAL_FEASIBILITY_TOLERANCE,
                "dual_feasibility_tolerance": PRIMAL_FEASIBILITY_TOLERANCE,
                "time_limit": time_limit, "presolve": presolve,
                "threads": threads, "random_seed": 0,
            },
        )
    certificate = _certificate(result, phase, time_limit, model, matrix,
                               presolve=presolve, threads=threads)
    if not certificate.optimal or result.x is None:
        raise PlannerFailure(certificate)
    vector = np.asarray(result.x, dtype=float)
    if vector.shape != (len(model.names),) or not np.all(np.isfinite(vector)):
        raise PlannerFailure(SolverCertificate(
            **{**certificate.__dict__, "optimal": False,
               "message": "optimal status returned a missing or nonfinite primal coordinate"}
        ))
    residual = matrix @ vector
    lower_violation = max(0.0, float(np.max(np.asarray(model.lower) - vector)))
    upper_violation = max(0.0, float(np.max(vector - np.asarray(model.upper))))
    row_lower_violation = max(0.0, float(np.max(row_lower - residual)))
    row_upper_violation = max(0.0, float(np.max(residual - row_upper)))
    integer_values = vector[np.asarray(model.integer) == 1]
    integer_violation = (max(0.0, float(np.max(np.abs(integer_values - np.round(integer_values)))))
                         if integer_values.size else 0.0)
    # Sparse row dot products accumulate floating-point rounding. The
    # allowance is calculated from the actual matrix and incumbent.
    row_scale = np.asarray(abs(matrix) @ abs(vector)).ravel()
    row_roundoff = 64 * np.finfo(float).eps * max(1.0, float(np.max(row_scale)))
    feasibility_limit = PRIMAL_FEASIBILITY_TOLERANCE + row_roundoff
    if (max(lower_violation, upper_violation, row_lower_violation,
            row_upper_violation) > feasibility_limit
            or integer_violation > REQUESTED_MIP_FEAS_TOL + row_roundoff):
        raise PlannerFailure(SolverCertificate(
            **{**certificate.__dict__, "optimal": False,
               "message": ("solver feasibility exceeds declared tolerance: "
                           f"variable=({lower_violation:.17g},{upper_violation:.17g}), "
                           f"row=({row_lower_violation:.17g},{row_upper_violation:.17g}), "
                           f"integer={integer_violation:.17g}, limit={feasibility_limit:.17g}")}
        ))
    if certificate.primal is None or certificate.dual is None:
        raise PlannerFailure(SolverCertificate(
            **{**certificate.__dict__, "optimal": False,
               "message": "optimal status lacks reported objective bounds"}
        ))
    gap = abs(certificate.primal - certificate.dual)
    gap_limit = certificate.checked_primal_dual_absolute_tolerance
    if gap > gap_limit:
        raise PlannerFailure(SolverCertificate(
            **{**certificate.__dict__, "optimal": False,
               "message": (f"solver objective gap {gap:.17g} exceeds "
                           f"feasibility-scaled closure {gap_limit:.17g}")}
        ))
    return vector, certificate


def _tie_vector(model: _Model, now: int) -> np.ndarray:
    """T in [0,1]: dominant qualify-now penalty and prime-root controls.

    The prime sequence and variable ordering are fixed. Distinct prime roots
    avoid rationally commensurate coordinate weights; continuous controls can
    still have arbitrarily close scores, so this is not a uniqueness proof.
    """
    tie = np.zeros(len(model.names))
    continuous = []
    for kind in ("order", "spot", "produce", "ship"):
        indices = [i for i, name in enumerate(model.names)
                   if name[0] == kind and name[1] == 0 and name[2] == now
                   and model.upper[i] > 0 and isfinite(model.upper[i])]
        continuous.extend(indices)
    primes = []
    candidate = 2
    while len(primes) < len(continuous):
        if all(candidate % prime for prime in primes if prime * prime <= candidate):
            primes.append(candidate)
        candidate += 1
    weights = [sqrt(prime) for prime in primes]
    total_weight = sum(weights)
    if total_weight:
        for index, weight in zip(continuous, weights):
            tie[index] = 0.25 * weight / (total_weight * model.upper[index])
    qualify = next(i for i, name in enumerate(model.names)
                   if name == ("qualify", 0, now, None))
    tie[qualify] = 0.75
    return tie


def plan(problem: PlanningProblem) -> PlanResult:
    """Optimize and return the common first action and solver certificates."""
    net, contract, notice, state = problem.network, problem.contract, problem.notice, problem.state
    now, horizon = state.period, net.horizon
    scenarios = problem.scenarios
    keys = sorted({(x.site, x.product) for x in net.initial_stock}
                  | {(net.primary_site, net.primary_product), net.backup_key, net.spot_key}
                  | {(x.site, x.output) for x in net.recipes}
                  | {(x.site, product) for x in net.recipes for product, _ in x.inputs}
                  | {(x.origin, x.product) for x in net.lanes}
                  | {(x.destination, x.product) for x in net.lanes}
                  | {(x.site, x.product) for period in problem.demand_forecast for x in period}
                  | {(x.site, x.product) for x in state.inventory + state.backlog + state.pipeline})
    demand_keys = sorted({(x.site, x.product) for period in problem.demand_forecast for x in period}
                         | {(x.site, x.product) for x in state.backlog})
    demand = [_demand_by_key(period) for period in problem.demand_forecast]
    initial_stock = {(x.site, x.product): x.quantity for x in state.inventory}
    initial_backlog = {(x.site, x.product): x.quantity for x in state.backlog}
    initial_pipeline: dict[tuple[int, tuple[str, str]], float] = {}
    for item in state.pipeline:
        key = (item.due, (item.site, item.product))
        initial_pipeline[key] = initial_pipeline.get(key, 0.0) + item.quantity
    existing_due = {period: state.outstanding_for(period) for period in range(now, horizon)}
    maximum_supply = max(max(x.capacity) for x in scenarios)
    maximum_supply = max(maximum_supply, max(notice.announced_capacity))
    total_demand = sum(sum(p.values()) for p in demand[now:])
    big = max(1.0, sum(initial_stock.values()) + sum(initial_backlog.values())
              + sum(item.quantity for item in state.pipeline)
              + total_demand + sum(existing_due.values())
              + (horizon - now) * (maximum_supply + contract.backup_minimum
                                   + sum(recipe.capacity for recipe in net.recipes) + 1))

    model = _Model()
    variable: dict[tuple, int] = {}

    def make(kind: str, scenario_index: int, period: int, item=None, **kwargs) -> int:
        name = (kind, scenario_index, period, item)
        index = model.var(name, **kwargs)
        variable[name] = index
        return index

    def v(kind: str, s: int, t: int, item=None) -> int:
        return variable[kind, s, t, item]

    constant_cost = 0.0
    for s, scenario in enumerate(scenarios):
        probability = scenario.probability
        if state.qualified_at is not None:
            active = sum(contract.backup_active_at(state.qualified_at, t)
                         for t in range(now, horizon))
            constant_cost += probability * active * contract.backup_minimum * contract.backup_price
        for t in range(now, horizon):
            due_date = t + contract.order_lead
            if due_date >= horizon:
                q_bound = 0.0
            else:
                schedule = notice.announced_capacity if t < contract.revelation_period else scenario.capacity
                q_bound = max(0.0, schedule[due_date] - state.outstanding_for(due_date))
            make("order", s, t, upper=q_bound)
            spot_bound = big if contract.spot_capacity == inf else contract.spot_capacity
            make("spot", s, t, cost=probability * contract.spot_price, upper=spot_bound)
            qual_bound = 1 if state.qualified_at is None and t + contract.qualification_lead < horizon else 0
            later_active = sum(contract.backup_active_at(t, p) for p in range(t, horizon))
            make("qualify", s, t, integer=True, upper=qual_bound,
                 cost=probability * (contract.qualification_cost
                                     + later_active * contract.backup_minimum * contract.backup_price))
            make("delivered", s, t, cost=probability * contract.primary_price,
                 upper=scenario.capacity[t])
            make("delivery_branch", s, t, integer=True, upper=1)
            for recipe_index, recipe in enumerate(net.recipes):
                make("produce", s, t, recipe_index, upper=recipe.capacity,
                     cost=probability * recipe.unit_cost)
            for lane_index, lane in enumerate(net.lanes):
                upper = lane.capacity if t + lane.lead < horizon else 0.0
                make("ship", s, t, lane_index, upper=upper,
                     cost=probability * lane.unit_cost)
            for key in keys:
                make("stock", s, t, key, upper=big)
            for key in demand_keys:
                if contract.demand_rule == "lost_sales":
                    make("lost", s, t, key, upper=big,
                         cost=probability * (contract.primary_price
                                             + contract.net_shortage_value))
                else:
                    make("backlog", s, t, key, upper=big,
                         cost=probability * contract.backlog_cost)
                    if t > now:
                        make("catch", s, t, key, upper=contract.catchup_capacity)
                        make("catch_branch", s, t, key, integer=True, upper=1)
                make("served", s, t, key, upper=big)
                make("service_branch", s, t, key, integer=True, upper=1)

    primary_key = (net.primary_site, net.primary_product)

    def backup_terms(s: int, t: int) -> tuple[float, dict[int, float]]:
        if state.qualified_at is not None:
            value = contract.backup_minimum if contract.backup_active_at(state.qualified_at, t) else 0.0
            return value, {}
        return 0.0, {
            v("qualify", s, q): contract.backup_minimum
            for q in range(now, t - contract.qualification_lead + 1)
            if contract.backup_active_at(q, t)
        }

    for s, scenario in enumerate(scenarios):
        if state.qualified_at is None:
            model.row({v("qualify", s, t): 1 for t in range(now, horizon)}, upper=1)
        for t in range(now, horizon):
            # Due commitment is an immutable old order plus an order placed
            # within this optimization exactly one lead period earlier.
            due_const = existing_due[t]
            due_terms = {}
            order_time = t - contract.order_lead
            if order_time >= now:
                due_terms[v("order", s, order_time)] = 1.0
            delivered = v("delivered", s, t)
            branch = v("delivery_branch", s, t)
            capacity = scenario.capacity[t]
            # delivered = min(due, capacity), with a finite disjunction.
            model.row({delivered: 1, **{k: -x for k, x in due_terms.items()}},
                      upper=due_const)
            model.row({delivered: 1}, upper=capacity)
            model.row({delivered: 1, branch: big,
                       **{k: -x for k, x in due_terms.items()}}, lower=due_const)
            model.row({delivered: 1, branch: -big}, lower=capacity - big)

            backup_const, backup_variables = backup_terms(s, t)
            for key in keys:
                terms: dict[int, float] = {v("stock", s, t, key): 1}
                right = (initial_stock.get(key, 0.0) if t == now else 0.0)
                if t > now:
                    _add(terms, v("stock", s, t - 1, key), -1)
                right += initial_pipeline.get((t, key), 0.0)
                for q in range(now, t):
                    for lane_index, lane in enumerate(net.lanes):
                        if q + lane.lead == t and (lane.destination, lane.product) == key:
                            _add(terms, v("ship", s, q, lane_index), -1)
                if key == primary_key:
                    _add(terms, delivered, -1)
                if key == net.spot_key:
                    _add(terms, v("spot", s, t), -1)
                if key == net.backup_key:
                    right += backup_const
                    for index, quantity in backup_variables.items():
                        _add(terms, index, -quantity)
                for recipe_index, recipe in enumerate(net.recipes):
                    if (recipe.site, recipe.output) == key:
                        _add(terms, v("produce", s, t, recipe_index), -1)
                    for product, coefficient in recipe.inputs:
                        if (recipe.site, product) == key:
                            _add(terms, v("produce", s, t, recipe_index), coefficient)
                for lane_index, lane in enumerate(net.lanes):
                    if (lane.origin, lane.product) == key:
                        _add(terms, v("ship", s, t, lane_index), 1)
                if key in demand_keys:
                    _add(terms, v("served", s, t, key), 1)
                model.row(terms, lower=right, upper=right)

            # Inputs for all simultaneous recipes must exist before any
            # same-period recipe output or shipment can be used.
            for key in keys:
                input_terms: dict[int, float] = {}
                for recipe_index, recipe in enumerate(net.recipes):
                    for product, coefficient in recipe.inputs:
                        if (recipe.site, product) == key:
                            _add(input_terms, v("produce", s, t, recipe_index), coefficient)
                if not input_terms:
                    continue
                right = (initial_stock.get(key, 0.0) if t == now else 0.0)
                if t > now:
                    _add(input_terms, v("stock", s, t - 1, key), -1)
                right += initial_pipeline.get((t, key), 0.0)
                for q in range(now, t):
                    for lane_index, lane in enumerate(net.lanes):
                        if q + lane.lead == t and (lane.destination, lane.product) == key:
                            _add(input_terms, v("ship", s, q, lane_index), -1)
                if key == primary_key:
                    _add(input_terms, delivered, -1)
                if key == net.spot_key:
                    _add(input_terms, v("spot", s, t), -1)
                if key == net.backup_key:
                    right += backup_const
                    for index, quantity in backup_variables.items():
                        _add(input_terms, index, -quantity)
                model.row(input_terms, upper=right)

            for key in demand_keys:
                served = v("served", s, t, key)
                right = demand[t].get(key, 0.0)
                branch = v("service_branch", s, t, key)
                model.row({v("stock", s, t, key): 1, branch: -big}, upper=0)
                if contract.demand_rule == "lost_sales":
                    lost = v("lost", s, t, key)
                    model.row({lost: 1, served: 1}, lower=right, upper=right)
                    model.row({lost: 1, branch: big}, upper=big)
                else:
                    backlog = v("backlog", s, t, key)
                    terms = {backlog: 1, served: 1}
                    if t == now:
                        opening = initial_backlog.get(key, 0.0)
                        model.row(terms, lower=right + opening,
                                  upper=right + opening)
                        eligible = right + min(opening, contract.catchup_capacity)
                        model.row({served: 1}, upper=eligible)
                        model.row({served: 1, branch: -big},
                                  lower=eligible - big)
                    else:
                        previous = v("backlog", s, t - 1, key)
                        catch = v("catch", s, t, key)
                        catch_branch = v("catch_branch", s, t, key)
                        _add(terms, previous, -1)
                        model.row(terms, lower=right, upper=right)
                        # catch = min(previous backlog, per-period cap).
                        model.row({catch: 1, previous: -1}, upper=0)
                        model.row({catch: 1, previous: -1,
                                   catch_branch: -big}, lower=-big)
                        model.row({catch: 1, catch_branch: big},
                                  lower=contract.catchup_capacity)
                        model.row({served: 1, catch: -1}, upper=right)
                        model.row({served: 1, catch: -1,
                                   branch: -big}, lower=right - big)

    if len(scenarios) > 1:
        for s in range(1, len(scenarios)):
            for t in range(now, min(horizon, contract.revelation_period)):
                for kind in ("order", "spot", "qualify", "delivered"):
                    model.row({v(kind, s, t): 1, v(kind, 0, t): -1}, lower=0, upper=0)
                for recipe_index in range(len(net.recipes)):
                    model.row({v("produce", s, t, recipe_index): 1,
                               v("produce", 0, t, recipe_index): -1}, lower=0, upper=0)
                for lane_index in range(len(net.lanes)):
                    model.row({v("ship", s, t, lane_index): 1,
                               v("ship", 0, t, lane_index): -1}, lower=0, upper=0)
                for key in keys:
                    model.row({v("stock", s, t, key): 1,
                               v("stock", 0, t, key): -1}, lower=0, upper=0)
                for key in demand_keys:
                    outcome_kind = "lost" if contract.demand_rule == "lost_sales" else "backlog"
                    for kind in (outcome_kind, "served"):
                        model.row({v(kind, s, t, key): 1, v(kind, 0, t, key): -1},
                                  lower=0, upper=0)

    primary_cost = np.asarray(model.cost, dtype=float)
    tie = _tie_vector(model, now)
    combined = primary_cost + TIE_EPSILON * tie
    solution, certificate = _solve(
        model, combined, problem.time_limit, "single",
        presolve=problem.presolve, threads=problem.threads,
    )
    qualifier = solution[v("qualify", 0, now)]
    if abs(qualifier - round(qualifier)) > REQUESTED_MIP_FEAS_TOL:
        raise PlannerFailure(SolverCertificate(
            **{**certificate.__dict__, "optimal": False,
               "message": f"executed qualification is noninteger: {qualifier:.17g}"}
        ))
    raw = Action(
        qualify=bool(round(qualifier)),
        primary_order=float(solution[v("order", 0, now)]),
        spot_order=float(solution[v("spot", 0, now)]),
        production=tuple(
            Production(recipe.site, recipe.output,
                       float(solution[v("produce", 0, now, index)]))
            for index, recipe in enumerate(net.recipes)),
        shipments=tuple(
            Shipment(index, float(solution[v("ship", 0, now, index)]))
            for index in range(len(net.lanes))),
    )
    handoff = project_action(
        net, contract, now, raw,
        accepted_order_limit=model.upper[v("order", 0, now)],
    )
    reveal = (problem.scenarios[0].capacity
              if now == contract.revelation_period else None)
    step_v3(net, contract, notice, state, handoff.action,
            Realization(problem.observed_capacity, problem.observed_demand),
            revealed_schedule=reveal)
    primary_recomputed = float(primary_cost @ solution)
    combined_recomputed = float(combined @ solution)
    objective_roundoff = abs(combined_recomputed - float(certificate.primal))
    bound_gap = abs(float(certificate.primal) - float(certificate.dual))
    cost_loss_bound = TIE_EPSILON + bound_gap + objective_roundoff
    return PlanResult(
        handoff.action, primary_recomputed + constant_cost,
        combined_recomputed + constant_cost, cost_loss_bound,
        certificate, handoff,
    )


Benchmark = Literal["literal", "prior_only", "full_information", "clairvoyant"]


@dataclass(frozen=True)
class Rollout:
    benchmark: Benchmark
    ledgers: tuple[PeriodLedgerV3, ...]
    actions: tuple[Action, ...]
    certificates: tuple[SolverCertificate, ...]
    handoffs: tuple[Handoff, ...]
    operating_cost: float


def run_benchmark(
    *, network: NetworkV3, contract: ContractV3, notice: Notice,
    short_capacity: tuple[float, ...], long_capacity: tuple[float, ...],
    actual_type: Literal["short", "long"],
    realized_demand: tuple[tuple[Demand, ...], ...],
    mean_demand: tuple[tuple[Demand, ...], ...],
    benchmark: Benchmark, prior_severe: float, literal_severe: float,
    time_limit: float = 30.0, presolve: bool = False, threads: int = 1,
) -> Rollout:
    """Execute a rolling benchmark on one physical realization.

    All benchmarks see current demand. Clairvoyant alone sees future realized
    demand; full information knows the supplier type but uses mean future demand.
    """
    horizon = network.horizon
    if (len(short_capacity) != horizon or len(long_capacity) != horizon
            or len(realized_demand) != horizon or len(mean_demand) != horizon):
        raise ValueError("benchmark schedules must span the horizon")
    if any(not 0 <= x <= 1 for x in (prior_severe, literal_severe)):
        raise ValueError("belief probabilities must be in [0,1]")
    if benchmark not in ("literal", "prior_only", "full_information", "clairvoyant"):
        raise ValueError("unknown benchmark")
    if actual_type not in ("short", "long"):
        raise ValueError("actual type must be short or long")
    actual = short_capacity if actual_type == "short" else long_capacity
    state = initial_state_v3(network)
    ledgers: list[PeriodLedgerV3] = []
    actions: list[Action] = []
    certificates: list[SolverCertificate] = []
    handoffs: list[Handoff] = []
    for t in range(horizon):
        forecast = list(realized_demand if benchmark == "clairvoyant" else mean_demand)
        forecast[t] = realized_demand[t]
        if t >= contract.revelation_period or benchmark in ("full_information", "clairvoyant"):
            scenarios = (Scenario(actual_type, 1.0, actual),)
        else:
            severe = literal_severe if benchmark == "literal" else prior_severe
            candidates = (("short", 1 - severe, short_capacity),
                          ("long", severe, long_capacity))
            scenarios = tuple(Scenario(name, probability, capacity)
                              for name, probability, capacity in candidates if probability > 0)
        problem = PlanningProblem(network, contract, notice, state, scenarios,
                                  actual[t], realized_demand[t], tuple(forecast),
                                  time_limit, presolve, threads)
        choice = plan(problem)
        state, ledger = step_v3(
            network, contract, notice, state, choice.action,
            Realization(actual[t], realized_demand[t]),
            revealed_schedule=actual if t == contract.revelation_period else None,
        )
        ledgers.append(ledger)
        actions.append(choice.action)
        certificates.append(choice.certificate)
        handoffs.append(choice.handoff)
    return Rollout(benchmark, tuple(ledgers), tuple(actions), tuple(certificates),
                   tuple(handoffs),
                   sum(row.operating_cost for row in ledgers))
