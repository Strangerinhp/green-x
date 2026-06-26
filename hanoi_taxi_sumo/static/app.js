const initialState = window.__INITIAL_STATE__ || {};

const text = (id, value) => {
    const node = document.getElementById(id);
    if (node) {
        node.textContent = value;
    }
};

const formatVnd = (value) => {
    const safeValue = Number.isFinite(value) ? value : 0;
    return new Intl.NumberFormat("en-US").format(Math.round(safeValue)) + " VND";
};

const formatNumber = (value, digits = 0) => {
    const safeValue = Number.isFinite(value) ? value : 0;
    return safeValue.toFixed(digits);
};

const MAX_CHART_POINTS = 80;

const mapCenter = [
    initialState?.meta?.center?.lat || 21.0288,
    initialState?.meta?.center?.lon || 105.8523,
];

const map = L.map("map", {
    zoomControl: true,
    attributionControl: true,
}).setView(mapCenter, 15);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);

const taxiLayer = L.layerGroup().addTo(map);
const hotspotLayer = L.layerGroup().addTo(map);
const serviceAreaLayer = L.layerGroup().addTo(map);
let lastMapMetaKey = "";

const opsChart = new Chart(document.getElementById("ops-chart"), {
    type: "line",
    data: {
        labels: [],
        datasets: [
            {
                label: "Completed trips",
                data: [],
                borderColor: "#1f7a8c",
                backgroundColor: "rgba(31, 122, 140, 0.15)",
                borderWidth: 2.5,
                tension: 0.3,
                yAxisID: "y",
            },
            {
                label: "Pending requests",
                data: [],
                borderColor: "#d1495b",
                backgroundColor: "rgba(209, 73, 91, 0.12)",
                borderWidth: 2.5,
                tension: 0.3,
                yAxisID: "y",
            },
            {
                label: "Utilization %",
                data: [],
                borderColor: "#d4a017",
                backgroundColor: "rgba(212, 160, 23, 0.12)",
                borderWidth: 2,
                tension: 0.3,
                yAxisID: "y1",
            },
        ],
    },
    options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
            y: {
                position: "left",
                beginAtZero: true,
            },
            y1: {
                position: "right",
                beginAtZero: true,
                grid: {
                    drawOnChartArea: false,
                },
            },
        },
        plugins: {
            legend: {
                position: "bottom",
            },
        },
    },
});

const economyChart = new Chart(document.getElementById("economy-chart"), {
    type: "line",
    data: {
        labels: [],
        datasets: [
            {
                label: "Revenue (million VND)",
                data: [],
                borderColor: "#a63d40",
                backgroundColor: "rgba(166, 61, 64, 0.14)",
                borderWidth: 2.5,
                tension: 0.3,
                yAxisID: "y",
            },
            {
                label: "CO2 (kg)",
                data: [],
                borderColor: "#2f3542",
                backgroundColor: "rgba(47, 53, 66, 0.12)",
                borderWidth: 2.3,
                tension: 0.3,
                yAxisID: "y1",
            },
        ],
    },
    options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
            y: {
                position: "left",
                beginAtZero: true,
            },
            y1: {
                position: "right",
                beginAtZero: true,
                grid: {
                    drawOnChartArea: false,
                },
            },
        },
        plugins: {
            legend: {
                position: "bottom",
            },
        },
    },
});

function renderHotspots(hotspots) {
    hotspotLayer.clearLayers();
    (hotspots || []).forEach((hotspot) => {
        const marker = L.circleMarker([hotspot.lat, hotspot.lon], {
            radius: 6 + hotspot.weight * 2,
            color: "#a63d40",
            weight: 2,
            fillColor: "#f4a259",
            fillOpacity: 0.75,
        });
        marker.bindTooltip(`Demand hotspot: ${hotspot.name}`, { direction: "top" });
        hotspotLayer.addLayer(marker);
    });
}

function renderServiceArea(bbox) {
    serviceAreaLayer.clearLayers();
    if (!bbox) {
        return;
    }

    const bounds = [
        [bbox.south, bbox.west],
        [bbox.north, bbox.east],
    ];
    L.rectangle(bounds, {
        color: "#1f7a8c",
        weight: 2,
        dashArray: "8 6",
        fillOpacity: 0.03,
    }).addTo(serviceAreaLayer);
}

function renderTaxis(taxis) {
    taxiLayer.clearLayers();
    (taxis || []).forEach((taxi) => {
        const marker = L.circleMarker([taxi.lat, taxi.lon], {
            radius: taxi.state === "occupied" || taxi.state === "shared" ? 7 : 6,
            color: taxi.color,
            weight: 2,
            fillColor: taxi.color,
            fillOpacity: 0.75,
        });
        marker.bindPopup(`
            <strong>${taxi.id}</strong><br>
            State: ${taxi.state}<br>
            Speed: ${formatNumber(taxi.speedKmh, 1)} km/h<br>
            Passengers: ${taxi.passengers}<br>
            Customers served: ${taxi.customersServed}
        `);
        taxiLayer.addLayer(marker);
    });
}

function renderTrips(trips) {
    const tbody = document.getElementById("recent-trips-body");
    if (!tbody) {
        return;
    }
    if (!trips || trips.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="6" class="empty-row">Trips will appear once the simulation starts finishing rides.</td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = trips.map((trip) => `
        <tr>
            <td>${trip.route}</td>
            <td>${trip.taxiId}</td>
            <td>${formatNumber(trip.waitMin, 2)} min</td>
            <td>${formatNumber(trip.tripMin, 2)} min</td>
            <td>${formatNumber(trip.distanceKm, 2)} km</td>
            <td>${formatVnd(trip.fareVnd)}</td>
        </tr>
    `).join("");
}

function updateCharts(series) {
    const safeSeries = (series || []).slice(-MAX_CHART_POINTS);
    opsChart.data.labels = safeSeries.map((point) => point.timeLabel);
    opsChart.data.datasets[0].data = safeSeries.map((point) => point.completedTrips);
    opsChart.data.datasets[1].data = safeSeries.map((point) => point.pendingRequests);
    opsChart.data.datasets[2].data = safeSeries.map((point) => point.utilizationPct);
    opsChart.update("none");

    economyChart.data.labels = safeSeries.map((point) => point.timeLabel);
    economyChart.data.datasets[0].data = safeSeries.map((point) => point.revenueVnd / 1_000_000);
    economyChart.data.datasets[1].data = safeSeries.map((point) => point.co2Kg);
    economyChart.update("none");
}

function renderState(state) {
    const meta = state.meta || {};
    const kpis = state.kpis || {};

    text("scenario-name", meta.scenario || "Hanoi Taxi Ride-Hailing Demo");
    text("district-name", meta.description || meta.district || "");
    text("simulation-clock", state.clock || "Starting...");
    text("run-status", (state.status || "booting").toUpperCase());

    text("kpi-revenue", formatVnd(kpis.revenueVnd || 0));
    text("kpi-co2", `${formatNumber(kpis.co2Kg || 0, 3)} kg`);
    text("kpi-completed", String(kpis.completedTrips || 0));
    text("kpi-wait", `${formatNumber(kpis.avgWaitMin || 0, 2)} min`);
    text("kpi-utilization", `${formatNumber(kpis.utilizationPct || 0, 1)}%`);
    text("kpi-pending", String(kpis.pendingRequests || 0));

    text("detail-distance", `${formatNumber(kpis.fleetDistanceKm || 0, 2)} km`);
    text("detail-active", String(kpis.activeRides || 0));
    text("detail-idle", String(kpis.idleTaxis || 0));
    text("detail-pickup", String(kpis.pickupTaxis || 0));
    text("detail-occupied", String(kpis.occupiedTaxis || 0));
    text("detail-co2-trip", `${formatNumber(kpis.co2PerTripKg || 0, 3)} kg`);
    text("detail-revenue-trip", formatVnd(kpis.revenuePerTripVnd || 0));
    text("detail-trip-time", `${formatNumber(kpis.avgTripMin || 0, 2)} min`);

    const mapMetaKey = JSON.stringify({ bbox: meta.bbox, hotspots: meta.hotspots });
    if (mapMetaKey !== lastMapMetaKey) {
        renderServiceArea(meta.bbox);
        renderHotspots(meta.hotspots || []);
        lastMapMetaKey = mapMetaKey;
    }
    renderTaxis(state.taxis || []);
    renderTrips(state.recentTrips || []);
    updateCharts(state.series || []);
}

async function refresh() {
    try {
        const response = await fetch("/api/state", { cache: "no-store" });
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        const state = await response.json();
        renderState(state);
    } catch (error) {
        text("run-status", "CONNECTION LOST");
    }
}

renderState(initialState);
setInterval(refresh, 1000);
