from __future__ import annotations

import json
import math
import os
import random
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .config import DEFAULT_DISTRICT, DEFAULT_SIMULATION, DistrictConfig, SimulationConfig
from .paths import ScenarioPaths, build_scenario_paths


OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
)
OVERPASS_HEADERS = {
    "Content-Type": "text/plain; charset=utf-8",
    "Accept": "application/osm3s+xml, application/xml, text/xml;q=0.9, */*;q=0.1",
    "User-Agent": "green-x-sumo-bootstrap/1.0",
}


class ScenarioBuildError(RuntimeError):
    pass


def resolve_sumo_home(explicit: str | None = None) -> Path:
    candidates: list[Path] = []

    if explicit:
        candidates.append(Path(explicit))

    env_value = Path(os.environ["SUMO_HOME"]) if "SUMO_HOME" in os.environ else None
    if env_value:
        candidates.append(env_value)

    for binary_name in ("sumo-gui", "sumo-gui.exe", "sumo"):
        resolved = shutil.which(binary_name)
        if resolved:
            path = Path(resolved).resolve()
            candidates.append(path.parent.parent)

    candidates.extend(
        [
            Path(r"C:\Program Files (x86)\Eclipse\Sumo"),
            Path(r"C:\Program Files\Eclipse\Sumo"),
            Path(r"C:\sumo"),
        ]
    )

    for candidate in candidates:
        bin_dir = candidate / "bin"
        tools_dir = candidate / "tools"
        if bin_dir.exists() and tools_dir.exists():
            return candidate

    raise ScenarioBuildError(
        "SUMO was not found. Install SUMO, then set SUMO_HOME to the install folder."
    )


def ensure_sumo_python(sumo_home: Path) -> None:
    tools_dir = sumo_home / "tools"
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))


def sumo_binary(sumo_home: Path, name: str) -> str:
    suffix = ".exe" if sys.platform.startswith("win") and not name.endswith(".exe") else ""
    candidate = sumo_home / "bin" / f"{name}{suffix}"
    if not candidate.exists():
        raise ScenarioBuildError(f"Expected SUMO binary not found: {candidate}")
    return str(candidate)


def _run(args: list[str], cwd: Path | None = None) -> None:
    completed = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise ScenarioBuildError(
            "Command failed:\n"
            f"Return code: {completed.returncode}\n"
            f"{' '.join(args)}\n\n"
            f"STDOUT:\n{completed.stdout}\n\n"
            f"STDERR:\n{completed.stderr}"
        )


def _import_requests() -> Any:
    try:
        import requests  # type: ignore
    except ImportError as exc:
        raise ScenarioBuildError(
            "Missing Python dependency 'requests'. Run `pip install -r requirements.txt` first."
        ) from exc
    return requests


def _load_sumolib(sumo_home: Path) -> Any:
    ensure_sumo_python(sumo_home)
    try:
        import sumolib  # type: ignore
    except ImportError as exc:
        raise ScenarioBuildError(
            f"Could not import sumolib from {sumo_home / 'tools'}. Check your SUMO install."
        ) from exc
    return sumolib


def build_default_scenario(
    sumo_home: str | None = None,
    fleet_size: int | None = None,
    request_count: int | None = None,
    background_trip_count: int | None = None,
    duration_s: int | None = None,
    force_download: bool = False,
) -> ScenarioPaths:
    district = DEFAULT_DISTRICT
    config = DEFAULT_SIMULATION

    if fleet_size is not None:
        config = replace(config, fleet_size=fleet_size)
    if request_count is not None:
        config = replace(config, request_count=request_count)
    if background_trip_count is not None:
        config = replace(config, background_trip_count=background_trip_count)
    if duration_s is not None:
        config = replace(config, duration_s=duration_s)

    return bootstrap_scenario(
        district=district,
        simulation=config,
        sumo_home=sumo_home,
        force_download=force_download,
    )


def bootstrap_scenario(
    district: DistrictConfig,
    simulation: SimulationConfig,
    sumo_home: str | None = None,
    force_download: bool = False,
) -> ScenarioPaths:
    resolved_sumo_home = resolve_sumo_home(sumo_home)
    os.environ["SUMO_HOME"] = str(resolved_sumo_home)
    paths = build_scenario_paths(district.key)
    sumolib = _load_sumolib(resolved_sumo_home)

    if force_download or not paths.osm_map.exists():
        download_osm_map(district, paths.osm_map)

    build_network(resolved_sumo_home, paths)
    build_polygons(resolved_sumo_home, paths)

    network = sumolib.net.readNet(str(paths.network), withInternal=False)
    drivable_edges = largest_reachable_edge_component(collect_drivable_edges(network))
    if len(drivable_edges) < 25:
        raise ScenarioBuildError(
            "The imported network is too small for the taxi scenario. Try a wider district bbox."
        )

    hotspot_edges = build_hotspot_edge_index(network, drivable_edges, district)
    write_taxi_trips(paths, drivable_edges, hotspot_edges, simulation)
    write_background_trips(paths, drivable_edges, hotspot_edges, simulation)
    write_taxi_routes(paths, drivable_edges, hotspot_edges, simulation)
    route_trip_file(
        resolved_sumo_home,
        paths.network,
        paths.background_trips,
        paths.background_routes,
    )
    write_request_schedule(paths, drivable_edges, hotspot_edges, simulation)
    write_gui_settings(paths)
    write_sumocfg(paths, simulation)
    write_metadata(paths, district, simulation)

    return paths


def download_osm_map(district: DistrictConfig, target_path: Path) -> None:
    requests = _import_requests()
    bbox = district.bbox
    query = f"""
[out:xml][timeout:180];
(
  way["highway"]({bbox.south},{bbox.west},{bbox.north},{bbox.east});
);
(._;>;);
out body;
""".strip()

    attempt_errors: list[str] = []
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            response = requests.post(
                endpoint,
                data=query.encode("utf-8"),
                headers=OVERPASS_HEADERS,
                timeout=180,
            )
            response.raise_for_status()
            if "<osm" not in response.text[:500]:
                snippet = response.text[:300].replace("\n", " ").strip()
                raise ScenarioBuildError(
                    f"Overpass returned unexpected content from {endpoint}: {snippet}"
                )
            target_path.write_text(response.text, encoding="utf-8")
            return
        except Exception as exc:  # pragma: no cover - network failure path
            attempt_errors.append(f"{endpoint} -> {exc}")

    raise ScenarioBuildError(
        "Failed to download OSM data from Overpass. Attempts:\n"
        + "\n".join(attempt_errors)
    )


def build_network(sumo_home: Path, paths: ScenarioPaths) -> None:
    type_dir = sumo_home / "data" / "typemap"
    type_files = ",".join(
        [
            str(type_dir / "osmNetconvert.typ.xml"),
            str(type_dir / "osmNetconvertUrbanDe.typ.xml"),
            str(type_dir / "osmNetconvertPedestrians.typ.xml"),
        ]
    )
    args = [
        sumo_binary(sumo_home, "netconvert"),
        "--osm-files",
        str(paths.osm_map),
        "-o",
        str(paths.network),
        "--type-files",
        type_files,
        "--geometry.remove",
        "--ramps.guess",
        "--junctions.join",
        "--tls.guess-signals",
        "--tls.discard-simple",
        "--tls.join",
        "--tls.default-type",
        "actuated",
        "--osm.sidewalks",
        "--osm.turn-lanes",
    ]
    _run(args)


def build_polygons(sumo_home: Path, paths: ScenarioPaths) -> None:
    type_file = sumo_home / "data" / "typemap" / "osmPolyconvert.typ.xml"
    if not type_file.exists():
        return

    args = [
        sumo_binary(sumo_home, "polyconvert"),
        "--osm-files",
        str(paths.osm_map),
        "--net-file",
        str(paths.network),
        "--type-file",
        str(type_file),
        "-o",
        str(paths.polygons),
    ]
    try:
        _run(args)
    except ScenarioBuildError:
        # The taxi system can run without polygon overlays.
        pass


def collect_drivable_edges(network: Any) -> list[Any]:
    edges = []
    for edge in network.getEdges():
        if edge.getID().startswith(":"):
            continue
        if not edge.allows("passenger"):
            continue
        if edge.getLength() < 40:
            continue
        edges.append(edge)
    return edges


def largest_reachable_edge_component(edges: list[Any]) -> list[Any]:
    edge_by_id = {edge.getID(): edge for edge in edges}
    graph: dict[str, set[str]] = {edge_id: set() for edge_id in edge_by_id}
    reverse_graph: dict[str, set[str]] = {edge_id: set() for edge_id in edge_by_id}

    for edge in edges:
        edge_id = edge.getID()
        for next_edge in edge.getOutgoing():
            next_id = next_edge.getID()
            if next_id in edge_by_id:
                graph[edge_id].add(next_id)
                reverse_graph[next_id].add(edge_id)

    components = strongly_connected_components(graph, reverse_graph)
    if not components:
        return edges

    largest = max(components, key=len)
    return [edge_by_id[edge_id] for edge_id in largest]


def strongly_connected_components(
    graph: dict[str, set[str]],
    reverse_graph: dict[str, set[str]],
) -> list[set[str]]:
    visited: set[str] = set()
    finish_order: list[str] = []

    for node in graph:
        if node in visited:
            continue
        stack: list[tuple[str, bool]] = [(node, False)]
        while stack:
            current, expanded = stack.pop()
            if expanded:
                finish_order.append(current)
                continue
            if current in visited:
                continue
            visited.add(current)
            stack.append((current, True))
            for neighbor in graph[current]:
                if neighbor not in visited:
                    stack.append((neighbor, False))

    visited.clear()
    components: list[set[str]] = []
    for node in reversed(finish_order):
        if node in visited:
            continue
        component: set[str] = set()
        stack = [node]
        visited.add(node)
        while stack:
            current = stack.pop()
            component.add(current)
            for neighbor in reverse_graph[current]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    stack.append(neighbor)
        components.append(component)

    return components


def build_hotspot_edge_index(network: Any, edges: list[Any], district: DistrictConfig) -> dict[str, list[str]]:
    edge_midpoints = {
        edge.getID(): midpoint(edge.getShape()) for edge in edges if edge.getShape()
    }
    hotspot_index: dict[str, list[str]] = {}
    for hotspot in district.hotspots:
        x, y = convert_hotspot_to_network_xy(network, hotspot.lon, hotspot.lat)
        ranked = sorted(
            edge_midpoints.items(),
            key=lambda item: squared_distance(item[1], (x, y)),
        )
        hotspot_index[hotspot.name] = [edge_id for edge_id, _ in ranked[:12]]
    return hotspot_index


def write_taxi_trips(
    paths: ScenarioPaths,
    drivable_edges: list[Any],
    hotspot_edges: dict[str, list[str]],
    simulation: SimulationConfig,
) -> None:
    rng = random.Random(simulation.random_seed)
    root = ET.Element("routes")
    ET.SubElement(
        root,
        "vType",
        {
            "id": "taxiType",
            "vClass": "taxi",
            "guiShape": "passenger/sedan",
            "color": "0.98,0.83,0.16",
            "personCapacity": "4",
            "maxSpeed": "13.9",
            "sigma": "0.5",
            "emissionClass": "HBEFA3/PC_G_EU4",
        },
    )
    vehicle_type = root.findall("vType")[0]
    ET.SubElement(vehicle_type, "param", {"key": "has.taxi.device", "value": "true"})
    ET.SubElement(
        vehicle_type,
        "param",
        {
            "key": "device.taxi.pickUpDuration",
            "value": str(simulation.taxi_pickup_duration_s),
        },
    )
    ET.SubElement(
        vehicle_type,
        "param",
        {
            "key": "device.taxi.dropOffDuration",
            "value": str(simulation.taxi_dropoff_duration_s),
        },
    )
    ET.SubElement(vehicle_type, "param", {"key": "device.taxi.parking", "value": "false"})

    for taxi_index in range(simulation.fleet_size):
        from_edge = choose_start_edge(rng, drivable_edges, hotspot_edges)
        to_edge = choose_trip_edge(rng, drivable_edges, hotspot_edges, exclude=from_edge)
        ET.SubElement(
            root,
            "trip",
            {
                "id": f"taxi_{taxi_index:03d}",
                "type": "taxiType",
                "from": from_edge,
                "to": to_edge,
                "depart": str(rng.randint(0, 90)),
                "departLane": "best",
                "departPos": "random_free",
                "departSpeed": "max",
                "line": "taxi",
            },
        )

    indent_xml(root)
    ET.ElementTree(root).write(paths.taxi_trips, encoding="utf-8", xml_declaration=True)


def write_taxi_routes(
    paths: ScenarioPaths,
    drivable_edges: list[Any],
    hotspot_edges: dict[str, list[str]],
    simulation: SimulationConfig,
) -> None:
    rng = random.Random(simulation.random_seed)
    root = ET.Element("routes")
    taxi_type = ET.SubElement(
        root,
        "vType",
        {
            "id": "taxiType",
            "vClass": "taxi",
            "guiShape": "passenger/sedan",
            "color": "0.98,0.83,0.16",
            "personCapacity": "4",
            "maxSpeed": "13.9",
            "sigma": "0.5",
            "emissionClass": "HBEFA3/PC_G_EU4",
        },
    )
    ET.SubElement(taxi_type, "param", {"key": "has.taxi.device", "value": "true"})
    ET.SubElement(
        taxi_type,
        "param",
        {
            "key": "device.taxi.pickUpDuration",
            "value": str(simulation.taxi_pickup_duration_s),
        },
    )
    ET.SubElement(
        taxi_type,
        "param",
        {
            "key": "device.taxi.dropOffDuration",
            "value": str(simulation.taxi_dropoff_duration_s),
        },
    )
    ET.SubElement(taxi_type, "param", {"key": "device.taxi.parking", "value": "false"})

    for taxi_index in range(simulation.fleet_size):
        start_edge = choose_start_edge(rng, drivable_edges, hotspot_edges)
        vehicle = ET.SubElement(
            root,
            "vehicle",
            {
                "id": f"taxi_{taxi_index:03d}",
                "type": "taxiType",
                "depart": str(rng.randint(0, 30)),
                "departLane": "best",
                "departPos": "random_free",
                "departSpeed": "max",
                "line": "taxi",
            },
        )
        ET.SubElement(vehicle, "route", {"edges": start_edge})

    indent_xml(root)
    ET.ElementTree(root).write(paths.taxi_routes, encoding="utf-8", xml_declaration=True)


def write_background_trips(
    paths: ScenarioPaths,
    drivable_edges: list[Any],
    hotspot_edges: dict[str, list[str]],
    simulation: SimulationConfig,
) -> None:
    rng = random.Random(simulation.random_seed + 7)
    root = ET.Element("routes")
    ET.SubElement(
        root,
        "vType",
        {
            "id": "backgroundCar",
            "vClass": "passenger",
            "guiShape": "passenger",
            "color": "0.45,0.49,0.57",
            "sigma": "0.7",
            "maxSpeed": "12.5",
            "emissionClass": "HBEFA3/PC_G_EU4",
        },
    )

    for trip_index in range(simulation.background_trip_count):
        from_edge = choose_trip_edge(rng, drivable_edges, hotspot_edges)
        to_edge = choose_trip_edge(rng, drivable_edges, hotspot_edges, exclude=from_edge)
        depart = int(simulation.duration_s * rng.betavariate(1.4, 1.8))
        ET.SubElement(
            root,
            "trip",
            {
                "id": f"bg_{trip_index:04d}",
                "type": "backgroundCar",
                "from": from_edge,
                "to": to_edge,
                "depart": str(depart),
                "departLane": "best",
                "departPos": "random_free",
                "departSpeed": "max",
            },
        )

    indent_xml(root)
    ET.ElementTree(root).write(
        paths.background_trips,
        encoding="utf-8",
        xml_declaration=True,
    )


def route_trip_file(sumo_home: Path, network_path: Path, trip_path: Path, route_path: Path) -> None:
    args = [
        sumo_binary(sumo_home, "duarouter"),
        "-n",
        str(network_path),
        "-r",
        str(trip_path),
        "-o",
        str(route_path),
        "--ignore-errors",
        "true",
    ]
    _run(args)


def write_request_schedule(
    paths: ScenarioPaths,
    drivable_edges: list[Any],
    hotspot_edges: dict[str, list[str]],
    simulation: SimulationConfig,
) -> None:
    rng = random.Random(simulation.random_seed + 17)
    records: list[dict[str, Any]] = []
    for request_index in range(simulation.request_count):
        depart_s = int(simulation.duration_s * rng.betavariate(1.7, 1.9))
        origin_name, from_edge = choose_named_request_edge(rng, drivable_edges, hotspot_edges)
        destination_name, to_edge = choose_named_request_edge(
            rng,
            drivable_edges,
            hotspot_edges,
            exclude=from_edge,
        )
        records.append(
            {
                "request_id": f"ride_{request_index:04d}",
                "depart_s": depart_s,
                "from_edge": from_edge,
                "to_edge": to_edge,
                "origin_name": origin_name,
                "destination_name": destination_name,
                "depart_pos": round(rng.uniform(10.0, 35.0), 1),
            }
        )

    records.sort(key=lambda item: item["depart_s"])
    paths.request_schedule.write_text(
        json.dumps(records, indent=2),
        encoding="utf-8",
    )


def write_gui_settings(paths: ScenarioPaths) -> None:
    content = """<?xml version="1.0" encoding="UTF-8"?>
<viewsettings>
    <scheme name="real world"/>
    <delay value="75"/>
    <viewport x="0" y="0" zoom="5500"/>
    <background backgroundColor="0.97,0.98,1.00" showGrid="0"/>
</viewsettings>
"""
    paths.gui_settings.write_text(content, encoding="utf-8")


def write_sumocfg(paths: ScenarioPaths, simulation: SimulationConfig) -> None:
    additional_files = [paths.polygons.name] if paths.polygons.exists() else []
    additional_value = ",".join(additional_files)
    tree = ET.Element("configuration")

    input_element = ET.SubElement(tree, "input")
    ET.SubElement(input_element, "net-file", {"value": paths.network.name})
    ET.SubElement(
        input_element,
        "route-files",
        {"value": f"{paths.taxi_routes.name},{paths.background_routes.name}"},
    )
    if additional_value:
        ET.SubElement(input_element, "additional-files", {"value": additional_value})

    time_element = ET.SubElement(tree, "time")
    ET.SubElement(time_element, "begin", {"value": "0"})
    ET.SubElement(time_element, "end", {"value": str(simulation.duration_s)})
    ET.SubElement(time_element, "step-length", {"value": str(simulation.step_length_s)})

    processing_element = ET.SubElement(tree, "processing")
    ET.SubElement(processing_element, "time-to-teleport", {"value": "-1"})
    ET.SubElement(processing_element, "collision.action", {"value": "warn"})

    report_element = ET.SubElement(tree, "report")
    ET.SubElement(report_element, "verbose", {"value": "false"})
    ET.SubElement(report_element, "duration-log.statistics", {"value": "true"})

    gui_only = ET.SubElement(tree, "gui_only")
    ET.SubElement(gui_only, "gui-settings-file", {"value": paths.gui_settings.name})

    indent_xml(tree)
    ET.ElementTree(tree).write(paths.sumocfg, encoding="utf-8", xml_declaration=True)


def write_metadata(paths: ScenarioPaths, district: DistrictConfig, simulation: SimulationConfig) -> None:
    payload = {
        "district": district.as_dict(),
        "simulation": simulation.as_dict(),
        "paths": {
            "sumocfg": str(paths.sumocfg),
            "network": str(paths.network),
            "runtimeState": str(paths.live_state),
            "rideLogCsv": str(paths.ride_log_csv),
            "summaryJson": str(paths.summary_json),
            "tripinfoXml": str(paths.tripinfo_xml),
        },
    }
    paths.metadata.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def choose_start_edge(rng: random.Random, drivable_edges: list[Any], hotspot_edges: dict[str, list[str]]) -> str:
    if rng.random() < 0.7:
        hotspot_name = rng.choice(list(hotspot_edges))
        return rng.choice(hotspot_edges[hotspot_name])
    return rng.choice(drivable_edges).getID()


def choose_trip_edge(
    rng: random.Random,
    drivable_edges: list[Any],
    hotspot_edges: dict[str, list[str]],
    exclude: str | None = None,
) -> str:
    for _ in range(25):
        if rng.random() < 0.68:
            hotspot_name = rng.choice(list(hotspot_edges))
            candidate = rng.choice(hotspot_edges[hotspot_name])
        else:
            candidate = rng.choice(drivable_edges).getID()
        if candidate != exclude:
            return candidate
    return rng.choice([edge.getID() for edge in drivable_edges if edge.getID() != exclude])


def choose_named_request_edge(
    rng: random.Random,
    drivable_edges: list[Any],
    hotspot_edges: dict[str, list[str]],
    exclude: str | None = None,
) -> tuple[str, str]:
    hotspot_names = list(hotspot_edges)
    if rng.random() < 0.74:
        hotspot_name = rng.choice(hotspot_names)
        for _ in range(20):
            edge_id = rng.choice(hotspot_edges[hotspot_name])
            if edge_id != exclude:
                return hotspot_name, edge_id
    return "Neighbourhood Street", choose_trip_edge(rng, drivable_edges, hotspot_edges, exclude=exclude)


def midpoint(shape: list[tuple[float, float]]) -> tuple[float, float]:
    if len(shape) == 1:
        return shape[0]
    index = len(shape) // 2
    return shape[index]


def squared_distance(first: tuple[float, float], second: tuple[float, float]) -> float:
    return (first[0] - second[0]) ** 2 + (first[1] - second[1]) ** 2


def convert_hotspot_to_network_xy(network: Any, lon: float, lat: float) -> tuple[float, float]:
    try:
        return network.convertLonLat2XY(lon, lat)
    except RuntimeError:
        location = getattr(network, "_location", {})
        proj_parameter = location.get("projParameter", "")
        if "+proj=utm" not in proj_parameter:
            raise ScenarioBuildError(
                "Could not convert hotspot coordinates. Install `pyproj` or use a SUMO net with UTM projection metadata."
            )

        zone = extract_utm_zone(proj_parameter)
        northern = "+south" not in proj_parameter
        utm_x, utm_y = lon_lat_to_utm_xy(lon, lat, zone=zone, northern=northern)
        offset_x, offset_y = [float(value) for value in location.get("netOffset", "0,0").split(",")]
        return utm_x + offset_x, utm_y + offset_y


def extract_utm_zone(proj_parameter: str) -> int:
    for token in proj_parameter.split():
        if token.startswith("+zone="):
            return int(token.split("=", 1)[1])
    raise ScenarioBuildError(f"Could not parse UTM zone from projection string: {proj_parameter}")


def lon_lat_to_utm_xy(lon: float, lat: float, zone: int, northern: bool) -> tuple[float, float]:
    # WGS84 ellipsoid constants for a lightweight pyproj-free fallback.
    a = 6378137.0
    flattening = 1 / 298.257223563
    e_sq = flattening * (2 - flattening)
    e_prime_sq = e_sq / (1 - e_sq)
    k0 = 0.9996

    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    lon_origin_rad = math.radians((zone - 1) * 6 - 180 + 3)

    sin_lat = math.sin(lat_rad)
    cos_lat = math.cos(lat_rad)
    tan_lat = math.tan(lat_rad)

    n = a / math.sqrt(1 - e_sq * sin_lat * sin_lat)
    t = tan_lat * tan_lat
    c = e_prime_sq * cos_lat * cos_lat
    a_term = cos_lat * (lon_rad - lon_origin_rad)

    m = a * (
        (1 - e_sq / 4 - 3 * e_sq**2 / 64 - 5 * e_sq**3 / 256) * lat_rad
        - (3 * e_sq / 8 + 3 * e_sq**2 / 32 + 45 * e_sq**3 / 1024) * math.sin(2 * lat_rad)
        + (15 * e_sq**2 / 256 + 45 * e_sq**3 / 1024) * math.sin(4 * lat_rad)
        - (35 * e_sq**3 / 3072) * math.sin(6 * lat_rad)
    )

    x = k0 * n * (
        a_term
        + (1 - t + c) * a_term**3 / 6
        + (5 - 18 * t + t**2 + 72 * c - 58 * e_prime_sq) * a_term**5 / 120
    ) + 500000.0

    y = k0 * (
        m
        + n
        * tan_lat
        * (
            a_term**2 / 2
            + (5 - t + 9 * c + 4 * c**2) * a_term**4 / 24
            + (61 - 58 * t + t**2 + 600 * c - 330 * e_prime_sq) * a_term**6 / 720
        )
    )
    if not northern:
        y += 10_000_000.0

    return x, y


def indent_xml(element: ET.Element, level: int = 0) -> None:
    indent = "\n" + level * "    "
    if len(element):
        if not element.text or not element.text.strip():
            element.text = indent + "    "
        for child in element:
            indent_xml(child, level + 1)
        if not element[-1].tail or not element[-1].tail.strip():
            element[-1].tail = indent
    if level and (not element.tail or not element.tail.strip()):
        element.tail = indent
