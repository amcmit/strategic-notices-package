"""Versioned v2 rolling planner with finite backup commitment and certified qualification.

The optimization is over the declared physical core, with a shared action
history until type revelation. Future demand is the caller's conditional mean;
only the first action is executed before the next observation and re-solve.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import inf, isfinite
from typing import Literal
import warnings

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix
import scipy
from scipy.optimize._highspy import _core as highs_core

from .core import (
    Action, CoreNetwork, Demand, Notice, PeriodLedger, Production,
    Realization, Shipment, State, TOL, _add as _core_add, initial_state,
)
from .core_v2 import ContractV2, step_v2

POSTSOLVE_TOL = 1e-7
PRIMAL_DUAL_ABS_TOL = 1e-6
REQUESTED_MIP_FEAS_TOL = 1e-8
QUALIFICATION_STRICT_TOL = 1e-6
QUALIFICATION_TIE_TOL = 1e-8
REQUESTED_MIP_ABS_GAP = 0.0
# The independent secondary checks apply even to a status-zero HiGHS result.
# The objective includes small coefficients and continuous order quantities,
# so no positive separation between distinct feasible plans can be assumed.
SECONDARY_ABS_GAP_MAX = 1e-8
SECONDARY_REL_GAP_MAX = 1e-6


@dataclass(frozen=True)
class Scenario:
    name: str
    probability: float
    capacity: tuple[float, ...]


@dataclass(frozen=True)
class PlanningProblem:
    network: CoreNetwork
    contract: ContractV2
    notice: Notice
    state: State
    scenarios: tuple[Scenario, ...]
    observed_capacity: float
    observed_demand: tuple[Demand, ...]
    demand_forecast: tuple[tuple[Demand, ...], ...]
    time_limit: float = 30.0

    def __post_init__(self) -> None:
        if not isinstance(self.contract, ContractV2):
            raise TypeError("v2 planner requires ContractV2")
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
    tie_cost_delta: float
    primary_certificate: SolverCertificate
    tie_certificate: SolverCertificate
    current_tie_certificates: tuple[SolverCertificate, ...]
    qualification_branch_certificate: SolverCertificate | None
    qualification_classification: str
    actions_by_scenario: tuple[tuple[str, tuple[Action, ...]], ...]
    execution_shipment_correction_l1: float
    rounding_weight_sum: float
    rounding_analytic_bound: float
    rounding_fp_allowance: float
    unrounded_tie_score: float
    rounded_tie_score: float
    certified_tie_band_upper: float


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
                 matrix) -> SolverCertificate:
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
        requested_threads=1, requested_random_seed=0,
        solver_name="HiGHS via scipy.optimize.milp",
        model_variables=len(model.names), model_constraints=matrix.shape[0],
        model_integer_variables=sum(model.integer), model_nonzeros=matrix.nnz,
        checked_feasibility_tolerance=POSTSOLVE_TOL,
        checked_integrality_tolerance=POSTSOLVE_TOL,
        checked_primal_dual_absolute_tolerance=PRIMAL_DUAL_ABS_TOL,
        scipy_version=scipy.__version__,
        highs_version=(f"{highs_core.HIGHS_VERSION_MAJOR}."
                       f"{highs_core.HIGHS_VERSION_MINOR}."
                       f"{highs_core.HIGHS_VERSION_PATCH}"),
        solver_tolerance_note="HiGHS absolute gap, feasibility tolerance, thread and seed options are forwarded through SciPy's unrecognized-option pass-through; postsolve residuals are checked explicitly.",
    )


def _solve(model: _Model, objective: np.ndarray, time_limit: float, phase: str,
           *, primary_ceiling: float | None = None):
    matrix = model.matrix()
    row_lower = np.asarray(model.row_lower, dtype=float)
    row_upper = np.asarray(model.row_upper, dtype=float)
    if primary_ceiling is not None:
        from scipy.sparse import vstack

        matrix = vstack((matrix, np.asarray(model.cost, dtype=float)[None, :])).tocsr()
        row_lower = np.append(row_lower, -inf)
        row_upper = np.append(row_upper, primary_ceiling)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Unrecognized options detected.*",
                                category=RuntimeWarning)
        result = milp(
            c=objective, integrality=np.asarray(model.integer),
            bounds=Bounds(model.lower, model.upper),
            constraints=LinearConstraint(matrix, row_lower, row_upper),
            options={
                "mip_rel_gap": 0.0, "time_limit": time_limit, "presolve": True,
                "mip_abs_gap": REQUESTED_MIP_ABS_GAP,
                "mip_feasibility_tolerance": REQUESTED_MIP_FEAS_TOL,
                "threads": 1, "random_seed": 0,
            },
        )
    certificate = _certificate(result, phase, time_limit, model, matrix)
    if not certificate.optimal or result.x is None:
        raise PlannerFailure(certificate)
    vector = np.asarray(result.x)
    residual = matrix @ vector
    lower_violation = max(0.0, float(np.max(np.asarray(model.lower) - vector)))
    upper_violation = max(0.0, float(np.max(vector - np.asarray(model.upper))))
    row_lower_violation = max(0.0, float(np.max(row_lower - residual)))
    row_upper_violation = max(0.0, float(np.max(residual - row_upper)))
    integer_values = vector[np.asarray(model.integer) == 1]
    integer_violation = max(0.0, float(np.max(np.abs(integer_values - np.round(integer_values))))) \
        if integer_values.size else 0.0
    if max(lower_violation, upper_violation, row_lower_violation,
           row_upper_violation, integer_violation) > POSTSOLVE_TOL:
        raise PlannerFailure(SolverCertificate(
            **{**certificate.__dict__, "optimal": False,
               "message": ("postsolve feasibility or integrality check failed: "
                           f"variable=({lower_violation:.3g},{upper_violation:.3g}), "
                           f"row=({row_lower_violation:.3g},{row_upper_violation:.3g}), "
                           f"integer={integer_violation:.3g}")}
        ))
    if (certificate.dual is None or certificate.primal is None
            or abs(certificate.primal - certificate.dual) > PRIMAL_DUAL_ABS_TOL):
        raise PlannerFailure(SolverCertificate(
            **{**certificate.__dict__, "optimal": False,
               "message": "primal-dual certificate not closed"}
        ))
    return vector, certificate


def _qualification_choice(
    primary: SolverCertificate, opposite: SolverCertificate, primary_qualifies: bool,
) -> tuple[bool, str]:
    """Classify one certified opposite branch; near ties remain unresolved."""
    if any(value is None for value in (primary.primal, primary.dual,
                                        opposite.primal, opposite.dual)):
        raise PlannerFailure(replace(opposite, optimal=False,
                                     message="qualification branch lacks bounds"))
    if opposite.primal < primary.dual - QUALIFICATION_STRICT_TOL:
        raise PlannerFailure(replace(opposite, optimal=False,
                                     message="opposite branch contradicts unrestricted primary bound"))
    if opposite.dual > primary.primal + QUALIFICATION_STRICT_TOL:
        return primary_qualifies, "strict_qualify" if primary_qualifies else "strict_skip"
    if (abs(opposite.primal - primary.primal) <= QUALIFICATION_TIE_TOL
            and abs(opposite.dual - primary.dual) <= QUALIFICATION_TIE_TOL):
        return False, "primary_tie"
    raise PlannerFailure(replace(
        opposite, optimal=False,
        message="qualification branch has a numerically unresolved near tie",
    ))


def _check_secondary_gap(certificate: SolverCertificate) -> None:
    """Require both absolute and scale-aware secondary bound closure."""
    if (certificate.primal is None or certificate.dual is None
            or abs(certificate.primal - certificate.dual) > SECONDARY_ABS_GAP_MAX):
        raise PlannerFailure(replace(
            certificate, optimal=False,
            message=f"secondary bound gap exceeds {SECONDARY_ABS_GAP_MAX:g}",
        ))
    if (certificate.relative_gap is None
            or certificate.relative_gap > SECONDARY_REL_GAP_MAX):
        raise PlannerFailure(replace(
            certificate, optimal=False,
            message=f"secondary relative bound gap exceeds {SECONDARY_REL_GAP_MAX:g}",
        ))


def _rounding_allowances(
    tie_cost: np.ndarray, rounded_solution: np.ndarray,
    band: float,
) -> tuple[float, float, float]:
    """Bound score motion from eight-decimal rounding and float arithmetic.

    Every current-action coefficient is nonnegative. All nonzero objective
    coefficients are current-action weights, including fixed-zero controls.
    The first bound is 0.5e-8 times their sum, as specified in the protocol.
    The second allows two conservative machine-epsilon units per product,
    addition and band computation on a scale at least one.
    """
    weights = tie_cost[tie_cost > 0]
    weight_sum = float(np.sum(weights))
    analytic = 0.5e-8 * weight_sum
    n = len(weights)
    absolute_terms = float(np.sum(np.abs(tie_cost * rounded_solution)))
    floating = (2 * (n + 2) * np.finfo(float).eps
                * max(1.0, abs(band), absolute_terms))
    return weight_sum, analytic, float(floating)


def _check_rounded_score(
    certificate: SolverCertificate, unrounded_score: float,
    rounded_score: float, analytic: float, floating: float,
) -> float:
    """Keep the solver band exact; permit only bounded post-round movement."""
    if certificate.primal is None:
        raise PlannerFailure(replace(certificate, optimal=False,
                                     message="secondary score lacks certified optimum"))
    band = float(certificate.primal) + SECONDARY_ABS_GAP_MAX
    if unrounded_score > band:
        raise PlannerFailure(replace(
            certificate, optimal=False,
            message="unrounded current-action score exceeds certified optimum band",
        ))
    if rounded_score > band + analytic + floating:
        raise PlannerFailure(replace(
            certificate, optimal=False,
            message="rounded current-action score exceeds analytic rounding bound",
        ))
    return band


def _secondary_objective(model: _Model, now: int) -> np.ndarray:
    """Moderate-scale preference over the executed action only.

    Qualification has already been pinned. Future controls have zero weight
    because they are re-optimized before execution. Within each action kind,
    model order fixes a coefficient rank independent of solver presolve.
    """
    cost = np.zeros(len(model.names))
    for kind, base in (("order", 0.4), ("spot", 0.3),
                       ("produce", 0.2), ("ship", 0.1)):
        indices = [index for index, name in enumerate(model.names)
                   if name[0] == kind and name[1] == 0 and name[2] == now]
        for rank, index in enumerate(indices, start=1):
            cost[index] = base + rank / (10 * (len(indices) + 1))
    return cost


def _current_action_indices(model: _Model, now: int) -> tuple[int, ...]:
    """Declared lexicographic rule for equal-score executed actions."""
    return tuple(index for kind in ("order", "spot", "produce", "ship")
                 for index, name in enumerate(model.names)
                 if name[0] == kind and name[1] == 0 and name[2] == now
                 and model.upper[index] > 0)


def _reconcile_current_shipments(problem: PlanningProblem, action: Action) -> tuple[Action, float]:
    """Clip only solver-scale shipment overdraw before the strict physical core.

    HiGHS/postsolve accepts row residuals up to ``POSTSOLVE_TOL`` whereas
    ``core.step`` rejects a stock overdraw above ``TOL``. This projection
    uses the *observed* current supply and opening state, in the same order
    as the physical transition. A larger mismatch is an error, not a policy
    adjustment. Qualification and order decisions are never changed here.
    """
    net, contract, state = problem.network, problem.contract, problem.state
    now = state.period
    inventory = {(x.site, x.product): x.quantity for x in state.inventory}
    for item in state.pipeline:
        if item.due == now:
            _core_add(inventory, (item.site, item.product), item.quantity)
    primary_key = (net.primary_site, net.primary_product)
    _core_add(inventory, primary_key,
              min(state.outstanding_for(now), problem.observed_capacity))
    if contract.backup_active_at(state.qualified_at, now):
        _core_add(inventory, primary_key, contract.backup_minimum)
    _core_add(inventory, primary_key, action.spot_order)

    recipes = {(x.site, x.output): x for x in net.recipes}
    needs: dict[tuple[str, str], float] = {}
    produced: dict[tuple[str, str], float] = {}
    for item in action.production:
        recipe = recipes[(item.site, item.product)]
        for product, coefficient in recipe.inputs:
            key = (item.site, product)
            needs[key] = needs.get(key, 0.0) + coefficient * item.quantity
        key = (item.site, item.product)
        produced[key] = produced.get(key, 0.0) + item.quantity
    for key, quantity in needs.items():
        if quantity > inventory.get(key, 0.0) + TOL:
            raise ValueError(f"v2 planner component stock mismatch at {key}")
    for key, quantity in needs.items():
        _core_add(inventory, key, -quantity)
    for key, quantity in produced.items():
        _core_add(inventory, key, quantity)

    corrected: list[Shipment] = []
    correction = 0.0
    for item in sorted(action.shipments, key=lambda x: x.lane_index):
        if item.quantity < 0:
            raise ValueError("v2 planner produced a negative shipment")
        lane = net.lanes[item.lane_index]
        key = (lane.origin, lane.product)
        available = max(0.0, inventory.get(key, 0.0))
        quantity = min(item.quantity, lane.capacity, available)
        correction += item.quantity - quantity
        if correction > POSTSOLVE_TOL:
            raise ValueError("v2 planner shipment projection exceeds postsolve tolerance")
        _core_add(inventory, key, -quantity)
        if quantity > 0:
            corrected.append(Shipment(item.lane_index, quantity))
    return replace(action, shipments=tuple(corrected)), correction


def plan(problem: PlanningProblem) -> PlanResult:
    """Optimize and return the common first action and solver certificates."""
    net, contract, notice, state = problem.network, problem.contract, problem.notice, problem.state
    now, horizon = state.period, net.horizon
    scenarios = problem.scenarios
    keys = sorted({(x.site, x.product) for x in net.initial_stock}
                  | {(net.primary_site, net.primary_product)}
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
                make("backlog", s, t, key, upper=big,
                     cost=probability * contract.backlog_cost)
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
                    _add(terms, v("spot", s, t), -1)
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
                    _add(input_terms, v("spot", s, t), -1)
                    right += backup_const
                    for index, quantity in backup_variables.items():
                        _add(input_terms, index, -quantity)
                model.row(input_terms, upper=right)

            for key in demand_keys:
                backlog = v("backlog", s, t, key)
                served = v("served", s, t, key)
                right = demand[t].get(key, 0.0)
                terms = {backlog: 1, served: 1}
                if t == now:
                    right += initial_backlog.get(key, 0.0)
                else:
                    _add(terms, v("backlog", s, t - 1, key), -1)
                model.row(terms, lower=right, upper=right)
                # Core.step serves as much as possible. Stock and backlog
                # cannot both remain at the end of a period for this key.
                branch = v("service_branch", s, t, key)
                model.row({v("stock", s, t, key): 1, branch: -big}, upper=0)
                model.row({backlog: 1, branch: big}, upper=big)

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
                    for kind in ("backlog", "served"):
                        model.row({v(kind, s, t, key): 1, v(kind, 0, t, key): -1},
                                  lower=0, upper=0)

    primary_cost = np.asarray(model.cost, dtype=float)
    primary_solution, primary_certificate = _solve(
        model, primary_cost, problem.time_limit, "primary"
    )
    qualification_branch_certificate: SolverCertificate | None = None
    qualification_classification = "unavailable"
    current_qualify = v("qualify", 0, now)
    if model.upper[current_qualify] >= 1:
        primary_qualifies = bool(round(primary_solution[current_qualify]))
        opposite = 0 if primary_qualifies else 1
        added = len(scenarios)
        for s in range(added):
            model.row({v("qualify", s, now): 1}, lower=opposite, upper=opposite)
        try:
            _, qualification_branch_certificate = _solve(
                model, primary_cost, problem.time_limit, "qualification_branch"
            )
        finally:
            del model.rows[-added:]
            del model.row_lower[-added:]
            del model.row_upper[-added:]
        choose_qualify, qualification_classification = _qualification_choice(
            primary_certificate, qualification_branch_certificate, primary_qualifies
        )
        for s in range(len(scenarios)):
            model.row({v("qualify", s, now): 1},
                      lower=int(choose_qualify), upper=int(choose_qualify))
    # Reconditioned secondary objective chooses only the executed response.
    # Qualification is already certified and pinned.
    tie_cost = _secondary_objective(model, now)
    tie_slack = QUALIFICATION_TIE_TOL if qualification_classification == "primary_tie" else 0.0
    tie_solution, tie_certificate = _solve(
        model, tie_cost, problem.time_limit, "tie_break",
        primary_ceiling=float(primary_certificate.primal) + tie_slack,
    )
    _check_secondary_gap(tie_certificate)
    # A remaining tie between different current actions is resolved by the
    # smallest vector in the declared order. Each coordinate is optimized
    # and then fixed; later future-only variation cannot change that vector.
    model.row({index: float(weight) for index, weight in enumerate(tie_cost)
               if weight != 0}, upper=float(tie_certificate.primal) + SECONDARY_ABS_GAP_MAX)
    current_tie_certificates: list[SolverCertificate] = []
    current_targets: dict[int, float] = {}
    for index in _current_action_indices(model, now):
        coordinate_cost = np.zeros(len(model.names))
        coordinate_cost[index] = 1.0
        tie_solution, coordinate_certificate = _solve(
            model, coordinate_cost, problem.time_limit, "current_tie",
            primary_ceiling=float(primary_certificate.primal) + tie_slack,
        )
        _check_secondary_gap(coordinate_certificate)
        current_tie_certificates.append(coordinate_certificate)
        current_targets[index] = float(tie_solution[index])
        model.row({index: 1}, lower=current_targets[index], upper=current_targets[index])
    if any(abs(tie_solution[index] - value) > SECONDARY_ABS_GAP_MAX
           for index, value in current_targets.items()):
        raise PlannerFailure(replace(
            current_tie_certificates[-1], optimal=False,
            message="current-action lexicographic pins were not preserved",
        ))
    unrounded_tie_score = float(tie_cost @ tie_solution)
    for index, value in current_targets.items():
        tie_solution[index] = round(value, 8)
    # Canonicalize solver-scale differences in the executed action, but do
    # not accept a rounded point that breaches the existing numerical limits.
    rounded_rows = model.matrix() @ tie_solution
    rounded_violation = max(
        float(np.max(np.asarray(model.row_lower) - rounded_rows)),
        float(np.max(rounded_rows - np.asarray(model.row_upper))),
        float(np.max(np.asarray(model.lower) - tie_solution)),
        float(np.max(tie_solution - np.asarray(model.upper))),
        0.0,
    )
    if rounded_violation > POSTSOLVE_TOL:
        raise PlannerFailure(replace(
            current_tie_certificates[-1], optimal=False,
            message="canonical current action violates model constraints",
        ))
    rounded_tie_score = float(tie_cost @ tie_solution)
    band = float(tie_certificate.primal) + SECONDARY_ABS_GAP_MAX
    rounding_weight_sum, rounding_analytic_bound, rounding_fp_allowance = _rounding_allowances(
        tie_cost, tie_solution, band)
    certified_tie_band_upper = _check_rounded_score(
        tie_certificate, unrounded_tie_score, rounded_tie_score,
        rounding_analytic_bound, rounding_fp_allowance)
    expected = float(primary_cost @ tie_solution + constant_cost)
    if expected > float(primary_certificate.primal) + constant_cost + tie_slack + POSTSOLVE_TOL:
        raise ValueError("secondary solution violates the primary cost ceiling")

    def action_at(s: int, t: int) -> Action:
        def clean(value: float) -> float:
            return 0.0 if abs(value) < POSTSOLVE_TOL else float(value)

        return Action(
            qualify=bool(round(tie_solution[v("qualify", s, t)])),
            primary_order=clean(tie_solution[v("order", s, t)]),
            spot_order=clean(tie_solution[v("spot", s, t)]),
            production=tuple(
                Production(recipe.site, recipe.output, quantity)
                for index, recipe in enumerate(net.recipes)
                if (quantity := clean(tie_solution[v("produce", s, t, index)])) > 0
            ),
            shipments=tuple(
                Shipment(index, quantity) for index in range(len(net.lanes))
                if (quantity := clean(tie_solution[v("ship", s, t, index)])) > 0
            ),
        )

    plans = tuple((scenario.name, tuple(action_at(s, t) for t in range(now, horizon)))
                  for s, scenario in enumerate(scenarios))
    executed, correction = _reconcile_current_shipments(problem, plans[0][1][0])
    if correction:
        plans = tuple((name, (executed, *actions[1:])) for name, actions in plans)
    full_primary_certificate = replace(
        primary_certificate,
        primal=float(primary_certificate.primal) + constant_cost,
        dual=float(primary_certificate.dual) + constant_cost,
    )
    return PlanResult(plans[0][1][0], expected,
                      expected - float(full_primary_certificate.primal),
                      full_primary_certificate,
                      tie_certificate, tuple(current_tie_certificates),
                      qualification_branch_certificate,
                      qualification_classification, plans, correction,
                      rounding_weight_sum, rounding_analytic_bound,
                      rounding_fp_allowance, unrounded_tie_score,
                      rounded_tie_score, certified_tie_band_upper)


Benchmark = Literal["literal", "prior_only", "full_information", "clairvoyant"]


@dataclass(frozen=True)
class Rollout:
    benchmark: Benchmark
    ledgers: tuple[PeriodLedger, ...]
    actions: tuple[Action, ...]
    certificates: tuple[tuple[SolverCertificate, SolverCertificate], ...]
    qualification_classifications: tuple[str, ...]
    qualification_branch_certificates: tuple[SolverCertificate | None, ...]
    operating_cost: float


def run_benchmark(
    *, network: CoreNetwork, contract: ContractV2, notice: Notice,
    short_capacity: tuple[float, ...], long_capacity: tuple[float, ...],
    actual_type: Literal["short", "long"],
    realized_demand: tuple[tuple[Demand, ...], ...],
    mean_demand: tuple[tuple[Demand, ...], ...],
    benchmark: Benchmark, prior_severe: float, literal_severe: float,
    time_limit: float = 30.0,
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
    state = initial_state(network)
    ledgers: list[PeriodLedger] = []
    actions: list[Action] = []
    certificates: list[tuple[SolverCertificate, SolverCertificate]] = []
    qualification_classifications: list[str] = []
    qualification_branch_certificates: list[SolverCertificate | None] = []
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
                                  time_limit)
        choice = plan(problem)
        state, ledger = step_v2(
            network, contract, notice, state, choice.action,
            Realization(actual[t], realized_demand[t]),
            revealed_schedule=actual if t == contract.revelation_period else None,
        )
        ledgers.append(ledger)
        actions.append(choice.action)
        certificates.append((choice.primary_certificate, choice.tie_certificate))
        qualification_classifications.append(choice.qualification_classification)
        qualification_branch_certificates.append(choice.qualification_branch_certificate)
    return Rollout(benchmark, tuple(ledgers), tuple(actions), tuple(certificates),
                   tuple(qualification_classifications),
                   tuple(qualification_branch_certificates),
                   sum(row.operating_cost for row in ledgers))
