from oncall.application.alert_labels import (
    localize_alert_answer,
    severity_label,
    status_label,
)


def test_alert_display_values_are_chinese_without_changing_identifiers():
    assert severity_label("critical") == "严重"
    assert status_label("investigating") == "调查中"
    answer = "| critical | investigating |\n| OncallContainerMetricsDown | diagnosed |"
    assert localize_alert_answer(answer) == (
        "| 严重 | 调查中 |\n| OncallContainerMetricsDown | 已诊断 |"
    )
