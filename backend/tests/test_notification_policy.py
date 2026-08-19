from oncall.channels.notification_policy import is_cooldown_kind, retry_delay_seconds


def test_retry_backoff_is_bounded_exponential():
    assert [retry_delay_seconds(i) for i in range(1,6)]==[5,10,20,40,80]
    assert retry_delay_seconds(20)==900


def test_recovery_is_never_suppressed_by_diagnosis_cooldown():
    assert is_cooldown_kind('diagnosis')
    assert not is_cooldown_kind('resolved')
    assert not is_cooldown_kind('reply')
