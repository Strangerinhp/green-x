from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class BoundingBox:
    south: float
    west: float
    north: float
    east: float

    @property
    def center_lat(self) -> float:
        return (self.south + self.north) / 2

    @property
    def center_lon(self) -> float:
        return (self.west + self.east) / 2

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True)
class Hotspot:
    name: str
    lat: float
    lon: float
    weight: float

    def as_dict(self) -> dict[str, float | str]:
        return asdict(self)


@dataclass(frozen=True)
class DistrictConfig:
    key: str
    name: str
    description: str
    bbox: BoundingBox
    hotspots: tuple[Hotspot, ...]

    @property
    def center(self) -> tuple[float, float]:
        return (self.bbox.center_lat, self.bbox.center_lon)

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "bbox": self.bbox.as_dict(),
            "center": {"lat": self.center[0], "lon": self.center[1]},
            "hotspots": [hotspot.as_dict() for hotspot in self.hotspots],
        }


@dataclass(frozen=True)
class FareConfig:
    currency: str = "VND"
    base_fare: float = 18_000.0
    distance_rate_per_km: float = 12_000.0
    time_rate_per_min: float = 3_000.0
    minimum_fare: float = 28_000.0

    def estimate(self, distance_m: float, duration_s: float) -> float:
        fare = (
            self.base_fare
            + (distance_m / 1_000.0) * self.distance_rate_per_km
            + (duration_s / 60.0) * self.time_rate_per_min
        )
        return max(self.minimum_fare, round(fare, 0))


@dataclass(frozen=True)
class SimulationConfig:
    scenario_name: str = "Hanoi Taxi Ride-Hailing Demo"
    local_start_hour: int = 8
    duration_s: int = 7_200
    step_length_s: int = 1
    fleet_size: int = 55
    request_count: int = 320
    background_trip_count: int = 220
    random_seed: int = 42
    taxi_idle_algorithm: str = "stop"
    taxi_pickup_duration_s: int = 10
    taxi_dropoff_duration_s: int = 20
    dispatch_recheck_period_s: int = 5
    fare: FareConfig = field(default_factory=FareConfig)

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["fare"] = asdict(self.fare)
        return payload


DEFAULT_DISTRICT = DistrictConfig(
    key="hoan_kiem_core",
    name="Hoan Kiem District Core",
    description=(
        "A compact, taxi-friendly central Hanoi envelope around Hoan Kiem Lake, "
        "the Old Quarter, the railway station, and the Opera House."
    ),
    bbox=BoundingBox(
        south=21.0178,
        west=105.8380,
        north=21.0408,
        east=105.8638,
    ),
    hotspots=(
        Hotspot("Hoan Kiem Lake", lat=21.02883, lon=105.85236, weight=1.55),
        Hotspot("Dong Xuan Market", lat=21.03856, lon=105.84963, weight=1.10),
        Hotspot("Hanoi Railway Station", lat=21.02453, lon=105.84125, weight=1.05),
        Hotspot("Hanoi Opera House", lat=21.02455, lon=105.85776, weight=0.95),
        Hotspot("St. Joseph Cathedral", lat=21.02835, lon=105.84884, weight=0.85),
        Hotspot("Long Bien Station", lat=21.04015, lon=105.85465, weight=0.75),
    ),
)

DEFAULT_SIMULATION = SimulationConfig()
