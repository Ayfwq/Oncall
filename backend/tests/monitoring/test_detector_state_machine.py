"""Detector state machine: full transition coverage.

Unit tests cover the pure Detector transitions
(NORMAL -> PENDING -> FIRING -> RECOVERING -> NORMAL, plus the recovery
interruption and one-sample configurations).  PostgreSQL-backed tests exercise
the real MonitoringEngine.evaluate_rules path and prove that rule state is
durably persisted in monitoring_rule_states across service restarts (fresh
sessions) and that manual resolution resets the state so an unchanged abnormal
metric can fire again.
"""
from __future__ import annotations

import pytest

from helpers import (
    make_project_with_rule as _project_with_synth_rule,
)
from helpers import (
    project_incidents as _open_incidents,
)
from helpers import (
    rule_id as _rule_id,
)
from helpers import (
    synth_snapshot as _snap,
)
from oncall.application.incident_service import IncidentService
from oncall.domain.enums import RuleState
from oncall.infrastructure.db.models import MonitoringRule, MonitoringRuleState
from oncall.infrastructure.db.session import SessionFactory
from oncall.monitoring.detector import (
    Detector,
    RuleConfig,
    RuleRuntimeState,
    compare,
    recovery_compare,
)
from oncall.monitoring.engine import MonitoringEngine
from sqlalchemy import func, select

# always abnormal: value > -1; never recovered: value < -2
ALWAYS_ABNORMAL = RuleConfig(">", -1.0, trigger_for=2, recovery_threshold=-2.0, recovery_for=2)


async def _state(db, rule_id):
    return await db.get(MonitoringRuleState, rule_id)


# --------------------------------------------------------------------------
# pure Detector unit tests
# --------------------------------------------------------------------------


def test_full_cycle_normal_pending_firing_recovering_normal():
    d = Detector()
    s = RuleRuntimeState()
    cfg = ALWAYS_ABNORMAL
    t = d.evaluate(cfg, s, 5.0)
    assert (t.before, t.after) == (RuleState.NORMAL, RuleState.PENDING) and not t.became_firing
    t = d.evaluate(cfg, s, 6.0)
    assert (t.before, t.after) == (RuleState.PENDING, RuleState.FIRING) and t.became_firing
    t = d.evaluate(cfg, s, -5.0)  # recovered
    assert (t.before, t.after) == (RuleState.FIRING, RuleState.RECOVERING) and not t.became_recovered
    t = d.evaluate(cfg, s, -6.0)
    assert (t.before, t.after) == (RuleState.RECOVERING, RuleState.NORMAL) and t.became_recovered


def test_pending_resets_to_normal_on_healthy_value():
    d = Detector()
    s = RuleRuntimeState()
    assert d.evaluate(ALWAYS_ABNORMAL, s, 5.0).after == RuleState.PENDING
    t = d.evaluate(ALWAYS_ABNORMAL, s, -5.0)  # healthy
    assert t.after == RuleState.NORMAL
    assert s.abnormal_hits == 0


def test_recovering_interrupted_by_abnormal_returns_to_firing():
    d = Detector()
    s = RuleRuntimeState()
    d.evaluate(ALWAYS_ABNORMAL, s, 5.0)
    d.evaluate(ALWAYS_ABNORMAL, s, 6.0)  # FIRING
    assert d.evaluate(ALWAYS_ABNORMAL, s, -5.0).after == RuleState.RECOVERING
    t = d.evaluate(ALWAYS_ABNORMAL, s, 5.0)  # abnormal again mid-recovery
    assert (t.before, t.after) == (RuleState.RECOVERING, RuleState.FIRING)
    # entering FIRING from a non-firing state re-arms the firing event; the
    # engine dedupes against the still-open Incident so nothing duplicates
    assert t.became_firing and not t.became_recovered


def test_trigger_for_one_fires_immediately():
    d = Detector()
    s = RuleRuntimeState()
    cfg = RuleConfig(">", -1.0, trigger_for=1, recovery_threshold=-2.0, recovery_for=1)
    t = d.evaluate(cfg, s, 5.0)
    assert (t.before, t.after) == (RuleState.NORMAL, RuleState.FIRING) and t.became_firing


def test_recovery_for_one_recovers_immediately():
    d = Detector()
    s = RuleRuntimeState()
    cfg = RuleConfig(">", -1.0, trigger_for=1, recovery_threshold=-2.0, recovery_for=1)
    d.evaluate(cfg, s, 5.0)  # FIRING
    t = d.evaluate(cfg, s, -5.0)
    assert (t.before, t.after) == (RuleState.FIRING, RuleState.NORMAL) and t.became_recovered


def test_hysteresis_band_keeps_firing_between_thresholds():
    # value between trigger (85) and recovery (70): stays FIRING
    d = Detector()
    s = RuleRuntimeState()
    cfg = RuleConfig(">", 85.0, 2, 70.0, 2)
    d.evaluate(cfg, s, 90.0)
    d.evaluate(cfg, s, 91.0)  # FIRING
    assert d.evaluate(cfg, s, 80.0).after == RuleState.FIRING


def test_transition_flags_are_mutually_exclusive():
    d = Detector()
    s = RuleRuntimeState()
    d.evaluate(ALWAYS_ABNORMAL, s, 5.0)
    t = d.evaluate(ALWAYS_ABNORMAL, s, 6.0)
    assert t.changed and t.became_firing and not t.became_recovered
    assert s.state == RuleState.FIRING and s.abnormal_hits == 2


def test_compare_and_recovery_compare_operators():
    assert compare(10, ">", 5) and compare(5, ">=", 5) and compare(3, "<", 5)
    assert compare(5, "<=", 5) and compare(5, "==", 5) and compare(5, "!=", 6)
    assert not compare(10, "<", 5) and not compare(1, ">", 5)
    assert not compare(1, "bogus", 5)  # unknown operator never fires
    assert recovery_compare(4, ">", 5) and not recovery_compare(6, ">", 5)
    assert recovery_compare(6, "<", 5) and not recovery_compare(4, "<", 5)


# --------------------------------------------------------------------------
# PostgreSQL persistence tests (MonitoringRuleState read/write)
# --------------------------------------------------------------------------


@pytest.mark.integration
async def test_rule_state_persists_across_evaluations_and_restart(db, test_user):
    project = await _project_with_synth_rule(db, test_user)
    rule_id = await _rule_id(db, project.id)
    engine = MonitoringEngine(db)

    await engine.evaluate_rules(project.id, _snap(project.id, 5.0))
    st = await _state(db, rule_id)
    assert st is not None and st.state == RuleState.PENDING.value and st.abnormal_hits == 1

    await engine.evaluate_rules(project.id, _snap(project.id, 6.0))
    st = await _state(db, rule_id)
    assert st.state == RuleState.FIRING.value and st.abnormal_hits == 2
    assert len(await _open_incidents(db, project.id, rule_id)) == 1

    # ---- service restart: a brand-new session must see the persisted state ----
    async with SessionFactory() as fresh:
        st2 = await fresh.get(MonitoringRuleState, rule_id)
        assert st2 is not None
        assert st2.state == RuleState.FIRING.value and st2.abnormal_hits == 2
        assert st2.last_value == 6.0
        # feeding an abnormal value keeps it FIRING without a duplicate incident
        await MonitoringEngine(fresh).evaluate_rules(project.id, _snap(project.id, 7.0))
    incidents = await _open_incidents(db, project.id, rule_id)
    assert len(incidents) == 1
    st3 = await _state(db, rule_id)
    assert st3.state == RuleState.FIRING.value and st3.abnormal_hits == 2


@pytest.mark.integration
async def test_full_pg_cycle_recovers_and_resolves_incident(db, test_user):
    project = await _project_with_synth_rule(db, test_user)
    rule_id = await _rule_id(db, project.id)
    engine = MonitoringEngine(db)

    await engine.evaluate_rules(project.id, _snap(project.id, 5.0))
    await engine.evaluate_rules(project.id, _snap(project.id, 6.0))  # FIRING -> incident
    incs = await _open_incidents(db, project.id, rule_id)
    assert len(incs) == 1 and incs[0].status == "open"

    await engine.evaluate_rules(project.id, _snap(project.id, -5.0))  # RECOVERING
    st = await _state(db, rule_id)
    assert st.state == RuleState.RECOVERING.value and st.recovery_hits == 1
    await engine.evaluate_rules(project.id, _snap(project.id, -6.0))  # NORMAL -> resolve
    st = await _state(db, rule_id)
    assert st.state == RuleState.NORMAL.value
    incs = await _open_incidents(db, project.id, rule_id)
    assert len(incs) == 1 and incs[0].status == "resolved"


@pytest.mark.integration
async def test_recovering_interrupted_keeps_incident_open(db, test_user):
    project = await _project_with_synth_rule(db, test_user)
    rule_id = await _rule_id(db, project.id)
    engine = MonitoringEngine(db)

    await engine.evaluate_rules(project.id, _snap(project.id, 5.0))
    await engine.evaluate_rules(project.id, _snap(project.id, 6.0))  # FIRING
    await engine.evaluate_rules(project.id, _snap(project.id, -5.0))  # RECOVERING
    await engine.evaluate_rules(project.id, _snap(project.id, 7.0))   # abnormal again
    st = await _state(db, rule_id)
    assert st.state == RuleState.FIRING.value and st.recovery_hits == 0
    incs = await _open_incidents(db, project.id, rule_id)
    assert len(incs) == 1 and incs[0].status == "open"  # not resolved, not duplicated


@pytest.mark.integration
async def test_manual_resolve_resets_state_and_allows_retrigger(db, test_user):
    project = await _project_with_synth_rule(db, test_user)
    rule_id = await _rule_id(db, project.id)
    engine = MonitoringEngine(db)

    await engine.evaluate_rules(project.id, _snap(project.id, 5.0))
    await engine.evaluate_rules(project.id, _snap(project.id, 6.0))  # incident A
    incs = await _open_incidents(db, project.id, rule_id)
    assert len(incs) == 1
    incident_a = incs[0]

    await IncidentService(db).resolve(incident_a.id, "manual_resolve")
    st = await _state(db, rule_id)
    assert st.state == RuleState.NORMAL.value and st.abnormal_hits == 0  # state reset

    # the metric is still abnormal -> it must be able to fire again
    await engine.evaluate_rules(project.id, _snap(project.id, 8.0))
    await engine.evaluate_rules(project.id, _snap(project.id, 9.0))  # incident B
    incs = await _open_incidents(db, project.id, rule_id)
    assert len(incs) == 2, "expected a second incident after manual resolve + retrigger"
    incident_b = incs[-1]
    assert incident_a.id != incident_b.id
    assert incident_a.status == "resolved" and incident_b.status == "open"


@pytest.mark.integration
async def test_rule_state_count_matches_rules(db, test_user):
    project = await _project_with_synth_rule(db, test_user)
    engine = MonitoringEngine(db)
    await engine.evaluate_rules(project.id, _snap(project.id, 5.0))
    # count only states belonging to this project's rules (other projects may exist)
    total = await db.scalar(
        select(func.count())
        .select_from(MonitoringRuleState)
        .join(MonitoringRule, MonitoringRule.id == MonitoringRuleState.rule_id)
        .where(MonitoringRule.project_id == project.id)
    )
    assert total == 1
