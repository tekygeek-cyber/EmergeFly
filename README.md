# EmergeFly

EmergeFly is a FastAPI-powered emergency flight dashboard for comparing urgent route options. It serves a lightweight HTML/CSS/JavaScript interface, scores available routes, and can use OpenSky OAuth2 credentials for live aircraft state data while keeping mock schedule data available for local use.

Note: the original source files referenced in the setup plan, `emergency_flight_scanner.py` and `FLIGHT-2.py`, were not present in this repository when this version was created. The current implementation recreates the planned combined backend and frontend contract from the project brief.

## Folder structure

```text
EmergeFly/
├── backend_flight_api.py
├── combined_flight_app.py
├── flight-live-dashboard.html
├── assets/
│   ├── styles.css
│   └── app.js
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

## Quickstart: single-file mode

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python combined_flight_app.py
```

Open `http://127.0.0.1:8080`.

## Quickstart: split mode

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend_flight_api:app --host 0.0.0.0 --port 8080
```

Open `http://127.0.0.1:8080`. The backend serves `flight-live-dashboard.html` and the `/assets` directory, so the frontend can call `/health`, `/search`, and `/route/{id}` from the same origin.

## Environment variables

| Name | Default | Description |
| --- | --- | --- |
| `OPENSKY_CLIENT_ID` | empty | OpenSky OAuth2 client ID. When missing, the app uses mock aircraft state data. |
| `OPENSKY_CLIENT_SECRET` | empty | OpenSky OAuth2 client secret. When missing, the app uses mock aircraft state data. |
| `SCHEDULE_PROVIDER` | `mock` | Schedule source. Supported values are `mock`, `aviationstack`, and `flightaware`. |
| `SCHEDULE_API_KEY` | empty | Reserved for future Aviationstack or FlightAware schedule integrations. |
| `PORT` | `8080` | Port used by `python backend_flight_api.py` or `python combined_flight_app.py`. |

## API reference

| Method | Path | Body | Response example |
| --- | --- | --- | --- |
| `GET` | `/` | None | Returns the HTML dashboard. |
| `GET` | `/health` | None | `{"ok":true,"service":"EmergeFly","openskyConfigured":false,"scheduleProvider":"mock","time":"2026-06-06T12:00:00+00:00"}` |
| `POST` | `/search` | `{"origin":"MLA","destination":"FCO","departureTime":"2026-06-06T10:00:00Z","arrivalTime":"2026-06-06T22:00:00Z","priority":"medical"}` | `{"degradedMode":true,"scheduleProvider":"mock","stateProvider":"mock","routes":[{"id":"mla-fco-emf101","score":98.1}]}` |
| `GET` | `/route/{route_id}` | None | Returns the cached route detail from the most recent `/search`, or `404` if the route is unknown. |

## Mock mode vs live mode

Schedule data defaults to the mock provider because real schedule integrations are not implemented yet. OpenSky state data uses live OAuth2 credentials when both `OPENSKY_CLIENT_ID` and `OPENSKY_CLIENT_SECRET` are configured; otherwise it falls back to mock state vectors and marks `/search` responses with `degradedMode: true`.

## Add a real schedule provider

Implement a new provider class with a `name` attribute and an async `search(self, request)` method returning route dictionaries that match `RouteOption`. Then update `get_schedule_provider()` in `backend_flight_api.py` to select it from `SCHEDULE_PROVIDER`. The current `aviationstack` and `flightaware` providers are stubs that log `Not yet implemented - returning empty schedule`.

## Run with OpenSky credentials

```bash
cp .env.example .env
export OPENSKY_CLIENT_ID="your-client-id"
export OPENSKY_CLIENT_SECRET="your-client-secret"
python combined_flight_app.py
```

The app caches OpenSky access tokens in memory and refreshes them with a 60-second expiry buffer.

## Deploy to a VPS

Install Python 3.10 or later, clone the repository, create a virtual environment, and install `requirements.txt`. Run the app with Uvicorn behind nginx:

```bash
uvicorn backend_flight_api:app --host 127.0.0.1 --port 8080
```

Configure nginx to proxy HTTPS traffic to `http://127.0.0.1:8080`, then run Uvicorn under a process manager such as systemd.

## Contributing

Keep changes focused on the flight search workflow, add small tests or manual verification notes for API behavior, and avoid committing secrets. When adding a real schedule provider, document the provider-specific environment variables and failure behavior in this README.
