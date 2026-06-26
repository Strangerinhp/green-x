from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from hanoi_taxi_sumo.scenario import ScenarioBuildError, build_default_scenario


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the central Hanoi SUMO taxi scenario assets."
    )
    parser.add_argument("--sumo-home", help="Override SUMO_HOME for this run.")
    parser.add_argument("--fleet-size", type=int, help="Number of taxi vehicles.")
    parser.add_argument("--requests", type=int, help="Number of ride requests.")
    parser.add_argument("--background-trips", type=int, help="Number of non-taxi background trips.")
    parser.add_argument("--duration", type=int, help="Scenario duration in seconds.")
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Redownload the OSM map even if it already exists.",
    )
    args = parser.parse_args()

    try:
        paths = build_default_scenario(
            sumo_home=args.sumo_home,
            fleet_size=args.fleet_size,
            request_count=args.requests,
            background_trip_count=args.background_trips,
            duration_s=args.duration,
            force_download=args.force_download,
        )
    except ScenarioBuildError as exc:
        print(f"[bootstrap] {exc}", file=sys.stderr)
        return 1

    print("[bootstrap] Scenario created successfully.")
    print(f"[bootstrap] SUMO config: {paths.sumocfg}")
    print(f"[bootstrap] Request schedule: {paths.request_schedule}")
    print(f"[bootstrap] Metadata: {paths.metadata}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
