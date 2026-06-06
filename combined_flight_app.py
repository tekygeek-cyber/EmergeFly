from backend_flight_api import app


if __name__ == "__main__":
    import os

    import uvicorn

    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("combined_flight_app:app", host="0.0.0.0", port=port, reload=False)
