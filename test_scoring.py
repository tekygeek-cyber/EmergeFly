import pytest

from backend_flight_api import (
    WEIGHT_PROFILES,
    compute_cost_score,
    compute_delay_risk,
    compute_duration_score,
    compute_overall_score,
    compute_reliability,
    compute_transfer_score,
)


class TestReliability:
    def test_perfect(self):
        assert compute_reliability(1.0, 0.0) == 100.0

    def test_zero_ontime(self):
        assert compute_reliability(0.0, 0.0) == 0.0

    def test_cancel_clamps(self):
        assert compute_reliability(1.0, 1.5) == 0.0

    def test_typical(self):
        assert 90 < compute_reliability(0.92, 0.01) < 100

    def test_below_minimum_reliability(self):
        assert compute_reliability(0.45, 0.10) < 50.0


class TestTransferScore:
    def test_direct_always_100(self):
        assert compute_transfer_score(0, 90, 60, 0) == 100.0

    def test_mct_violation(self):
        assert compute_transfer_score(45, 90, 60, 1) == 0.0

    def test_passes_mct(self):
        assert 0 < compute_transfer_score(120, 90, 60, 1) <= 100

    def test_multiple_transfers_penalised(self):
        assert compute_transfer_score(150, 90, 60, 1) > compute_transfer_score(150, 90, 60, 2)

    def test_exact_mct_scores_zero_buffer(self):
        assert compute_transfer_score(90, 90, 60, 1) == 0.0


class TestDelayRisk:
    def test_perfect_ontime(self):
        assert compute_delay_risk(1.0, 0) == 0.0

    def test_always_late_long_delay(self):
        assert compute_delay_risk(0.0, 180) == 100.0

    def test_avg_delay_capped_at_180(self):
        assert compute_delay_risk(0.5, 180) == compute_delay_risk(0.5, 360)


class TestCostScore:
    def test_cheapest_is_100(self):
        assert compute_cost_score(100, 100, 1000) == pytest.approx(100.0, abs=0.01)

    def test_most_expensive_is_0(self):
        assert compute_cost_score(1000, 100, 1000) == pytest.approx(0.0, abs=0.01)

    def test_midpoint(self):
        assert 45 < compute_cost_score(550, 100, 1000) < 55

    def test_single_price_epsilon_guard(self):
        assert compute_cost_score(500, 500, 500) == pytest.approx(100.0, abs=0.01)


class TestDurationScore:
    def test_fastest_is_100(self):
        assert compute_duration_score(480, 480, 1020) == pytest.approx(100.0, abs=0.01)

    def test_slowest_is_0(self):
        assert compute_duration_score(1020, 480, 1020) == pytest.approx(0.0, abs=0.01)

    def test_symmetric_with_cost(self):
        assert abs(compute_cost_score(620, 420, 1100) - compute_duration_score(620, 420, 1100)) < 0.01


class TestOverallScore:
    def test_weights_sum_to_1(self):
        for weights in WEIGHT_PROFILES.values():
            assert abs(sum(weights.values()) - 1.0) < 1e-9

    def test_all_100(self):
        scores = {"reliability": 100, "transferScore": 100, "delayRisk": 0, "costScore": 100, "durationScore": 100}
        assert compute_overall_score(scores, WEIGHT_PROFILES["balanced"]) == 100.0

    def test_all_0(self):
        scores = {"reliability": 0, "transferScore": 0, "delayRisk": 100, "costScore": 0, "durationScore": 0}
        assert compute_overall_score(scores, WEIGHT_PROFILES["balanced"]) == 0.0

    def test_clamped_above_100(self):
        scores = {"reliability": 110, "transferScore": 110, "delayRisk": -10, "costScore": 110, "durationScore": 110}
        assert compute_overall_score(scores, WEIGHT_PROFILES["balanced"]) == 100.0
