# Combined flight app

Run:

```bash
pip install fastapi uvicorn pydantic
python combined_flight_app.py
```

Then open `http://127.0.0.1:8080`

Features:
- Combines the scoring/search logic from both attached Python files
- Serves a built-in HTML dashboard from FastAPI
- Uses JSON between frontend and backend (`POST /search`)
- Includes `/health` and `/route/{route_id}` endpoints
- Works in mock mode now; ready for live OpenSky/schedule integration
