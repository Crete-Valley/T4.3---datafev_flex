from dataclasses import dataclass


@dataclass
class ClusterForecast():
    cluster_id: int
    ts: str
    downward_capability_kW: float
    upward_capability_kW: float
    connected_evs: int
    cluster_power_kW: float