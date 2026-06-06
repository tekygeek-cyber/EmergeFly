from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from backend_flight_api import app
from backend_flight_api import load_opensky_credentials


@pytest.fixture
def future_window() -> dict[str, str]:
    start = datetime.now(timezone.utc) + timedelta(days=1)
    end = start + timedelta(hours=12)
    return {"startISO": start.isoformat(), "endISO": end.isoformat()}


@pytest.fixture(autouse=True)
def force_mock_states(monkeypatch):
    monkeypatch.setenv("OPENSKY_USE_MOCK_STATES", "1")


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


@pytest.mark.asyncio
async def test_health_returns_ok(client):
    response = await client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "routes_cached" in data
    assert "opensky_live_ready" in data
    assert "profiles_available" in data


@pytest.mark.asyncio
async def test_search_valid_body_returns_results(client, future_window):
    response = await client.post(
        "/search",
        json={
            "originIATA": "MLA",
            "destinationIATA": "DEL",
            "emergencyProfile": "balanced",
            "sortBy": "cost",
            "maxStops": 2,
            "maxResults": 5,
            "departWindow": future_window,
        },
    )

    data = response.json()
    assert response.status_code == 200
    assert isinstance(data["results"], list)
    assert data["results"]
    assert data["results"][0]["priceUSD"] <= data["results"][-1]["priceUSD"]


@pytest.mark.asyncio
async def test_search_invalid_origin_iata_returns_422(client, future_window):
    response = await client.post(
        "/search",
        json={"originIATA": "M1A", "destinationIATA": "DEL", "departWindow": future_window},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_search_past_window_returns_422(client):
    start = datetime.now(timezone.utc) - timedelta(days=1)
    end = start + timedelta(hours=12)
    response = await client.post(
        "/search",
        json={
            "originIATA": "MLA",
            "destinationIATA": "DEL",
            "departWindow": {"startISO": start.isoformat(), "endISO": end.isoformat()},
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_route_nonexistent_returns_404(client):
    response = await client.get("/route/nonexistent")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_low_max_price_returns_empty_results_and_warning(client, future_window):
    response = await client.post(
        "/search",
        json={
            "originIATA": "MLA",
            "destinationIATA": "DEL",
            "departWindow": future_window,
            "maxPriceUSD": 100,
        },
    )

    data = response.json()
    assert response.status_code == 200
    assert data["results"] == []
    assert any("No routes survived" in warning for warning in data["warnings"])


@pytest.mark.asyncio
async def test_mct_violating_route_is_warned_not_returned(client, future_window):
    response = await client.post(
        "/search",
        json={
            "originIATA": "MLA",
            "destinationIATA": "DEL",
            "departWindow": future_window,
            "maxStops": 2,
            "maxResults": 10,
        },
    )

    data = response.json()
    route_ids = {route["routeId"] for route in data["results"]}
    assert response.status_code == 200
    assert "R-003" not in route_ids
    assert any("R-003 pruned: minimum connection time violated" in warning for warning in data["warnings"])


@pytest.mark.asyncio
async def test_search_same_origin_destination_returns_422(client, future_window):
    response = await client.post(
        "/search",
        json={"originIATA": "MLA", "destinationIATA": "MLA", "departWindow": future_window},
    )

    assert response.status_code == 422


def test_load_opensky_credentials_file(monkeypatch, tmp_path):
    credentials = tmp_path / "credentials.json"
    credentials.write_text('{"clientId":"test-id","clientSecret":"test-secret"}')
    monkeypatch.delenv("OPENSKY_CLIENT_ID", raising=False)
    monkeypatch.delenv("OPENSKY_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("OPENSKY_CREDENTIALS_FILE", str(credentials))

    assert load_opensky_credentials() == ("test-id", "test-secret")


@pytest.mark.asyncio
async def test_search_sort_speed_ascending(client, future_window):
    response = await client.post(
        "/search",
        json={
            "originIATA": "MLA",
            "destinationIATA": "DEL",
            "sortBy": "speed",
            "departWindow": future_window,
        },
    )

    durations = [route["totalDurationMin"] for route in response.json()["results"]]
    assert response.status_code == 200
    assert durations == sorted(durations)


@pytest.mark.asyncio
async def test_search_sort_balanced_descending_score(client, future_window):
    response = await client.post(
        "/search",
        json={
            "originIATA": "MLA",
            "destinationIATA": "DEL",
            "sortBy": "balanced",
            "departWindow": future_window,
        },
    )

    scores = [route["overallScore"] for route in response.json()["results"]]
    assert response.status_code == 200
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["balanced", "budget", "fastest", "medical", "family", "evac"])
async def test_all_profiles_return_results(client, future_window, profile):
    response = await client.post(
        "/search",
        json={
            "originIATA": "MLA",
            "destinationIATA": "DEL",
            "emergencyProfile": profile,
            "departWindow": future_window,
        },
    )

    assert response.status_code == 200
    assert response.json()["results"]


@pytest.mark.asyncio
async def test_max_price_filter(client, future_window):
    response = await client.post(
        "/search",
        json={
            "originIATA": "MLA",
            "destinationIATA": "DEL",
            "maxPriceUSD": 650,
            "departWindow": future_window,
        },
    )

    assert response.status_code == 200
    assert all(route["priceUSD"] <= 650 for route in response.json()["results"])


@pytest.mark.asyncio
async def test_max_duration_filter(client, future_window):
    response = await client.post(
        "/search",
        json={
            "originIATA": "MLA",
            "destinationIATA": "DEL",
            "maxDurationMin": 600,
            "departWindow": future_window,
        },
    )

    assert response.status_code == 200
    assert all(route["totalDurationMin"] <= 600 for route in response.json()["results"])


@pytest.mark.asyncio
async def test_invalid_sort_by_returns_422(client, future_window):
    response = await client.post(
        "/search",
        json={
            "originIATA": "MLA",
            "destinationIATA": "DEL",
            "sortBy": "price_asc_plz",
            "departWindow": future_window,
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_max_stops_out_of_range_returns_422(client, future_window):
    response = await client.post(
        "/search",
        json={
            "originIATA": "MLA",
            "destinationIATA": "DEL",
            "maxStops": 9,
            "departWindow": future_window,
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_injection_iata_returns_422(client, future_window):
    response = await client.post(
        "/search",
        json={
            "originIATA": "MLA; DROP TABLE routes",
            "destinationIATA": "DEL",
            "departWindow": future_window,
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_route_detail_after_search(client, future_window):
    await client.post(
        "/search",
        json={
            "originIATA": "MLA",
            "destinationIATA": "DEL",
            "departWindow": future_window,
        },
    )
    response = await client.get("/route/R-001")

    assert response.status_code == 200
    assert "scores" in response.json()
    assert response.json()["legs"]


@pytest.mark.asyncio
async def test_cost_rank_1_is_cheapest(client, future_window):
    response = await client.post(
        "/search",
        json={
            "originIATA": "MLA",
            "destinationIATA": "DEL",
            "maxResults": 10,
            "departWindow": future_window,
        },
    )

    results = response.json()["results"]
    rank1 = next(route for route in results if route["costRank"] == 1)
    assert all(rank1["priceUSD"] <= route["priceUSD"] for route in results)


@pytest.mark.asyncio
async def test_speed_rank_1_is_fastest(client, future_window):
    response = await client.post(
        "/search",
        json={
            "originIATA": "MLA",
            "destinationIATA": "DEL",
            "maxResults": 10,
            "departWindow": future_window,
        },
    )

    results = response.json()["results"]
    rank1 = next(route for route in results if route["speedRank"] == 1)
    assert all(rank1["totalDurationMin"] <= route["totalDurationMin"] for route in results)


@pytest.mark.asyncio
async def test_scores_and_confidence_are_bounded(client, future_window):
    response = await client.post(
        "/search",
        json={
            "originIATA": "MLA",
            "destinationIATA": "DEL",
            "departWindow": future_window,
        },
    )

    assert response.status_code == 200
    for route in response.json()["results"]:
        assert 0.0 <= route["confidence"] <= 100.0
        for value in route["scores"].values():
            assert 0.0 <= value <= 100.0


@pytest.mark.asyncio
async def test_forced_mock_states_sets_degraded_mode(client, future_window):
    response = await client.post(
        "/search",
        json={
            "originIATA": "MLA",
            "destinationIATA": "DEL",
            "departWindow": future_window,
        },
    )

    assert response.status_code == 200
    assert response.json()["degradedMode"] is True
