const form = document.querySelector("#searchForm");
const results = document.querySelector("#results");
const warningsBox = document.querySelector("#warnings");
const kpis = document.querySelector("#kpis");
const healthStatus = document.querySelector("#healthStatus");
const modeStatus = document.querySelector("#modeStatus");
const themeToggle = document.querySelector("#themeToggle");
const objectiveButtons = document.querySelectorAll("[data-sort]");

let activeSortBy = "balanced";

const formatDateForInput = (date) => {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
};

const formatTime = (value) =>
  new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));

const minutesLabel = (minutes) => `${Math.floor(minutes / 60)}h ${minutes % 60}m`;

const setDefaultTimes = () => {
  const now = new Date();
  const start = new Date(now.getTime() + 30 * 60 * 1000);
  const end = new Date(now.getTime() + 12 * 60 * 60 * 1000);
  const min = formatDateForInput(now);
  document.querySelector("#startISO").min = min;
  document.querySelector("#endISO").min = min;
  document.querySelector("#startISO").value = formatDateForInput(start);
  document.querySelector("#endISO").value = formatDateForInput(end);
};

const checkHealth = async () => {
  try {
    const response = await fetch("/health");
    const data = await response.json();
    healthStatus.textContent =
      data.status === "ok"
        ? `Service online · OpenSky ${data.opensky_live_ready ? "OAuth ready" : "anonymous live lookup"}`
        : "Service unavailable";
    modeStatus.textContent = `Provider: ${data.schedule_provider} · Profiles: ${data.profiles_available.length}`;
  } catch (error) {
    healthStatus.textContent = "Service unavailable";
    modeStatus.textContent = "";
  }
};

const buildPayload = () => {
  const startValue = document.querySelector("#startISO").value;
  const endValue = document.querySelector("#endISO").value;
  const start = new Date(startValue);
  const end = new Date(endValue);
  const now = new Date();

  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
    throw new Error("Choose a valid departure window.");
  }
  if (start < now || end < now) {
    throw new Error("Departure window cannot be in the past.");
  }
  if (end <= start) {
    throw new Error("End time must be after the start time.");
  }

  const payload = {
    originIATA: document.querySelector("#origin").value.trim().toUpperCase(),
    destinationIATA: document.querySelector("#destination").value.trim().toUpperCase(),
    emergencyProfile: document.querySelector("#profile").value,
    maxStops: Number(document.querySelector("#maxStops").value),
    maxResults: Number(document.querySelector("#maxResults").value),
    sortBy: activeSortBy,
    departWindow: {
      startISO: start.toISOString(),
      endISO: end.toISOString(),
    },
  };

  if (payload.originIATA === payload.destinationIATA) {
    throw new Error("Destination must be different from origin.");
  }

  const maxPrice = Number.parseFloat(document.querySelector("#maxPrice").value);
  const maxDuration = Number.parseInt(document.querySelector("#maxDuration").value, 10);
  if (!Number.isNaN(maxPrice) && maxPrice > 0) {
    payload.maxPriceUSD = maxPrice;
  }
  if (!Number.isNaN(maxDuration) && maxDuration > 0) {
    payload.maxDurationMin = maxDuration;
  }
  return payload;
};

const renderWarnings = (warnings) => {
  warningsBox.innerHTML = warnings.length
    ? warnings.map((warning) => `<span class="warning-chip">${warning}</span>`).join("")
    : "";
};

const renderKpis = (routes) => {
  if (!routes.length) {
    kpis.innerHTML = "";
    return;
  }
  const cheapest = routes.reduce((best, route) => (route.priceUSD < best.priceUSD ? route : best));
  const fastest = routes.reduce((best, route) => (route.totalDurationMin < best.totalDurationMin ? route : best));
  const best = routes.reduce((winner, route) => (route.overallScore > winner.overallScore ? route : winner));
  kpis.innerHTML = `
    <div><strong>$${cheapest.priceUSD}</strong><span>Cheapest · ${cheapest.routeId}</span></div>
    <div><strong>${minutesLabel(fastest.totalDurationMin)}</strong><span>Fastest · ${fastest.routeId}</span></div>
    <div><strong>${best.overallScore}</strong><span>Best score · ${best.routeId}</span></div>
  `;
};

const renderRoutes = (payload) => {
  modeStatus.textContent = `${payload.degradedMode ? "Mock/degraded" : "Live OpenSky"} · ${payload.originIATA} to ${payload.destinationIATA} · sorted by ${payload.sortBy}`;
  renderWarnings([
    ...payload.warnings,
    "Route prices are schedule-provider values, not live fare quotes. Compare with a fare provider before booking.",
  ]);
  renderKpis(payload.results);

  if (!payload.results.length) {
    results.innerHTML = '<div class="empty">No routes found for this search.</div>';
    return;
  }

  results.innerHTML = payload.results
    .map((route) => {
      const firstLeg = route.legs[0];
      const lastLeg = route.legs[route.legs.length - 1];
      return `
        <article class="route-card" data-route-id="${route.routeId}">
          <header>
            <div>
              <h2>${route.routeId} · ${firstLeg.origin} to ${lastLeg.destination}</h2>
              <span>${route.legs.map((leg) => leg.airline).join(" + ")}</span>
            </div>
            <div class="score">${route.overallScore}</div>
          </header>
          <div class="meta">
            <span><strong>$${route.priceUSD}</strong>Price</span>
            <span><strong>${minutesLabel(route.totalDurationMin)}</strong>Duration</span>
            <span><strong>${route.stops}</strong>Stops</span>
            <span><strong>#${route.costRank}</strong>Cost rank</span>
            <span><strong>#${route.speedRank}</strong>Speed rank</span>
            <span><strong>${route.confidence}</strong>Confidence</span>
          </div>
          <div class="timeline">
            <span>${formatTime(firstLeg.departureISO)}</span>
            <span>${formatTime(route.arrivalETA)}</span>
          </div>
          <div class="chips">
            ${route.topReasons.map((reason) => `<span class="badge">${reason}</span>`).join("")}
            ${route.topRisks.map((risk) => `<span class="risk">${risk}</span>`).join("")}
          </div>
          <button class="secondary-button" type="button" data-route-detail="${route.routeId}">Details</button>
        </article>
      `;
    })
    .join("");
};

const loadRouteDetail = async (routeId, button) => {
  const card = button.closest(".route-card");
  const existing = card.querySelector(".route-detail");
  if (existing) {
    existing.remove();
    button.textContent = "Details";
    return;
  }

  button.disabled = true;
  try {
    const response = await fetch(`/route/${encodeURIComponent(routeId)}`);
    if (!response.ok) {
      throw new Error(`Route lookup failed with ${response.status}`);
    }
    const route = await response.json();
    button.insertAdjacentHTML("beforebegin", renderRouteDetail(route));
    button.textContent = "Hide details";
  } catch (error) {
    button.insertAdjacentHTML("beforebegin", '<span class="risk">Detail unavailable</span>');
  } finally {
    button.disabled = false;
  }
};

const renderRouteDetail = (route) => {
  const live = route.dataCompleteness;
  const rows = route.legs
    .map((leg, index) => {
      const nextLeg = route.legs[index + 1];
      const connection = nextLeg
        ? Math.round((new Date(nextLeg.departureISO) - new Date(leg.arrivalISO)) / 60000)
        : null;
      const liveState = leg.liveState;
      return `
        <div class="leg-row">
          <div>
            <strong>${leg.flightNumber} · ${leg.origin} to ${leg.destination}</strong>
            <span>${leg.airline} · ${leg.aircraft}</span>
          </div>
          <div>
            <strong>${formatTime(leg.departureISO)}</strong>
            <span>Depart</span>
          </div>
          <div>
            <strong>${formatTime(leg.arrivalISO)}</strong>
            <span>Arrive</span>
          </div>
          <div>
            <strong>${minutesLabel(leg.durationMin)}</strong>
            <span>Flight time</span>
          </div>
          <div>
            <strong>${liveState ? liveState.source : "missing"}</strong>
            <span>${liveState?.fresh ? "Fresh live match" : "No fresh live match"}</span>
          </div>
        </div>
        ${
          connection !== null
            ? `<div class="connection-row"><span>Connection in ${leg.destination}</span><strong>· ${minutesLabel(connection)}</strong></div>`
            : ""
        }
      `;
    })
    .join("");

  return `
    <section class="route-detail">
      <div class="detail-summary">
        <span>${live.matchedLiveLegs}/${live.totalLegs} live state matches · ${live.liveState}</span>
        <span>Cost rank #${route.costRank} · Speed rank #${route.speedRank}</span>
      </div>
      ${rows}
    </section>
  `;
};

const submitSearch = async (event) => {
  event.preventDefault();
  results.innerHTML = '<div class="empty">Optimizing cost, speed, and connection safety...</div>';
  warningsBox.innerHTML = "";
  kpis.innerHTML = "";

  let body;
  try {
    body = buildPayload();
  } catch (error) {
    results.innerHTML = `<div class="empty">${error.message}</div>`;
    return;
  }

  try {
    const response = await fetch("/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail?.[0]?.msg || "Search failed.");
    }
    renderRoutes(payload);
  } catch (error) {
    results.innerHTML = `<div class="empty">${error.message}</div>`;
  }
};

objectiveButtons.forEach((button) => {
  button.addEventListener("click", () => {
    activeSortBy = button.dataset.sort;
    objectiveButtons.forEach((item) => item.classList.toggle("active", item === button));
  });
});

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
