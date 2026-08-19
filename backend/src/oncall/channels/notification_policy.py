from __future__ import annotations


def retry_delay_seconds(attempts:int, *, base:int=5, cap:int=900)->int:
    """Bounded exponential backoff; attempts is the post-failure attempt count."""
    return min(cap, base * (2 ** max(0, attempts - 1)))


def is_cooldown_kind(kind:str|None)->bool:
    # Recovery should always be delivered. Repeated diagnosis reports can be suppressed.
    return kind in {'diagnosis','stale_diagnosis'}
