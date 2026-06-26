from __future__ import annotations

import csv
import json
import os
import sys
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import DEFAULT_DISTRICT, DEFAULT_SIMULATION, FareConfig
from .dashboard import DashboardServer
from .paths import build_scenario_paths
from .scenario import ScenarioBuildError, ensure_sumo_python, resolve_sumo_home, sumo_binary
from .state_store import LiveStateStore


STATE_LABELS = {
    0: "idle",
    1: "pickup",
    2: "occupied",
    3: "shared",
}

STATE_COLORS = {
    "idle": "#1f7a8c",
    "pickup": "#f4a259",
    "occupied": "#d1495b",
    "shared": "#7b2cbf",
}


class SimulationRunError(RuntimeError):
    pass


@dataclass
class RideRecord:
    request_id: str
    origin_name: str
    destination_name: str
    requested_at_s: int
    reservation_id: str | None = None
    depart_pos: float = 5.0
    status: str = "scheduled"
    taxi_id: str | None = None
    assigned_at_s: int | None = None
    pickup_at_s: int | None = None
    dropoff_at_s: int | None = None
    pickup_eta_s: float | None = None
    occupied_distance_start_m: float = 0.0
    occupied_time_start_s: float = 0.0
    trip_distance_m: float = 0.0
    trip_duration_s: float = 0.0
    fare_vnd: float = 0.0

    @property
    def wait_s(self) -> float:
        if self.pickup_at_s is None:
            return 0.0
        return float(self.pickup_at_s - self.requested_at_s)


def run_default_simulation(
    port: int = 8050,
    gui: bool = True,
    open_browser: bool = True,
    seed: int | None = None,
) -> None:
    sumo_home = resolve_sumo_home()
    os.environ["SUMO_HOME"] = str(sumo_home)
    ensure_sumo_python(sumo_home)

    try:
        import traci  # type: ignore
    except ImportError as exc:
        raise SimulationRunError(
            "Could not import traci. Install SUMO and ensure SUMO_HOME points to it."
        ) from exc

    scenario_paths = build_scenario_paths(DEFAULT_DISTRICT.key)
    if not scenario_paths.sumocfg.exists():
        raise SimulationRunError(
            "Scenario files are missing. Run `python scripts/bootstrap_hanoi_scenario.py` first."
        )

    metadata = json.loads(scenario_paths.metadata.read_text(encoding="utf-8"))
    request_schedule = json.loads(scenario_paths.request_schedule.read_text(encoding="utf-8"))
    simulation_config = metadata["simulation"]
    fare_config = FareConfig(**simulation_config["fare"])

    store = LiveStateStore(scenario_paths.live_state)
    dashboard = DashboardServer(store, port=port)
    dashboard.start()

    dashboard_url = f"http://127.0.0.1:{port}/"
    if open_browser:
        webbrowser.open(dashboard_url)

    seed_value = seed if seed is not None else simulation_config["random_seed"]
    sumo_cmd = [
        sumo_binary(sumo_home, "sumo-gui" if gui else "sumo"),
        "-c",
        str(scenario_paths.sumocfg),
        "--start",
        "--quit-on-end",
        "--seed",
        str(seed_value),
        "--device.taxi.dispatch-algorithm",
        "traci",
        "--device.taxi.idle-algorithm",
        simulation_config["taxi_idle_algorithm"],
        "--tripinfo-output",
        str(scenario_paths.tripinfo_xml),
        "--device.emissions.probability",
        "1",
        "--fcd-output",
        str(scenario_paths.fcd_xml),
        "--fcd-output.geo",
        "true",
    ]

    request_cursor = 0
    rides: dict[str, RideRecord] = {}
    rides_by_reservation: dict[str, str] = {}
    pending_reservations: dict[str, Any] = {}
    ride_log_rows: list[dict[str, Any]] = []
    last_vehicle_distance: dict[str, float] = {}
    series: list[dict[str, Any]] = []
    total_co2_kg = 0.0
    total_distance_km = 0.0
    completed_count = 0
    revenue_vnd = 0.0
    taxi_ids: set[str] = set()

    try:
        traci.start(sumo_cmd)
        state = build_initial_state(metadata, dashboard_url)
        store.replace(state)

        while True:
            traci.simulationStep()
            sim_time_s = int(traci.simulation.getTime())
            inject_requests(sim_time_s, request_schedule, rides, traci, request_cursor)
            while request_cursor < len(request_schedule) and request_schedule[request_cursor]["depart_s"] <= sim_time_s:
                request_cursor += 1

            for reservation in traci.person.getTaxiReservations(1):
                if not reservation.persons:
                    continue
                person_id = reservation.persons[0]
                ride = rides.get(person_id)
                if ride is None:
                    continue
                ride.status = "waiting"
                ride.reservation_id = reservation.id
                rides_by_reservation[reservation.id] = person_id
                pending_reservations[reservation.id] = reservation

            dispatch_reservations(traci, pending_reservations, rides, rides_by_reservation, sim_time_s)
            completed_now, revenue_now = update_rides(traci, rides, fare_config, sim_time_s, ride_log_rows)
            completed_count += completed_now
            revenue_vnd += revenue_now

            fleet_ids = get_all_taxi_fleet(traci)
            taxi_ids.update(fleet_ids)
            total_co2_kg += step_co2_kg(traci, fleet_ids)
            total_distance_km += step_distance_km(traci, fleet_ids, last_vehicle_distance)

            snapshot = build_snapshot(
                metadata=metadata,
                rides=rides,
                fleet_ids=fleet_ids,
                traci=traci,
                revenue_vnd=revenue_vnd,
                total_co2_kg=total_co2_kg,
                total_distance_km=total_distance_km,
                completed_count=completed_count,
                sim_time_s=sim_time_s,
                series=series,
                taxi_count=max(len(taxi_ids), metadata["simulation"]["fleet_size"]),
            )
            store.replace(snapshot)

            no_more_objects = (
                request_cursor >= len(request_schedule)
                and traci.simulation.getMinExpectedNumber() == 0
                and all(ride.status == "completed" for ride in rides.values())
            )
            if no_more_objects:
                break

        final_state = build_snapshot(
            metadata=metadata,
            rides=rides,
            fleet_ids=get_all_taxi_fleet(traci),
            traci=traci,
            revenue_vnd=revenue_vnd,
            total_co2_kg=total_co2_kg,
            total_distance_km=total_distance_km,
            completed_count=completed_count,
            sim_time_s=int(traci.simulation.getTime()),
            series=series,
            taxi_count=max(len(taxi_ids), metadata["simulation"]["fleet_size"]),
            status="finished",
        )
        store.replace(final_state)
        write_outputs(
            scenario_paths=scenario_paths,
            rides=rides,
            ride_log_rows=ride_log_rows,
            final_state=final_state,
        )
        time.sleep(1.5)
    except Exception as exc:
        error_state = build_initial_state(metadata, dashboard_url)
        error_state["status"] = "error"
        error_state["error"] = str(exc)
        store.replace(error_state)
        raise
    finally:
        try:
            traci.close()
        except Exception:
            pass
        dashboard.shutdown()


def inject_requests(sim_time_s: int, request_schedule: list[dict[str, Any]], rides: dict[str, RideRecord], traci: Any, request_cursor: int) -> None:
    cursor = request_cursor
    while cursor < len(request_schedule) and request_schedule[cursor]["depart_s"] <= sim_time_s:
        request = request_schedule[cursor]
        ride = RideRecord(
            request_id=request["request_id"],
            origin_name=request["origin_name"],
            destination_name=request["destination_name"],
            requested_at_s=request["depart_s"],
            depart_pos=float(request["depart_pos"]),
        )
        rides[ride.request_id] = ride
        traci.person.add(
            ride.request_id,
            request["from_edge"],
            ride.depart_pos,
            depart=sim_time_s,
            typeID="DEFAULT_PEDTYPE",
        )
        traci.person.appendDrivingStage(
            ride.request_id,
            request["to_edge"],
            "taxi",
        )
        cursor += 1


def dispatch_reservations(
    traci: Any,
    pending_reservations: dict[str, Any],
    rides: dict[str, RideRecord],
    rides_by_reservation: dict[str, str],
    sim_time_s: int,
) -> None:
    idle_taxis = list(traci.vehicle.getTaxiFleet(0))
    for reservation_id in list(pending_reservations):
        if not idle_taxis:
            break
        reservation = pending_reservations[reservation_id]
        assigned_taxi: str | None = None
        assigned_eta_s: float | None = None
        for taxi_id, pickup_eta_s in rank_taxis_by_pickup_eta(traci, idle_taxis, reservation.fromEdge):
            try:
                traci.vehicle.dispatchTaxi(taxi_id, [reservation_id])
            except Exception:
                idle_taxis.remove(taxi_id)
                continue
            assigned_taxi = taxi_id
            assigned_eta_s = pickup_eta_s
            idle_taxis.remove(taxi_id)
            break

        if not assigned_taxi:
            continue

        ride_id = rides_by_reservation.get(reservation_id)
        if ride_id and ride_id in rides:
            rides[ride_id].taxi_id = assigned_taxi
            rides[ride_id].assigned_at_s = sim_time_s
            rides[ride_id].pickup_eta_s = assigned_eta_s
            rides[ride_id].status = "assigned"
        pending_reservations.pop(reservation_id, None)


def choose_best_taxi(traci: Any, taxi_ids: list[str], request_edge: str) -> tuple[str | None, float | None]:
    best_taxi: str | None = None
    best_eta: float | None = None
    for taxi_id in taxi_ids:
        try:
            road_id = traci.vehicle.getRoadID(taxi_id)
            if not road_id or road_id.startswith(":"):
                continue
            route = traci.simulation.findRoute(road_id, request_edge, vType="taxiType")
            eta = float(route.travelTime)
        except Exception:
            continue
        if best_eta is None or eta < best_eta:
            best_taxi = taxi_id
            best_eta = eta
    return best_taxi, best_eta


def rank_taxis_by_pickup_eta(traci: Any, taxi_ids: list[str], request_edge: str) -> list[tuple[str, float]]:
    candidates: list[tuple[str, float]] = []
    for taxi_id in taxi_ids:
        try:
            road_id = traci.vehicle.getRoadID(taxi_id)
            if not road_id or road_id.startswith(":"):
                continue
            route = traci.simulation.findRoute(road_id, request_edge, vType="taxiType")
            eta = float(route.travelTime)
        except Exception:
            continue
        if eta >= 0:
            candidates.append((taxi_id, eta))
    return sorted(candidates, key=lambda item: item[1])


def get_all_taxi_fleet(traci: Any) -> list[str]:
    taxi_ids: list[str] = []
    seen: set[str] = set()
    for taxi_state in (0, 1, 2):
        for taxi_id in traci.vehicle.getTaxiFleet(taxi_state):
            if taxi_id not in seen:
                seen.add(taxi_id)
                taxi_ids.append(taxi_id)
    return taxi_ids


def update_rides(
    traci: Any,
    rides: dict[str, RideRecord],
    fare_config: FareConfig,
    sim_time_s: int,
    ride_log_rows: list[dict[str, Any]],
) -> tuple[int, float]:
    completed_now = 0
    revenue_now = 0.0
    arrived_people = set(traci.simulation.getArrivedPersonIDList())

    for ride in rides.values():
        if ride.status in {"completed", "scheduled"}:
            continue

        if ride.request_id in arrived_people and ride.dropoff_at_s is None:
            ride.dropoff_at_s = sim_time_s
            ride.status = "completed"
            if ride.taxi_id:
                occupied_distance = float(
                    traci.vehicle.getParameter(ride.taxi_id, "device.taxi.occupiedDistance") or 0.0
                )
                occupied_time = float(
                    traci.vehicle.getParameter(ride.taxi_id, "device.taxi.occupiedTime") or 0.0
                )
                ride.trip_distance_m = max(0.0, occupied_distance - ride.occupied_distance_start_m)
                ride.trip_duration_s = max(0.0, occupied_time - ride.occupied_time_start_s)
            ride.fare_vnd = fare_config.estimate(ride.trip_distance_m, ride.trip_duration_s)
            completed_now += 1
            revenue_now += ride.fare_vnd
            ride_log_rows.append(
                {
                    "request_id": ride.request_id,
                    "origin_name": ride.origin_name,
                    "destination_name": ride.destination_name,
                    "taxi_id": ride.taxi_id or "",
                    "requested_at_s": ride.requested_at_s,
                    "assigned_at_s": ride.assigned_at_s or "",
                    "pickup_at_s": ride.pickup_at_s or "",
                    "dropoff_at_s": ride.dropoff_at_s or "",
                    "wait_s": round(ride.wait_s, 1),
                    "trip_duration_s": round(ride.trip_duration_s, 1),
                    "trip_distance_km": round(ride.trip_distance_m / 1000.0, 3),
                    "fare_vnd": int(ride.fare_vnd),
                }
            )
            continue

        try:
            current_vehicle = traci.person.getVehicle(ride.request_id)
        except Exception:
            current_vehicle = ""

        if current_vehicle and ride.pickup_at_s is None:
            ride.pickup_at_s = sim_time_s
            ride.status = "occupied"
            ride.taxi_id = current_vehicle
            ride.occupied_distance_start_m = float(
                traci.vehicle.getParameter(current_vehicle, "device.taxi.occupiedDistance") or 0.0
            )
            ride.occupied_time_start_s = float(
                traci.vehicle.getParameter(current_vehicle, "device.taxi.occupiedTime") or 0.0
            )

    return completed_now, revenue_now


def step_co2_kg(traci: Any, fleet_ids: list[str]) -> float:
    total_mg = 0.0
    for taxi_id in fleet_ids:
        try:
            total_mg += float(traci.vehicle.getCO2Emission(taxi_id))
        except Exception:
            continue
    return total_mg / 1_000_000.0


def step_distance_km(traci: Any, fleet_ids: list[str], last_vehicle_distance: dict[str, float]) -> float:
    delta_km = 0.0
    for taxi_id in fleet_ids:
        try:
            current_distance = float(traci.vehicle.getDistance(taxi_id))
        except Exception:
            continue
        previous = last_vehicle_distance.get(taxi_id, current_distance)
        delta_km += max(0.0, current_distance - previous) / 1000.0
        last_vehicle_distance[taxi_id] = current_distance
    return delta_km


def build_initial_state(metadata: dict[str, Any], dashboard_url: str) -> dict[str, Any]:
    return {
        "status": "running",
        "meta": {
            "scenario": metadata["simulation"]["scenario_name"],
            "district": metadata["district"]["name"],
            "description": metadata["district"]["description"],
            "center": metadata["district"]["center"],
            "bbox": metadata["district"]["bbox"],
            "hotspots": metadata["district"]["hotspots"],
            "dashboardUrl": dashboard_url,
        },
        "kpis": {},
        "series": [],
        "taxis": [],
        "recentTrips": [],
        "generatedAt": datetime.utcnow().isoformat(),
    }


def build_snapshot(
    metadata: dict[str, Any],
    rides: dict[str, RideRecord],
    fleet_ids: list[str],
    traci: Any,
    revenue_vnd: float,
    total_co2_kg: float,
    total_distance_km: float,
    completed_count: int,
    sim_time_s: int,
    series: list[dict[str, Any]],
    taxi_count: int,
    status: str = "running",
) -> dict[str, Any]:
    taxis = collect_taxis(traci, fleet_ids)
    now_clock = sim_clock(metadata["simulation"]["local_start_hour"], sim_time_s)
    wait_samples = [ride.wait_s for ride in rides.values() if ride.status == "completed" and ride.pickup_at_s]
    trip_samples = [ride.trip_duration_s for ride in rides.values() if ride.status == "completed" and ride.dropoff_at_s]
    idle_taxis = sum(1 for taxi in taxis if taxi["state"] == "idle")
    occupied_taxis = sum(1 for taxi in taxis if taxi["state"] in {"occupied", "shared"})
    pickup_taxis = sum(1 for taxi in taxis if taxi["state"] == "pickup")
    pending_requests = sum(1 for ride in rides.values() if ride.status in {"waiting", "assigned"})
    active_rides = sum(1 for ride in rides.values() if ride.status == "occupied")
    avg_wait_min = (sum(wait_samples) / len(wait_samples) / 60.0) if wait_samples else 0.0
    avg_trip_min = (sum(trip_samples) / len(trip_samples) / 60.0) if trip_samples else 0.0
    utilization_pct = (occupied_taxis / taxi_count * 100.0) if taxi_count else 0.0

    if sim_time_s == 0 or sim_time_s % 15 == 0:
        point = {
            "timeLabel": format_duration(sim_time_s),
            "revenueVnd": int(revenue_vnd),
            "co2Kg": round(total_co2_kg, 3),
            "pendingRequests": pending_requests,
            "completedTrips": completed_count,
            "utilizationPct": round(utilization_pct, 1),
        }
        if not series or series[-1]["timeLabel"] != point["timeLabel"]:
            series.append(point)
            if len(series) > 480:
                del series[:-480]

    recent_trips = sorted(
        [ride for ride in rides.values() if ride.status == "completed" and ride.dropoff_at_s is not None],
        key=lambda item: item.dropoff_at_s or 0,
        reverse=True,
    )[:8]

    return {
        "status": status,
        "meta": {
            "scenario": metadata["simulation"]["scenario_name"],
            "district": metadata["district"]["name"],
            "description": metadata["district"]["description"],
            "center": metadata["district"]["center"],
            "bbox": metadata["district"]["bbox"],
            "hotspots": metadata["district"]["hotspots"],
        },
        "clock": now_clock,
        "simTimeLabel": format_duration(sim_time_s),
        "generatedAt": datetime.utcnow().isoformat(),
        "kpis": {
            "revenueVnd": int(revenue_vnd),
            "co2Kg": round(total_co2_kg, 3),
            "fleetDistanceKm": round(total_distance_km, 2),
            "completedTrips": completed_count,
            "pendingRequests": pending_requests,
            "activeRides": active_rides,
            "idleTaxis": idle_taxis,
            "pickupTaxis": pickup_taxis,
            "occupiedTaxis": occupied_taxis,
            "avgWaitMin": round(avg_wait_min, 2),
            "avgTripMin": round(avg_trip_min, 2),
            "utilizationPct": round(utilization_pct, 1),
            "co2PerTripKg": round(total_co2_kg / completed_count, 3) if completed_count else 0.0,
            "revenuePerTripVnd": int(revenue_vnd / completed_count) if completed_count else 0,
        },
        "series": list(series),
        "taxis": taxis,
        "recentTrips": [
            {
                "requestId": ride.request_id,
                "route": f"{ride.origin_name} -> {ride.destination_name}",
                "taxiId": ride.taxi_id or "",
                "waitMin": round(ride.wait_s / 60.0, 2),
                "tripMin": round(ride.trip_duration_s / 60.0, 2),
                "distanceKm": round(ride.trip_distance_m / 1000.0, 2),
                "fareVnd": int(ride.fare_vnd),
            }
            for ride in recent_trips
        ],
    }


def collect_taxis(traci: Any, fleet_ids: list[str]) -> list[dict[str, Any]]:
    taxis: list[dict[str, Any]] = []
    for taxi_id in fleet_ids:
        try:
            state_value = int(float(traci.vehicle.getParameter(taxi_id, "device.taxi.state") or 0))
            state = STATE_LABELS.get(state_value, "idle")
            x, y = traci.vehicle.getPosition(taxi_id)
            lon, lat = traci.simulation.convertGeo(x, y)
            speed_kmh = float(traci.vehicle.getSpeed(taxi_id)) * 3.6
            passengers = int(traci.vehicle.getPersonNumber(taxi_id))
            customers_served = int(float(traci.vehicle.getParameter(taxi_id, "device.taxi.customers") or 0))
        except Exception:
            continue

        taxis.append(
            {
                "id": taxi_id,
                "state": state,
                "color": STATE_COLORS[state],
                "lat": lat,
                "lon": lon,
                "speedKmh": round(speed_kmh, 1),
                "passengers": passengers,
                "customersServed": customers_served,
            }
        )
    return taxis


def write_outputs(
    scenario_paths: Any,
    rides: dict[str, RideRecord],
    ride_log_rows: list[dict[str, Any]],
    final_state: dict[str, Any],
) -> None:
    scenario_paths.outputs_dir.mkdir(parents=True, exist_ok=True)

    with scenario_paths.ride_log_csv.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = [
            "request_id",
            "origin_name",
            "destination_name",
            "taxi_id",
            "requested_at_s",
            "assigned_at_s",
            "pickup_at_s",
            "dropoff_at_s",
            "wait_s",
            "trip_duration_s",
            "trip_distance_km",
            "fare_vnd",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(ride_log_rows)

    summary = {
        "status": final_state["status"],
        "kpis": final_state["kpis"],
        "completedTrips": len([ride for ride in rides.values() if ride.status == "completed"]),
        "generatedAt": final_state["generatedAt"],
        "files": {
            "rideLogCsv": str(scenario_paths.ride_log_csv),
            "tripinfoXml": str(scenario_paths.tripinfo_xml),
            "fcdXml": str(scenario_paths.fcd_xml),
            "runtimeStateJson": str(scenario_paths.live_state),
        },
    }
    scenario_paths.summary_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def sim_clock(start_hour: int, sim_time_s: int) -> str:
    base = datetime(2026, 6, 25, start_hour, 0, 0)
    return (base + timedelta(seconds=sim_time_s)).strftime("%A, %B %d, %Y, %I:%M:%S %p")


def format_duration(seconds: int) -> str:
    return str(timedelta(seconds=seconds))
