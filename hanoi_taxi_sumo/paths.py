from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
GENERATED_DIR = DATA_DIR / "generated"
RUNTIME_DIR = DATA_DIR / "runtime"
OUTPUT_DIR = DATA_DIR / "outputs"


def slugify(value: str) -> str:
    return (
        value.lower()
        .replace(" ", "_")
        .replace("-", "_")
        .replace("/", "_")
        .replace("(", "")
        .replace(")", "")
    )


@dataclass(frozen=True)
class ScenarioPaths:
    slug: str
    scenario_dir: Path
    runtime_dir: Path
    outputs_dir: Path
    osm_map: Path
    network: Path
    polygons: Path
    taxi_trips: Path
    taxi_routes: Path
    background_trips: Path
    background_routes: Path
    request_schedule: Path
    metadata: Path
    gui_settings: Path
    sumocfg: Path
    live_state: Path
    ride_log_csv: Path
    summary_json: Path
    tripinfo_xml: Path
    fcd_xml: Path


def build_scenario_paths(district_key: str) -> ScenarioPaths:
    slug = f"hanoi_taxi_{slugify(district_key)}"
    scenario_dir = GENERATED_DIR / slug
    runtime_dir = RUNTIME_DIR / slug
    outputs_dir = OUTPUT_DIR / slug

    scenario_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)

    return ScenarioPaths(
        slug=slug,
        scenario_dir=scenario_dir,
        runtime_dir=runtime_dir,
        outputs_dir=outputs_dir,
        osm_map=scenario_dir / "district_map.osm.xml",
        network=scenario_dir / "district.net.xml",
        polygons=scenario_dir / "district.poly.xml",
        taxi_trips=scenario_dir / "taxis.trips.xml",
        taxi_routes=scenario_dir / "taxis.rou.xml",
        background_trips=scenario_dir / "background.trips.xml",
        background_routes=scenario_dir / "background.rou.xml",
        request_schedule=scenario_dir / "ride_requests.json",
        metadata=scenario_dir / "scenario_metadata.json",
        gui_settings=scenario_dir / "gui-settings.xml",
        sumocfg=scenario_dir / "hanoi_taxi.sumocfg",
        live_state=runtime_dir / "live_state.json",
        ride_log_csv=outputs_dir / "ride_log.csv",
        summary_json=outputs_dir / "summary.json",
        tripinfo_xml=outputs_dir / "tripinfo.xml",
        fcd_xml=outputs_dir / "fleet_fcd.xml",
    )

