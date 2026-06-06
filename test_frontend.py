import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_FRONTEND_E2E") != "1",
    reason="Set RUN_FRONTEND_E2E=1 and run the FastAPI server before frontend E2E tests.",
)

BASE = os.getenv("E2E_BASE_URL", "http://127.0.0.1:8080/")


def test_dashboard_loads(page):
    page.goto(BASE)

    assert "Flight Optimizer" in page.title()
    assert page.locator("#origin").count() == 1
    assert page.locator("#destination").count() == 1


def test_objective_pills_switch(page):
    page.goto(BASE)

    page.click("[data-sort='cost']")
    assert "active" in page.locator("[data-sort='cost']").get_attribute("class")
    assert "active" not in page.locator("[data-sort='balanced']").get_attribute("class")


def test_search_renders_route_details(page):
    page.goto(BASE)

    page.click("button[type='submit']")
    page.wait_for_selector(".route-card", timeout=5000)
    assert page.locator(".route-card").count() >= 1
    page.locator("[data-route-detail]").first.click()
    page.wait_for_selector(".route-detail", timeout=3000)
    assert page.locator(".leg-row").count() >= 1
