import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger("emergefly")
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"

DEFAULT_OPENSKY_AUTH_URL = (
    "https://auth.opensky-network.org/auth/realms/opensky-network/"
    "protocol/openid-connect/token"
)
DEFAULT_OPENSKY_BASE_URL = "https://opensky-network.org/api"

WEIGHT_PROFILES = {
    "balanced": {"w1": 0.15, "w2": 0.10, "w3": 0.10, "w4": 0.35, "w5": 0.30},
    "budget": {"w1": 0.15, "w2": 0.10, "w3": 0.10, "w4": 0.50, "w5": 0.15},
    "fastest": {"w1": 0.15, "w2": 0.15, "w3": 0.10, "w4": 0.10, "w5": 0.50},
    "medical": {"w1": 0.45, "w2": 0.25, "w3": 0.20, "w4": 0.05, "w5": 0.05},
    "family": {"w1": 0.30, "w2": 0.20, "w3": 0.15, "w4": 0.20, "w5": 0.15},
    "evac": {"w1": 0.40, "w2": 0.30, "w3": 0.20, "w4": 0.05, "w5": 0.05},
}

_token_cache = {"access_token": None, "expires_at": 0.0}
_route_cache: dict[str, dict[str, Any]] = {}
_last_degraded_mode = True


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, value))


class TimeWindow(BaseModel):
    startISO: datetime
    endISO: datetime

    @model_validator(mode="after")
    def validate_future_window(self) -> "TimeWindow":
        self.startISO = as_utc(self.startISO)
        self.endISO = as_utc(self.endISO)
        now = utc_now()
        if self.startISO < now:
            raise ValueError("departWindow.startISO cannot be in the past")
        if self.endISO < now:
            raise ValueError("departWindow.endISO cannot be in the past")
        if self.endISO <= self.startISO:
            raise ValueError("departWindow.endISO must be after startISO")
        return self


class SearchRequest(BaseModel):
    originIATA: str = "MLA"
    destinationIATA: str = "DEL"
    departWindow: TimeWindow
    maxStops: int = Field(default=2, ge=0, le=3)
    maxResults: int = Field(default_factory=lambda: env_int("MAX_RESULTS", 5), ge=1, le=25)
    emergencyProfile: str = "balanced"
    sortBy: Literal["cost", "speed", "balanced"] = "balanced"
    maxPriceUSD: float | None = Field(default=None, gt=0)
    maxDurationMin: int | None = Field(default=None, gt=0)

    @field_validator("originIATA", "destinationIATA")
    @classmethod
    def validate_iata(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", normalized):
            raise ValueError("IATA codes must be 3 uppercase letters")
        return normalized

    @model_validator(mode="after")
    def validate_route_airports(self) -> "SearchRequest":
        if self.originIATA == self.destinationIATA:
            raise ValueError("destinationIATA must be different from originIATA")
        return self

    @field_validator("emergencyProfile")
    @classmethod
    def validate_profile(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in WEIGHT_PROFILES:
            raise ValueError(f"emergencyProfile must be one of {', '.join(WEIGHT_PROFILES)}")
        return normalized


class LiveState(BaseModel):
    icao24: str | None = None
    callsign: str
    lat: float | None = None
    lon: float | None = None
    groundSpeed: float | None = None
    baroAltitudeM: float | None = None
    onGround: bool | None = None
    lastContactUnix: int | None = None
    spi: bool = False
    source: Literal["opensky", "mock", "missing"] = "missing"
    fresh: bool = False


class Leg(BaseModel):
    flightNumber: str
    airline: str
    origin: str
    destination: str
    departureISO: datetime
    arrivalISO: datetime
    durationMin: int
    aircraft: str
    liveState: LiveState | None = None


class RouteScores(BaseModel):
    reliability: float
    transferScore: float
    delayRisk: float
    costScore: float
    durationScore: float
    overall: float


class DataCompleteness(BaseModel):
    schedule: Literal["mock", "aviationstack", "flightaware"]
    liveState: Literal["opensky", "mock", "missing"]
    matchedLiveLegs: int
    totalLegs: int


class RouteSummary(BaseModel):
    routeId: str
    totalDurationMin: int
    arrivalETA: datetime
    priceUSD: float
    stops: int
    overallScore: float
    costRank: int = 0
    speedRank: int = 0
    confidence: float
    topReasons: list[str]
    topRisks: list[str]
    scores: RouteScores
    legs: list[Leg]
    dataCompleteness: DataCompleteness


class SearchResponse(BaseModel):
    queryId: str
    generatedAt: datetime
    originIATA: str
    destinationIATA: str
    sortBy: str
    degradedMode: bool
    warnings: list[str]
    results: list[RouteSummary]


class ScheduleProvider(Protocol):
    name: Literal["mock", "aviationstack", "flightaware"]

    async def search(self, request: SearchRequest) -> list[dict[str, Any]]:
        ...


def compute_reliability(p_ontime: float, c_cancel: float) -> float:
    return round(clamp(100 * p_ontime * (1 - c_cancel)), 4)


def compute_transfer_score(layover_min: int, mct: int, buffer: int, n_transfers: int) -> float:
    if n_transfers == 0:
        return 100.0
    if layover_min < mct:
        return 0.0
    score = 100 * min(1, (layover_min - mct) / max(buffer, 1)) * (1 / (1 + n_transfers))
    return round(clamp(score), 4)


def compute_delay_risk(p_ontime: float, avg_delay_min: float) -> float:
    return round(clamp(100 * (1 - p_ontime) * (1 + min(avg_delay_min, 180) / 60)), 4)


def compute_cost_score(price: float, min_price: float, max_price: float) -> float:
    return round(clamp(100 * (1 - (price - min_price) / (max_price - min_price + 1e-5))), 4)


def compute_duration_score(duration_min: int, min_dur: int, max_dur: int) -> float:
    return round(clamp(100 * (1 - (duration_min - min_dur) / (max_dur - min_dur + 1e-5))), 4)


def compute_overall_score(scores: dict[str, float], weights: dict[str, float]) -> float:
    overall = (
        weights["w1"] * scores["reliability"]
        + weights["w2"] * scores["transferScore"]
        + weights["w3"] * (100 - scores["delayRisk"])
        + weights["w4"] * scores["costScore"]
        + weights["w5"] * scores["durationScore"]
    )
    return round(clamp(overall), 2)


def opensky_live_ready() -> bool:
    return bool(os.getenv("OPENSKY_CLIENT_ID") and os.getenv("OPENSKY_CLIENT_SECRET"))


async def get_opensky_token() -> str | None:
    """Fetch and cache an OpenSky OAuth token with a 60-second refresh buffer."""
    now = time.time()
    if _token_cache["access_token"] and float(_token_cache["expires_at"]) > now + 60:
        return str(_token_cache["access_token"])
    if not opensky_live_ready():
        return None

    auth_url = os.getenv("OPENSKY_AUTH_URL", DEFAULT_OPENSKY_AUTH_URL)
    payload = {
        "grant_type": "client_credentials",
        "client_id": os.environ["OPENSKY_CLIENT_ID"],
        "client_secret": os.environ["OPENSKY_CLIENT_SECRET"],
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(auth_url, data=payload)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("OpenSky token request failed: %s", exc)
        return None

    token = data.get("access_token")
    if not token:
        logger.warning("OpenSky token response did not include access_token")
        return None
    _token_cache["access_token"] = token
    _token_cache["expires_at"] = now + int(data.get("expires_in", 1800))
    return str(token)


def map_opensky_state(item: list[Any], source: Literal["opensky", "mock"]) -> LiveState:
    return LiveState(
        icao24=item[0],
        callsign=(item[1] or "").strip(),
        lat=item[6],
        lon=item[5],
        groundSpeed=item[9],
        baroAltitudeM=item[7],
        onGround=bool(item[8]),
        lastContactUnix=item[4],
        spi=bool(item[15]) if len(item) > 15 else False,
        source=source,
        fresh=bool(item[4] and utc_now().timestamp() - int(item[4]) < 900),
    )


def mock_opensky_state_rows() -> list[list[Any]]:
    now = int(utc_now().timestamp())
    return [
        ["4d2211", "AI101", None, now - 60, now - 45, 77.1, 23.8, 10972, False, 239, 90, 0, None, 11200, None, False],
        ["4ca7b3", "TK721", None, now - 80, now - 55, 28.9, 41.2, 10340, False, 232, 120, 0, None, 10600, None, False],
        ["4b9901", "LH765", None, now - 40, now - 30, 13.4, 47.1, 11200, False, 245, 110, 0, None, 11300, None, False],
        ["4d2260", "KM614", None, now - 70, now - 50, 12.2, 42.3, 9800, False, 210, 80, 0, None, 10000, None, False],
        ["7102aa", "EK112", None, now - 5000, now - 4900, 55.1, 25.2, 10800, False, 238, 100, 0, None, 10950, None, False],
    ]


async def fetch_opensky_states(callsigns: set[str]) -> tuple[dict[str, LiveState], bool, list[str]]:
    """Return OpenSky states keyed by callsign, matching live data to scheduled legs."""
    if os.getenv("OPENSKY_USE_MOCK_STATES") == "1":
        states = {
            state.callsign: state
            for state in (map_opensky_state(row, "mock") for row in mock_opensky_state_rows())
            if state.callsign in callsigns
        }
        return states, True, ["OpenSky mock state mode forced by environment."]

    token = await get_opensky_token()
    degraded = False
    warnings: list[str] = []
    source: Literal["opensky", "mock"] = "opensky"

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if not token:
        warnings.append("OpenSky credentials missing - trying anonymous live state lookup.")

    base_url = os.getenv("OPENSKY_BASE_URL", DEFAULT_OPENSKY_BASE_URL).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(f"{base_url}/states/all", headers=headers)
            response.raise_for_status()
            rows = response.json().get("states") or []
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("OpenSky state request failed: %s", exc)
        rows = mock_opensky_state_rows()
        source = "mock"
        degraded = True
        warnings.append("OpenSky request failed - using mock live states.")

    states: dict[str, LiveState] = {}
    for row in rows:
        if len(row) <= 9:
            continue
        state = map_opensky_state(row, source)
        if state.callsign in callsigns:
            states[state.callsign] = state
    return states, degraded, warnings


def leg(
    flight: str,
    airline: str,
    origin: str,
    destination: str,
    depart: datetime,
    duration_min: int,
    aircraft: str,
) -> dict[str, Any]:
    return {
        "flightNumber": flight,
        "airline": airline,
        "origin": origin,
        "destination": destination,
        "departureISO": depart,
        "arrivalISO": depart + timedelta(minutes=duration_min),
        "durationMin": duration_min,
        "aircraft": aircraft,
    }


def route_layovers(legs: list[dict[str, Any]]) -> list[int]:
    return [
        int((legs[index + 1]["departureISO"] - legs[index]["arrivalISO"]).total_seconds() / 60)
        for index in range(len(legs) - 1)
    ]


def total_duration(legs: list[dict[str, Any]]) -> int:
    return int((legs[-1]["arrivalISO"] - legs[0]["departureISO"]).total_seconds() / 60)


def build_route(
    route_id: str,
    price: float,
    p_ontime: float,
    c_cancel: float,
    avg_delay_min: float,
    legs: list[dict[str, Any]],
    provider: str,
) -> dict[str, Any]:
    return {
        "routeId": route_id,
        "priceUSD": price,
        "pOnTime": p_ontime,
        "cancelRisk": c_cancel,
        "avgDelayMin": avg_delay_min,
        "legs": legs,
        "stops": len(legs) - 1,
        "totalDurationMin": total_duration(legs),
        "arrivalETA": legs[-1]["arrivalISO"],
        "provider": provider,
    }


def build_mock_schedule(request: SearchRequest) -> list[dict[str, Any]]:
    """Create deterministic mock routes whose times are derived from the search window."""
    start = request.departWindow.startISO
    origin = request.originIATA
    destination = request.destinationIATA
    return [
        build_route(
            "R-001",
            850,
            0.90,
            0.03,
            18,
            [leg("AI101", "Air India", origin, destination, start + timedelta(minutes=30), 480, "B787-9")],
            "mock",
        ),
        build_route(
            "R-002",
            620,
            0.88,
            0.02,
            22,
            [
                leg("TK721", "Turkish Airlines", origin, "IST", start + timedelta(minutes=45), 150, "A321"),
                leg("TK716", "Turkish Airlines", "IST", destination, start + timedelta(minutes=45 + 150 + 120), 510, "A330"),
            ],
            "mock",
        ),
        build_route(
            "R-003",
            500,
            0.84,
            0.04,
            36,
            [
                leg("EK112", "Emirates", origin, "DXB", start + timedelta(minutes=50), 410, "B777"),
                leg("EK512", "Emirates", "DXB", destination, start + timedelta(minutes=50 + 410 + 45), 85, "B777"),
            ],
            "mock",
        ),
        build_route(
            "R-004",
            1100,
            0.93,
            0.01,
            12,
            [leg("LH765", "Lufthansa", origin, destination, start + timedelta(minutes=90), 480, "A350")],
            "mock",
        ),
        build_route(
            "R-005",
            420,
            0.76,
            0.08,
            44,
            [
                leg("A3621", "Aegean", origin, "ATH", start + timedelta(minutes=120), 95, "A320"),
                leg("AI172", "Air India", "ATH", "BOM", start + timedelta(minutes=120 + 95 + 150), 465, "B787"),
                leg("AI241", "Air India", "BOM", destination, start + timedelta(minutes=120 + 95 + 150 + 465 + 110), 110, "A320"),
            ],
            "mock",
        ),
        build_route(
            "R-006",
            710,
            0.86,
            0.03,
            28,
            [
                leg("KM614", "KM Malta Airlines", origin, "FCO", start + timedelta(minutes=75), 85, "A320"),
                leg("AI148", "Air India", "FCO", destination, start + timedelta(minutes=75 + 85 + 135), 585, "B787"),
            ],
            "mock",
        ),
    ]


class MockScheduleProvider:
    name: Literal["mock"] = "mock"

    async def search(self, request: SearchRequest) -> list[dict[str, Any]]:
        return build_mock_schedule(request)


class StubScheduleProvider:
    def __init__(self, name: Literal["aviationstack", "flightaware"]) -> None:
        self.name = name

    async def search(self, request: SearchRequest) -> list[dict[str, Any]]:
        logger.warning("%s provider: Not yet implemented - returning empty schedule", self.name)
        if not os.getenv("SCHEDULE_API_KEY"):
            logger.warning("%s provider selected without SCHEDULE_API_KEY", self.name)
        return []


def get_schedule_provider() -> ScheduleProvider:
    provider_name = os.getenv("SCHEDULE_PROVIDER", "mock").strip().lower()
    if provider_name == "aviationstack":
        return StubScheduleProvider("aviationstack")
    if provider_name == "flightaware":
        return StubScheduleProvider("flightaware")
    if provider_name != "mock":
        logger.warning("Unknown SCHEDULE_PROVIDER=%s; using mock", provider_name)
    return MockScheduleProvider()


def build_explanations(
    route: dict[str, Any],
    scores: dict[str, float],
    live_states: dict[str, LiveState],
    state_degraded: bool,
) -> tuple[list[str], list[str], float, DataCompleteness]:
    reasons = [
        f"Price ${route['priceUSD']:.0f} (cost score {scores['costScore']:.0f}/100)",
        f"Total duration {route['totalDurationMin']}m (speed score {scores['durationScore']:.0f}/100)",
    ]
    risks: list[str] = []
    layovers = route_layovers(route["legs"])
    if layovers:
        reasons.append(f"Safest layover {min(layovers)}m")
    if scores["delayRisk"] > 40:
        risks.append(f"Elevated delay risk {scores['delayRisk']:.0f}/100")

    matched = sum(1 for item in route["legs"] if item["flightNumber"] in live_states)
    if state_degraded:
        risks.append("OpenSky data missing - schedule ETA only")
    elif matched < len(route["legs"]):
        risks.append("Some legs are missing live OpenSky state matches")
    else:
        reasons.append("All legs matched current OpenSky callsign data")

    if matched == len(route["legs"]):
        sky_bonus = 1.0
        live_source: Literal["opensky", "mock", "missing"] = next(iter(live_states.values())).source if live_states else "missing"
    elif matched > 0:
        sky_bonus = 0.75
        live_source = "mock" if state_degraded else "opensky"
    else:
        sky_bonus = 0.5
        live_source = "missing"

    completeness = DataCompleteness(
        schedule=route["provider"],
        liveState=live_source,
        matchedLiveLegs=matched,
        totalLegs=len(route["legs"]),
    )
    return reasons[:3], risks[:3], round(scores["overall"] * sky_bonus, 2), completeness


def materialize_route(
    route: dict[str, Any],
    scores: RouteScores,
    live_states: dict[str, LiveState],
    state_degraded: bool,
) -> RouteSummary:
    legs = []
    for item in route["legs"]:
        state = live_states.get(item["flightNumber"])
        legs.append(Leg(**item, liveState=state))
    reasons, risks, confidence, completeness = build_explanations(route, scores.model_dump(), live_states, state_degraded)
    return RouteSummary(
        routeId=route["routeId"],
        totalDurationMin=route["totalDurationMin"],
        arrivalETA=route["arrivalETA"],
        priceUSD=route["priceUSD"],
        stops=route["stops"],
        overallScore=scores.overall,
        confidence=confidence,
        topReasons=reasons,
        topRisks=risks,
        scores=scores,
        legs=legs,
        dataCompleteness=completeness,
    )


async def run_search(request: SearchRequest) -> SearchResponse:
    provider = get_schedule_provider()
    warnings: list[str] = []
    candidates = await provider.search(request)
    schedule_degraded = provider.name != "mock"
    if not candidates:
        warnings.append(f"{provider.name} schedule provider returned no routes - using mock schedule.")
        candidates = build_mock_schedule(request)
        schedule_degraded = True

    filtered = [
        route
        for route in candidates
        if route["stops"] <= request.maxStops
        and (request.maxPriceUSD is None or route["priceUSD"] <= request.maxPriceUSD)
        and (request.maxDurationMin is None or route["totalDurationMin"] <= request.maxDurationMin)
    ]

    if not filtered:
        warnings.append("No routes survived price, stop, or duration filters.")
        return SearchResponse(
            queryId=str(uuid4()),
            generatedAt=utc_now(),
            originIATA=request.originIATA,
            destinationIATA=request.destinationIATA,
            sortBy=request.sortBy,
            degradedMode=True,
            warnings=warnings,
            results=[],
        )

    callsigns = {item["flightNumber"] for route in filtered for item in route["legs"]}
    live_states, state_degraded, state_warnings = await fetch_opensky_states(callsigns)
    warnings.extend(state_warnings)

    min_price = min(route["priceUSD"] for route in filtered)
    max_price = max(route["priceUSD"] for route in filtered)
    min_dur = min(route["totalDurationMin"] for route in filtered)
    max_dur = max(route["totalDurationMin"] for route in filtered)
    weights = WEIGHT_PROFILES[request.emergencyProfile]
    mct = env_int("DEFAULT_MCT_INTL_INTL", 90)
    buffer = env_int("BUFFER_MINUTES", 60)
    min_reliability = env_float("MIN_RELIABILITY", 50.0)

    survivors: list[RouteSummary] = []
    for route in filtered:
        reliability = compute_reliability(route["pOnTime"], route["cancelRisk"])
        if reliability < min_reliability:
            warnings.append(f"{route['routeId']} pruned: reliability {reliability:.1f} below minimum {min_reliability:.1f}.")
            continue

        layovers = route_layovers(route["legs"])
        transfer_score = 100.0
        if layovers:
            transfer_score = min(
                compute_transfer_score(layover, mct, buffer, route["stops"])
                for layover in layovers
            )
        if transfer_score == 0 and route["stops"] > 0:
            warnings.append(f"{route['routeId']} pruned: minimum connection time violated.")
            continue

        score_map = {
            "reliability": reliability,
            "transferScore": transfer_score,
            "delayRisk": compute_delay_risk(route["pOnTime"], route["avgDelayMin"]),
            "costScore": compute_cost_score(route["priceUSD"], min_price, max_price),
            "durationScore": compute_duration_score(route["totalDurationMin"], min_dur, max_dur),
        }
        score_map["overall"] = compute_overall_score(score_map, weights)
        survivors.append(materialize_route(route, RouteScores(**score_map), live_states, state_degraded))

    for rank, route in enumerate(sorted(survivors, key=lambda item: item.priceUSD), 1):
        route.costRank = rank
    for rank, route in enumerate(sorted(survivors, key=lambda item: item.totalDurationMin), 1):
        route.speedRank = rank

    if request.sortBy == "cost":
        survivors.sort(key=lambda item: item.priceUSD)
    elif request.sortBy == "speed":
        survivors.sort(key=lambda item: item.totalDurationMin)
    else:
        survivors.sort(key=lambda item: item.overallScore, reverse=True)

    survivors = survivors[: request.maxResults]
    _route_cache.clear()
    for route in survivors:
        _route_cache[route.routeId] = route.model_dump(mode="json")

    degraded = state_degraded or schedule_degraded
    global _last_degraded_mode
    _last_degraded_mode = degraded
    return SearchResponse(
        queryId=str(uuid4()),
        generatedAt=utc_now(),
        originIATA=request.originIATA,
        destinationIATA=request.destinationIATA,
        sortBy=request.sortBy,
        degradedMode=degraded,
        warnings=warnings,
        results=survivors,
    )


app = FastAPI(title="EmergeFly Flight Optimizer", version="2.0.0")
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
    provider = get_schedule_provider()
    return {
        "status": "ok",
        "time": utc_now().isoformat(),
        "routes_cached": len(_route_cache),
        "degraded_mode": _last_degraded_mode,
        "opensky_live_ready": opensky_live_ready(),
        "schedule_provider": provider.name,
        "profiles_available": list(WEIGHT_PROFILES.keys()),
    }


@app.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest) -> SearchResponse:
    return await run_search(request)


@app.get("/route/{route_id}")
async def route_detail(route_id: str) -> dict[str, Any]:
    route = _route_cache.get(route_id)
    if not route:
        raise HTTPException(status_code=404, detail="Route not found. Run /search first.")
    return route


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("backend_flight_api:app", host="0.0.0.0", port=port, reload=False)
