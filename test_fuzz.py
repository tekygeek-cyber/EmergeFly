from hypothesis import given, settings, strategies as st

from backend_flight_api import (
    compute_cost_score,
    compute_delay_risk,
    compute_duration_score,
    compute_reliability,
    compute_transfer_score,
)

finite_float = st.floats(0, 1_000_000, allow_nan=False, allow_infinity=False)


@settings(max_examples=100)
@given(price=finite_float, mn=finite_float, mx=finite_float)
def test_cost_score_always_clamped(price, mn, mx):
    assert 0.0 <= compute_cost_score(price, mn, mx) <= 100.0


@settings(max_examples=100)
@given(duration=st.integers(0, 100_000), mn=st.integers(0, 100_000), mx=st.integers(0, 100_000))
def test_duration_score_always_clamped(duration, mn, mx):
    assert 0.0 <= compute_duration_score(duration, mn, mx) <= 100.0


@settings(max_examples=100)
@given(p=st.floats(0, 2, allow_nan=False, allow_infinity=False), c=st.floats(0, 2, allow_nan=False, allow_infinity=False))
def test_reliability_always_clamped(p, c):
    assert 0.0 <= compute_reliability(p, c) <= 100.0


@settings(max_examples=100)
@given(p=st.floats(0, 2, allow_nan=False, allow_infinity=False), delay=st.floats(0, 500, allow_nan=False, allow_infinity=False))
def test_delay_risk_always_clamped(p, delay):
    assert 0.0 <= compute_delay_risk(p, delay) <= 100.0


@settings(max_examples=100)
@given(
    lay=st.floats(0, 600, allow_nan=False, allow_infinity=False),
    mct=st.floats(0, 300, allow_nan=False, allow_infinity=False),
    buffer=st.floats(1, 200, allow_nan=False, allow_infinity=False),
    transfers=st.integers(0, 3),
)
def test_transfer_score_always_clamped(lay, mct, buffer, transfers):
    assert 0.0 <= compute_transfer_score(lay, mct, buffer, transfers) <= 100.0
