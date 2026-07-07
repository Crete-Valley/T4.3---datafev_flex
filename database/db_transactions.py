from utils.db_connection import get_conn
from utils.db_models import ClusterForecast


# TODO: clarify whether this is what is needed
def fetch_cluster_forecast():
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM cluster_forecast")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows


def insert_cluster_forecast(forecast: ClusterForecast):
    with get_conn() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO cluster_forecast (cluster_id, ts, downward_capability_kW, 
                            upward_capability_kW, connected_evs, cluster_power_kW)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (cluster_id, ts) DO NOTHING;""",
                (forecast.cluster_id, forecast.ts, forecast.downward_capability_kW,
                 forecast.upward_capability_kW, forecast.connected_evs, forecast.cluster_power_kW)
            )
            conn.commit()
            cursor.close()
        conn.close()