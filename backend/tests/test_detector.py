from oncall.domain.enums import RuleState
from oncall.monitoring.detector import Detector, RuleConfig, RuleRuntimeState


def test_hysteresis():
    d=Detector();s=RuleRuntimeState();cfg=RuleConfig('>',85,2,70,2)
    assert d.evaluate(cfg,s,90).after==RuleState.PENDING
    assert d.evaluate(cfg,s,91).became_firing
    assert d.evaluate(cfg,s,80).after==RuleState.FIRING
    assert d.evaluate(cfg,s,69).after==RuleState.RECOVERING
    assert d.evaluate(cfg,s,68).became_recovered
