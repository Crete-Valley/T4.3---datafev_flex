from dataclasses import dataclass
from datetime import datetime


@dataclass
class Cluster:
    cluster_id: int
    cu_id: str
    p_max_ch_kW: float
    p_max_ds_kW: float
    efficiency: float


@dataclass
class Fleet:
    vehicle_id: str
    battery_capacity_kWh: float
    arrival_time: datetime
    departure_time: datetime
    initial_soc: float
    target_soc: float
    use_target_soc: bool
    min_allowed_soc: float
    max_allowed_soc: float
    target_cluster: str
    p_max_charge_kW: float
    p_max_discharge_kW: float
    exact_target_soc: bool = False


@dataclass
class MarketPrice:
    timestamp: datetime
    price_eur_per_kwh: float


@dataclass
class ClusterForecast:
    cluster_id: int
    timestamp: str
    downward_capability_kW: float
    upward_capability_kW: float
    connected_evs: int
    cluster_power_kW: float


@dataclass
class ChargingSchedule:
    vehicle_id: str
    cluster_id: str
    arrival_time_ts: datetime
    departure_time_ts: datetime
    initial_soc: float
    target_soc: float
    scheduled_departure_soc: float
    charged_energy_kWh: float
    total_charging_cost_eur: float