from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the central Hanoi taxi ride-hailing simulation with a live dashboard."
    )
    parser.add_argument("--port", type=int, default=8050, help="Dashboard port.")
    parser.add_argument("--seed", type=int, help="Override the random seed.")
    parser.add_argument(
        "--nogui",
        action="store_true",
        help="Run with sumo instead of sumo-gui.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not auto-open the dashboard in a browser.",
    )
    args = parser.parse_args()

    try:
        from hanoi_taxi_sumo.simulation import SimulationRunError, run_default_simulation
    except ModuleNotFoundError as exc:
        print(
            "[runner] Missing Python dependency. Run `pip install -r requirements.txt` first.",
            file=sys.stderr,
        )
        print(f"[runner] {exc}", file=sys.stderr)
        return 1

    try:
        run_default_simulation(
            port=args.port,
            gui=not args.nogui,
            open_browser=not args.no_browser,
            seed=args.seed,
        )
    except SimulationRunError as exc:
        print(f"[runner] {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
