from database.db_connection import get_conn
from database.db_models import Cluster, ClusterForecast, Fleet


# TODO: clarify whether this is what is needed
def fetch_cluster_forecast():
    with get_conn() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM cluster_forecast")
            rows = cursor.fetchall()
            return rows


def insert_cluster_forecast(forecast: ClusterForecast):
    with get_conn() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO cluster_forecast (cluster_id, timestamp, downward_capability_kW,
                            upward_capability_kW, connected_evs, cluster_power_kW)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (cluster_id, "timestamp") DO NOTHING;""",
                (forecast.cluster_id, forecast.timestamp, forecast.downward_capability_kW,
                 forecast.upward_capability_kW, forecast.connected_evs, forecast.cluster_power_kW)
            )
            conn.commit()


def insert_cluster(cluster: Cluster):
    with get_conn() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO cluster (cluster_id, cu_id, p_max_ch_kW, p_max_ds_kW, efficiency)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (cluster_id) DO NOTHING""",
                (cluster.cluster_id, cluster.cu_id, cluster.p_max_ch_kW,
                 cluster.p_max_ds_kW, cluster.efficiency),
            )
            conn.commit()


def insert_fleet(vehicle: Fleet):
    with get_conn() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO fleet (
                    vehicle_id, battery_capacity_kWh, arrival_time, departure_time,
                    initial_soc, target_soc, use_target_soc, min_allowed_soc,
                    max_allowed_soc, target_cluster, p_max_charge_kW, p_max_discharge_kW,
                    exact_target_soc
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (vehicle_id) DO NOTHING""",
                (vehicle.vehicle_id, vehicle.battery_capacity_kWh, vehicle.arrival_time,
                 vehicle.departure_time, vehicle.initial_soc, vehicle.target_soc,
                 vehicle.use_target_soc, vehicle.min_allowed_soc, vehicle.max_allowed_soc,
                 vehicle.target_cluster, vehicle.p_max_charge_kW, vehicle.p_max_discharge_kW,
                 vehicle.exact_target_soc),
            )
            conn.commit()