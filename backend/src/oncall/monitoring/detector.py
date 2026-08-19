from __future__ import annotations

from dataclasses import dataclass
from oncall.domain.enums import RuleState


@dataclass(frozen=True)
class RuleConfig:
    operator:str
    trigger_threshold:float
    trigger_for:int=2
    recovery_threshold:float=0
    recovery_for:int=2


@dataclass
class RuleRuntimeState:
    state:RuleState=RuleState.NORMAL
    abnormal_hits:int=0
    recovery_hits:int=0
    last_value:float|None=None


@dataclass(frozen=True)
class RuleTransition:
    before:RuleState
    after:RuleState
    value:float
    changed:bool
    became_firing:bool=False
    became_recovered:bool=False


def compare(value:float,op:str,threshold:float)->bool:
    return {'>':value>threshold,'>=':value>=threshold,'<':value<threshold,'<=':value<=threshold,'==':value==threshold,'!=':value!=threshold}.get(op,False)


def recovery_compare(value:float,op:str,threshold:float)->bool:
    # Recovery is intentionally the logical healthy side of the configured trigger direction.
    return value < threshold if op in ('>','>=') else value > threshold if op in ('<','<=') else not compare(value,op,threshold)


class Detector:
    def evaluate(self,cfg:RuleConfig,s:RuleRuntimeState,value:float)->RuleTransition:
        before=s.state; abnormal=compare(value,cfg.operator,cfg.trigger_threshold); recovered=recovery_compare(value,cfg.operator,cfg.recovery_threshold)
        if s.state==RuleState.NORMAL:
            if abnormal:
                s.abnormal_hits=1;s.recovery_hits=0;s.state=RuleState.FIRING if cfg.trigger_for<=1 else RuleState.PENDING
        elif s.state==RuleState.PENDING:
            if abnormal:
                s.abnormal_hits+=1
                if s.abnormal_hits>=cfg.trigger_for:s.state=RuleState.FIRING
            else:s.state=RuleState.NORMAL;s.abnormal_hits=0;s.recovery_hits=0
        elif s.state==RuleState.FIRING:
            if recovered:
                s.recovery_hits=1;s.state=RuleState.NORMAL if cfg.recovery_for<=1 else RuleState.RECOVERING
            else:s.recovery_hits=0
        elif s.state==RuleState.RECOVERING:
            if recovered:
                s.recovery_hits+=1
                if s.recovery_hits>=cfg.recovery_for:s.state=RuleState.NORMAL;s.abnormal_hits=0;s.recovery_hits=0
            elif abnormal:s.state=RuleState.FIRING;s.recovery_hits=0
        s.last_value=value
        return RuleTransition(before,s.state,value,before!=s.state,before!=RuleState.FIRING and s.state==RuleState.FIRING,before in (RuleState.FIRING,RuleState.RECOVERING) and s.state==RuleState.NORMAL)
