"""The active remote-Python signal contract has one source of truth."""

from oncall.monitoring.signals import PYTHON_GPU_SIGNALS, PYTHON_SIGNALS, SUPPORTED_SIGNALS


def test_base_and_gpu_signal_counts_are_stable():
    assert len(PYTHON_SIGNALS) == 28
    assert len(PYTHON_GPU_SIGNALS) == 6
    assert len(SUPPORTED_SIGNALS) == 34
    assert not set(PYTHON_SIGNALS) & set(PYTHON_GPU_SIGNALS)


def test_signal_families_are_exactly_the_supported_remote_families():
    assert {key.split('.', 1)[0] for key in PYTHON_SIGNALS} == {'host', 'service', 'app', 'process'}
    assert all(key.startswith('host.gpu.') for key in PYTHON_GPU_SIGNALS)
