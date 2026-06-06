import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

logger = logging.getLogger("emergefly")
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"

OPENSKY_TOKEN_URL = (
    "https://auth.opensky-network.org/auth/realms/opensky-network/"
    "protocol/openid-connect/token"
)
OPENSKY_STATES_URL = "https://opensky-network.org/api/states/all"

_opensky_token: str | None = None
_opensky_token_expires_at = 0.0
_route_cache: dict[str, dict[str, Any]] = {}


class SearchRequest(BaseModel):
    origin: str = Field(default="MLA", min_length=3, max_length=4)
    destination: str = Field(default="FCO", min_length=3, max_length=4)
    departureTime: datetime | None = None
    arrivalTime: datetime | None = None
    priority: str = "medical"


class RouteOption(BaseModel):
    id: str
    airline: str
    flightNumber: str
    origin: str
    destination: str
    departureTime: datetime
    arrivalTime: datetime
    durationMinutes: int
    stops: int
    aircraft: str
    status: str
    score: float
    scoreBreakdown: dict[str, float]
    source: str


class SearchResponse(BaseModel):
    query: SearchRequest
    degradedMode: bool
    scheduleProvider: str
    stateProvider: str
    routes: list[RouteOption]


class ScheduleProvider(Protocol):
    name: str

    async def search(self, request: SearchRequest) -> list[dict[str, Any]]:
        ...


def opensky_credentials_configured() -> bool:
    return bool(os.getenv("OPENSKY_CLIENT_ID") and os.getenv("OPENSKY_CLIENT_SECRET"))


async def get_opensky_token() -> str | None:
    """Fetches and caches an OpenSky OAuth2 token with a 60-second expiry buffer."""
    global _opensky_token, _opensky_token_expires_at
    if not opensky_credentials_configured():
        return None

    now = time.time()
    if _opensky_token and now < _opensky_token_expires_at:
        return _opensky_token

    payload = {
        "grant_type": "client_credentials",
        "client_id": os.environ["OPENSKY_CLIENT_ID"],
        "client_secret": os.environ["OPENSKY_CLIENT_SECRET"],
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(OPENSKY_TOKEN_URL, data=payload)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("OpenSky token request failed; falling back to mock state data: %s", exc)
        return None

    token = data.get("access_token")
    expires_in = int(data.get("expires_in", 0))
    if not token or expires_in <= 0:
        logger.warning("OpenSky token response was incomplete; falling back to mock state data")
        return None

    _opensky_token = token
    _opensky_token_expires_at = now + max(expires_in - 60, 0)
    return _opensky_token


async def fetch_opensky_states() -> tuple[list[dict[str, Any]], bool]:
    """Retrieves live OpenSky state vectors, or mock data when live mode is unavailable."""
    token = await get_opensky_token()
    if not token:
        return mock_state_vectors(), True

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(OPENSKY_STATES_URL, headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("OpenSky state request failed; falling back to mock state data: %s", exc)
        return mock_state_vectors(), True

    states = data.get("states") or []
    mapped = [
        {
            "icao24": row[0],
            "callsign": (row[1] or "").strip(),
            "longitude": row[5],
            "latitude": row[6],
            "altitude": row[7],
            "velocity": row[9],
        }
        for row in states
        if len(row) > 9
    ]
    return mapped, False


def mock_state_vectors() -> list[dict[str, Any]]:
    """Provides predictable aircraft state data when OpenSky cannot be reached."""
    return [
        {
            "icao24": "4d2211",
            "callsign": "EMF101",
            "longitude": 14.45,
            "latitude": 35.9,
            "altitude": 10668,
            "velocity": 230,
        },
        {
            "icao24": "4ca7b3",
            "callsign": "EMF202",
            "longitude": 12.5,
            "latitude": 41.8,
            "altitude": 9144,
            "velocity": 210,
        },
    ]


def normalize_airport(value: str) -> str:
    return value.strip().upper()


def score_route(route: dict[str, Any], priority: str) -> tuple[float, dict[str, float]]:
    """Scores routes for emergency travel using duration, stops, reliability, and urgency."""
    duration_score = max(0, 100 - (route["durationMinutes"] / 6))
    stop_score = max(0, 100 - (route["stops"] * 30))
    reliability_score = route.get("reliability", 82)
    status_score = 100 if route.get("status") in {"scheduled", "boarding", "in-air"} else 65

    priority_weight = 1.12 if priority in {"medical", "critical", "evacuation"} else 1.0
    breakdown = {
        "duration": round(duration_score, 2),
        "stops": round(stop_score, 2),
        "reliability": round(reliability_score, 2),
        "status": round(status_score, 2),
    }
    score = (
        duration_score * 0.42
        + stop_score * 0.24
        + reliability_score * 0.22
        + status_score * 0.12
    ) * priority_weight
    return round(min(score, 100), 2), breakdown


def build_mock_routes(request: SearchRequest) -> list[dict[str, Any]]:
    """Keeps the mock schedule data available for local use and degraded mode."""
    origin = normalize_airport(request.origin)
    destination = normalize_airport(request.destination)
    departure = request.departureTime or datetime.now(timezone.utc) + timedelta(hours=1)

    templates = [
        ("EMF101", "EmergeFly Air", 95, 0, 145, "A320neo", "scheduled"),
        ("MED214", "MedLink Express", 88, 0, 160, "B737-800", "boarding"),
        ("SKY330", "SkyBridge", 76, 1, 235, "A321", "scheduled"),
        ("RES909", "RescueJet", 84, 1, 260, "E190", "scheduled"),
    ]
    routes: list[dict[str, Any]] = []
    for index, (flight, airline, reliability, stops, duration, aircraft, status) in enumerate(templates, 1):
        offset = timedelta(minutes=35 * (index - 1))
        route_departure = departure + offset
        route_arrival = route_departure + timedelta(minutes=duration)
        routes.append(
            {
                "id": f"{origin}-{destination}-{flight}".lower(),
                "airline": airline,
                "flightNumber": flight,
                "origin": origin,
                "destination": destination,
                "departureTime": route_departure,
                "arrivalTime": route_arrival,
                "durationMinutes": duration,
                "stops": stops,
                "aircraft": aircraft,
                "status": status,
                "reliability": reliability,
                "source": "mock",
            }
        )
    return routes


class MockScheduleProvider:
    name = "mock"

    async def search(self, request: SearchRequest) -> list[dict[str, Any]]:
        return build_mock_routes(request)


class StubScheduleProvider:
    def __init__(self, name: str) -> None:
        self.name = name

    async def search(self, request: SearchRequest) -> list[dict[str, Any]]:
        logger.warning("%s provider: Not yet implemented - returning empty schedule", self.name)
        if not os.getenv("SCHEDULE_API_KEY"):
            logger.warning("%s provider selected without SCHEDULE_API_KEY", self.name)
        return []


def get_schedule_provider() -> ScheduleProvider:
    """Selects the schedule provider requested by SCHEDULE_PROVIDER."""
    provider_name = os.getenv("SCHEDULE_PROVIDER", "mock").strip().lower()
    if provider_name == "aviationstack":
        return StubScheduleProvider("aviationstack")
    if provider_name == "flightaware":
        return StubScheduleProvider("flightaware")
    if provider_name != "mock":
        logger.warning("Unknown SCHEDULE_PROVIDER=%s; using mock", provider_name)
    return MockScheduleProvider()


def enrich_routes(routes: list[dict[str, Any]], request: SearchRequest) -> list[RouteOption]:
    """Applies scoring and caches route details for /route/{id} lookups."""
    enriched: list[RouteOption] = []
    _route_cache.clear()
    for route in routes:
        score, breakdown = score_route(route, request.priority)
        route_data = {**route, "score": score, "scoreBreakdown": breakdown}
        option = RouteOption(**route_data)
        enriched.append(option)
        _route_cache[option.id] = option.model_dump(mode="json")
    enriched.sort(key=lambda item: item.score, reverse=True)
    return enriched


app = FastAPI(title="EmergeFly Flight Dashboard", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "flight-live-dashboard.html")


@app.get("/health")
async def health() -> dict[str, Any]:
    """Reports runtime health and whether live OpenSky credentials are configured."""
    provider = get_schedule_provider()
    return {
        "ok": True,
        "service": "EmergeFly",
        "openskyConfigured": opensky_credentials_configured(),
        "scheduleProvider": provider.name,
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest) -> SearchResponse:
    """Searches schedules and marks degradedMode when mock state data is in use."""
    provider = get_schedule_provider()
    states_task = asyncio.create_task(fetch_opensky_states())
    schedule_routes = await provider.search(request)
    states, state_degraded = await states_task

    if not schedule_routes:
        schedule_routes = build_mock_routes(request)
        schedule_degraded = provider.name != "mock"
    else:
        schedule_degraded = provider.name == "mock"

    routes = enrich_routes(schedule_routes, request)
    return SearchResponse(
        query=request,
        degradedMode=state_degraded or schedule_degraded,
        scheduleProvider=provider.name,
        stateProvider="mock" if state_degraded else "opensky",
        routes=routes,
    )


@app.get("/route/{route_id}")
async def route_detail(route_id: str) -> dict[str, Any]:
    """Returns the latest cached route detail from a previous search."""
    route = _route_cache.get(route_id)
    if not route:
        raise HTTPException(status_code=404, detail="Route not found. Run /search first.")
    return route


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("backend_flight_api:app", host="0.0.0.0", port=port, reload=False)
