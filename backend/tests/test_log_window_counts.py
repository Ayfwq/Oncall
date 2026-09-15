from oncall.monitoring.engine import new_log_counts


def test_overlapping_log_windows_count_each_line_once():
    old = [
        {'container': 'api', 'line': '2026-09-16T00:00:00Z ERROR ValueError: old'},
    ]
    current = [
        *old,
        {'container': 'api', 'line': '2026-09-16T00:01:00Z ERROR RuntimeError: new'},
        {'container': 'api', 'line': '2026-09-16T00:01:01Z INFO healthy'},
    ]

    errors, exceptions = new_log_counts(current, old)

    assert errors == 1
    assert exceptions == 1


def test_same_text_from_different_containers_is_distinct():
    line = '2026-09-16T00:01:00Z ERROR RuntimeError: failed'
    errors, exceptions = new_log_counts(
        [{'container': 'worker', 'line': line}],
        [{'container': 'api', 'line': line}],
    )
    assert (errors, exceptions) == (1, 1)
