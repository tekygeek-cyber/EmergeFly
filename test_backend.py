from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from backend_flight_api import app


@pytest.fixture
def future_window() -> dict[str, str]:
    start = datetime.now(timezone.utc) + timedelta(days=1)
    end = start + timedelta(hours=12)
    return {"startISO": start.isoformat(), "endISO": end.isoformat()}


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


@pytest.mark.asyncio
async def test_health_returns_ok(client):
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


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
