# EmergeFly 2.0

EmergeFly is a FastAPI flight optimizer for urgent travel planning. Version 2.0 ranks routes by cost, speed, reliability, delay risk, and connection safety, validates that search windows are not in the past, and matches scheduled legs with OpenSky live-state callsigns using OAuth credentials or anonymous OpenSky lookup.

## Folder structure

```text
repo/
├── backend_flight_api.py
├── flight-live-dashboard.html
├── assets/
│   ├── styles.css
│   └── app.js
├── requirements.txt
├── .env.example
├── .gitignore
├── test_backend.py
├── .github/workflows/ci.yml
└── README.md
```

## Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python backend_flight_api.py
```

Open `http://127.0.0.1:8080`.

## Environment variables

| Name | Default | Description |
| --- | --- | --- |
| `OPENSKY_CLIENT_ID` | empty | OpenSky OAuth2 client ID. Missing credentials use anonymous OpenSky lookup first. |
| `OPENSKY_CLIENT_SECRET` | empty | OpenSky OAuth2 client secret. Missing credentials use anonymous OpenSky lookup first. |
| `OPENSKY_CREDENTIALS_FILE` | empty | Path to an OpenSky account `credentials.json` file with `clientId` and `clientSecret`. Env vars take precedence. |
| `OPENSKY_BASE_URL` | `https://opensky-network.org/api` | Base URL for OpenSky state vectors. |
| `OPENSKY_AUTH_URL` | OpenSky token endpoint | OAuth2 token URL. |
| `SCHEDULE_PROVIDER` | `mock` | Schedule source: `mock`, `aviationstack`, or `flightaware`. |
| `SCHEDULE_API_KEY` | empty | Reserved for future schedule-provider integrations. |
| `DEFAULT_MCT_INTL_INTL` | `90` | Minimum international-to-international connection time in minutes. |
| `BUFFER_MINUTES` | `60` | Buffer above MCT for transfer-score scaling. |
| `MAX_RESULTS` | `5` | Default max routes returned. |
| `MIN_RELIABILITY` | `50.0` | Prune routes below this reliability score. |
| `PORT` | `8080` | Backend listen port. |

## Scoring

The optimizer uses five factors:

```text
Overall = w1*Reliability + w2*TransferScore + w3*(100-DelayRisk)
        + w4*CostScore + w5*DurationScore
```

Profiles: `balanced`, `budget`, `fastest`, `medical`, `family`, and `evac`.

Hard filters for `maxPriceUSD`, `maxDurationMin`, and `maxStops` are applied before normalization. MCT-violating routes are pruned and reported in `warnings`, so users can see why a connection was rejected.

## API reference

### `GET /health`

Returns runtime status:

```json
{
  "status": "ok",
  "routes_cached": 4,
  "degraded_mode": true,
  "opensky_live_ready": false,
  "schedule_provider": "mock",
  "profiles_available": ["balanced", "budget", "fastest", "medical", "family", "evac"]
}
```

### `POST /search`

Request:

```json
{
  "originIATA": "MLA",
  "destinationIATA": "DEL",
  "emergencyProfile": "budget",
  "sortBy": "cost",
  "maxStops": 2,
  "maxResults": 5,
  "maxPriceUSD": 800,
  "maxDurationMin": 900,
  "departWindow": {
    "startISO": "2026-06-07T08:00:00Z",
    "endISO": "2026-06-07T20:00:00Z"
  }
}
```

`originIATA` and `destinationIATA` must be different 3-letter IATA codes. `departWindow.startISO` and `departWindow.endISO` must both be in the future, and `endISO` must be after `startISO`.

Response:

```json
{
  "queryId": "uuid",
  "originIATA": "MLA",
  "destinationIATA": "DEL",
  "sortBy": "cost",
  "degradedMode": true,
  "warnings": ["OpenSky credentials missing - trying anonymous live state lookup."],
  "results": [
    {
      "routeId": "R-002",
      "totalDurationMin": 780,
      "arrivalETA": "2026-06-07T21:00:00Z",
      "priceUSD": 620,
      "stops": 1,
      "overallScore": 84.5,
      "costRank": 1,
      "speedRank": 3,
      "confidence": 63.4,
      "topReasons": ["Price $620 (cost score 92/100)", "Safest layover 120m"],
      "topRisks": ["OpenSky data missing - schedule ETA only"],
      "scores": {
        "reliability": 88,
        "transferScore": 50,
        "delayRisk": 22.4,
        "costScore": 92,
        "durationScore": 61,
        "overall": 84.5
      },
      "legs": []
    }
  ]
}
```

### `GET /route/{route_id}`

Returns the cached route detail from the most recent `/search`. Returns `404` if the route is not cached.

## Mock mode vs live mode

The schedule provider defaults to mock route data. OpenSky live-state matching uses OAuth2 when `OPENSKY_CLIENT_ID` and `OPENSKY_CLIENT_SECRET` are set, or when `OPENSKY_CREDENTIALS_FILE` points to an OpenSky account JSON file. If credentials are not set, the app still attempts anonymous OpenSky state-vector lookup with reduced rate limits; it falls back to mock state rows only when the OpenSky request fails. Live states are matched by leg callsign, so a route only receives full live confidence when every scheduled leg has a matching OpenSky state.

## Add a schedule provider

Create a provider class with a `name` attribute and an async `search(request)` method that returns route dictionaries with legs, price, reliability inputs, cancellation risk, and average delay. Register the provider in `get_schedule_provider()`. The `aviationstack` and `flightaware` providers currently log `Not yet implemented - returning empty schedule` and fall back to mock schedules.

## Test

```bash
pytest
```

The test suite covers `/health`, valid and invalid `/search` requests, past date validation, low-price empty results, MCT pruning warnings, and `/route/{id}` 404 behavior.

Additional verification:

```bash
# Unit scoring formulas
pytest test_scoring.py -v

# API integration and data integrity
OPENSKY_USE_MOCK_STATES=1 pytest test_backend.py -v

# Boundary fuzz tests
pytest test_fuzz.py -v --hypothesis-seed=0

# Optional frontend E2E, with the FastAPI server already running
RUN_FRONTEND_E2E=1 pytest test_frontend.py -v

# Optional load test
locust -f locustfile.py --host=http://127.0.0.1:8080 --users 50 --spawn-rate 5 --run-time 60s --headless
```

Load-test targets are zero `500` responses, error rate below `0.5%`, `/search` p95 latency below `300ms`, and stable behavior for invalid input under concurrency.

## Deploy to a VPS

Install Python 3.10 or later, clone the repository, create a virtual environment, and install `requirements.txt`. Run the backend behind nginx:

```bash
uvicorn backend_flight_api:app --host 127.0.0.1 --port 8080
```

Configure nginx to proxy HTTPS traffic to `http://127.0.0.1:8080`, then run Uvicorn under systemd or another process manager.

## Contributing

Keep changes focused on search quality, provider integrations, validation, or frontend correctness. Add tests for new scoring behavior and avoid committing secrets or real credentials.
