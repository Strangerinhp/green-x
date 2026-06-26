# Hanoi Taxi Ride-Hailing SUMO Demo

This project builds and runs a SUMO-based taxi ride-hailing simulation for a compact central Hanoi area around Hoan Kiem District. It pairs `sumo-gui` with a local browser dashboard that shows:

- a live map of taxi positions over the district
- revenue collected from completed trips
- cumulative taxi-fleet CO2 emissions
- completed trips, pending requests, utilization, average wait, and other operating figures

The default setup is tuned to be comfortably runnable on a mid-range Windows laptop like yours:

- district: Hoan Kiem core envelope
- taxis: 55
- ride requests: 320
- background traffic trips: 220
- horizon: 2 simulated hours

## What gets generated

After bootstrapping, the scenario files live under `data/generated/hanoi_taxi_hoan_kiem_core/`.

Key outputs during or after a run:

- `data/runtime/.../live_state.json`: latest dashboard state
- `data/outputs/.../ride_log.csv`: per-trip summary
- `data/outputs/.../summary.json`: final KPI summary
- `data/outputs/.../tripinfo.xml`: native SUMO trip output

## 1. Install SUMO on Windows

SUMO is not included in this repo, so install it first.

Recommended path:

1. Download the current Windows build from the official SUMO downloads page.
2. Install it or extract the ZIP.
3. Set `SUMO_HOME` to the SUMO install folder.

Example PowerShell, if SUMO ends up in `C:\Program Files (x86)\Eclipse\Sumo`:

```powershell
setx SUMO_HOME "C:\Program Files (x86)\Eclipse\Sumo"
```

Then close and reopen your terminal.

Notes:

- As of June 25, 2026, the official SUMO downloads page lists version `1.27.1`.
- The dashboard uses OpenStreetMap tiles in the browser, and the scenario bootstrap step downloads OSM road data from Overpass, so internet access is needed.

Official references:

- SUMO install docs: https://sumo.dlr.de/docs/Installing/index.html
- SUMO downloads: https://sumo.dlr.de/docs/Downloads.php
- Taxi support docs: https://sumo.dlr.de/docs/Simulation/Taxi.html
- OSM import docs: https://sumo.dlr.de/docs/Networks/Import/OpenStreetMap.html

## 2. Install Python dependencies

From the repo root:

```powershell
pip install -r requirements.txt
```

## 3. Build the Hanoi scenario

This downloads the map, converts it to a SUMO network, creates taxi trips, background traffic, and the ride-request schedule.

```powershell
python scripts/bootstrap_hanoi_scenario.py
```

Useful variants:

```powershell
python scripts/bootstrap_hanoi_scenario.py --fleet-size 70 --requests 450
python scripts/bootstrap_hanoi_scenario.py --background-trips 300 --duration 9000
python scripts/bootstrap_hanoi_scenario.py --force-download
```

## 4. Run the live simulation

Default run with `sumo-gui` plus the dashboard:

```powershell
python scripts/run_hanoi_taxi_dashboard.py
```

Options:

```powershell
python scripts/run_hanoi_taxi_dashboard.py --port 8060
python scripts/run_hanoi_taxi_dashboard.py --seed 123
python scripts/run_hanoi_taxi_dashboard.py --no-browser
python scripts/run_hanoi_taxi_dashboard.py --nogui
```

Expected behavior:

1. A dashboard opens at `http://127.0.0.1:8050/`.
2. `sumo-gui` launches the Hanoi network.
3. Ride requests are injected during the run and dispatched by a custom TraCI nearest-taxi policy.
4. The dashboard refreshes every second with taxi states and KPIs.


