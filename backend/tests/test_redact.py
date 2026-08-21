from oncall.security.redact import redact_text


def test_redact():
    assert 'secret123' not in redact_text('password=secret123')
