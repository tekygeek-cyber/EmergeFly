from datetime import datetime, timedelta, timezone
import random

from locust import HttpUser, between, task

PROFILES = ["balanced", "budget", "fastest", "medical", "family", "evac"]
SORT_BY = ["cost", "speed", "balanced"]


def window() -> dict[str, str]:
    start = datetime.now(timezone.utc) + timedelta(days=1)
    end = start + timedelta(hours=14)
    return {"startISO": start.isoformat(), "endISO": end.isoformat()}


class FlightSearchUser(HttpUser):
    wait_time = between(0.5, 2)

    @task(5)
    def search_balanced(self):
        self.client.post(
            "/search",
            json={
                "originIATA": "MLA",
                "destinationIATA": "DEL",
                "emergencyProfile": random.choice(PROFILES),
                "sortBy": random.choice(SORT_BY),
                "maxResults": 5,
                "departWindow": window(),
            },
        )

    @task(3)
    def search_with_price_cap(self):
        self.client.post(
            "/search",
            json={
                "originIATA": "MLA",
                "destinationIATA": "DEL",
                "sortBy": "cost",
                "maxPriceUSD": random.choice([500, 700, 900, 1200]),
                "departWindow": window(),
            },
        )

    @task(2)
    def search_with_duration_cap(self):
        self.client.post(
            "/search",
            json={
                "originIATA": "MLA",
                "destinationIATA": "DEL",
                "sortBy": "speed",
                "maxDurationMin": random.choice([480, 600, 720, 1080]),
                "departWindow": window(),
            },
        )

    @task(2)
    def health_check(self):
        self.client.get("/health")

    @task(1)
    def get_route_detail(self):
        response = self.client.post(
            "/search",
            json={
                "originIATA": "MLA",
                "destinationIATA": "DEL",
                "maxResults": 10,
                "departWindow": window(),
            },
        )
        if response.status_code == 200 and response.json().get("results"):
            route_id = random.choice(response.json()["results"])["routeId"]
            self.client.get(f"/route/{route_id}")

    @task(1)
    def invalid_input(self):
        with self.client.post(
            "/search",
            json={
                "originIATA": "TOOLONG",
                "destinationIATA": "DEL",
                "departWindow": window(),
            },
            catch_response=True,
        ) as response:
            if response.status_code == 422:
                response.success()
