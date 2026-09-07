from oncall.monitoring.engine import baseline_z_score


def test_high_water_baseline_ignores_improvements():
    assert baseline_z_score(50.0, 100.0, 10.0, '>') == 0.0
    assert baseline_z_score(140.0, 100.0, 10.0, '>') == 4.0


def test_low_water_baseline_ignores_improvements():
    assert baseline_z_score(99.0, 95.0, 1.0, '<') == 0.0
    assert baseline_z_score(90.0, 95.0, 1.0, '<') == 5.0


def test_constant_baseline_only_fires_in_unhealthy_direction():
    assert baseline_z_score(0.0, 100.0, 0.0, '>') == 0.0
    assert baseline_z_score(101.0, 100.0, 0.0, '>') == float('inf')
