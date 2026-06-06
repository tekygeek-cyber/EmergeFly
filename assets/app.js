const form = document.querySelector("#searchForm");
const results = document.querySelector("#results");
const healthStatus = document.querySelector("#healthStatus");
const modeStatus = document.querySelector("#modeStatus");
const themeToggle = document.querySelector("#themeToggle");

const formatDateForInput = (date) => {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
};

const formatTime = (value) =>
  new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));

const setDefaultTimes = () => {
  const now = new Date();
  const later = new Date(now.getTime() + 12 * 60 * 60 * 1000);
  document.querySelector("#departureTime").value = formatDateForInput(now);
  document.querySelector("#arrivalTime").value = formatDateForInput(later);
};

const checkHealth = async () => {
  try {
    const response = await fetch("/health");
    const data = await response.json();
    healthStatus.textContent = data.ok
      ? `Service online · OpenSky ${data.openskyConfigured ? "configured" : "mock mode"}`
      : "Service unavailable";
    modeStatus.textContent = `Schedule provider: ${data.scheduleProvider}`;
  } catch (error) {
    healthStatus.textContent = "Service unavailable";
    modeStatus.textContent = "";
  }
};

const renderRoutes = (payload) => {
  modeStatus.textContent = `${payload.degradedMode ? "Degraded" : "Live"} · ${payload.scheduleProvider} schedule · ${payload.stateProvider} states`;

  if (!payload.routes.length) {
    results.innerHTML = '<div class="empty">No routes found for this search.</div>';
    return;
  }

  results.innerHTML = payload.routes
    .map(
      (route) => `
        <article class="route-card" data-route-id="${route.id}">
          <header>
            <div>
              <h2>${route.airline} ${route.flightNumber}</h2>
              <span>${route.origin} to ${route.destination}</span>
            </div>
            <div class="score">${route.score}</div>
          </header>
          <div class="meta">
            <span><strong>${formatTime(route.departureTime)}</strong>Depart</span>
            <span><strong>${formatTime(route.arrivalTime)}</strong>Arrive</span>
            <span><strong>${Math.floor(route.durationMinutes / 60)}h ${route.durationMinutes % 60}m</strong>Duration</span>
            <span><strong>${route.stops}</strong>Stops</span>
            <span><strong>${route.aircraft}</strong>Aircraft</span>
            <span><strong>${route.status}</strong>Status</span>
          </div>
          ${payload.degradedMode ? '<span class="badge">Mock-assisted result</span>' : ""}
          <button class="secondary-button" type="button" data-route-detail="${route.id}">Details</button>
        </article>
      `
    )
    .join("");
};

const loadRouteDetail = async (routeId, button) => {
  button.disabled = true;
  try {
    const response = await fetch(`/route/${encodeURIComponent(routeId)}`);
    if (!response.ok) {
      throw new Error(`Route lookup failed with ${response.status}`);
    }
    const route = await response.json();
    button.closest(".route-card").querySelector(".badge")?.remove();
    button.insertAdjacentHTML(
      "beforebegin",
      `<span class="badge">${route.source} source · ${route.aircraft}</span>`
    );
  } catch (error) {
    button.insertAdjacentHTML("beforebegin", '<span class="badge">Detail unavailable</span>');
  } finally {
    button.disabled = false;
  }
};

const submitSearch = async (event) => {
  event.preventDefault();
  results.innerHTML = '<div class="empty">Searching emergency-ready routes...</div>';

  const formData = new FormData(form);
  const body = {
    origin: formData.get("origin"),
    destination: formData.get("destination"),
    departureTime: new Date(formData.get("departureTime")).toISOString(),
    arrivalTime: new Date(formData.get("arrivalTime")).toISOString(),
    priority: formData.get("priority"),
  };

  try {
    const response = await fetch("/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      throw new Error(`Search failed with ${response.status}`);
    }
    renderRoutes(await response.json());
  } catch (error) {
    results.innerHTML = '<div class="empty">Search failed. Check that the backend is running.</div>';
  }
};

themeToggle.addEventListener("click", () => {
  const html = document.documentElement;
  html.dataset.theme = html.dataset.theme === "dark" ? "light" : "dark";
});

setDefaultTimes();
checkHealth();
form.addEventListener("submit", submitSearch);
results.addEventListener("click", (event) => {
  const button = event.target.closest("[data-route-detail]");
  if (button) {
    loadRouteDetail(button.dataset.routeDetail, button);
  }
});
