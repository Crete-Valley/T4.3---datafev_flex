import math
import pandas as pd
import pytest

from algorithms.capability.g2v_capability import compute_g2v_capability
from utils.input_parser import parse_xlsx_input


@pytest.mark.integration
def test_end_to_end_parsing_and_capability_computation(excel_builder, fake_solver):
    """Integration test covering Excel parsing through capability computation."""
    fleet_df = pd.DataFrame(
        [
            {
                "vehicle_id": "EV_A",
                "battery_capacity_kWh": 70,
                "arrival_time": "2024-02-01 08:00",
                "departure_time": "2024-02-01 10:30",
                "initial_soc": 0.3,
                "target_soc": 0.85,
                "use_target_soc": 1,
                "min_allowed_soc": 0.2,
                "max_allowed_soc": 0.95,
                "target_cluster": "1",
                "p_max_charge_kW": 11.0,
                "p_max_discharge_kW": 3.0,
            },
            {
                "vehicle_id": "EV_B",
                "battery_capacity_kWh": 60,
                "arrival_time": "2024-02-01 08:30",
                "departure_time": "2024-02-01 11:00",
                "initial_soc": 0.4,
                "target_soc": 0.9,
                "use_target_soc": 0,
                "min_allowed_soc": 0.25,
                "max_allowed_soc": 0.98,
                "target_cluster": "2",
                "p_max_charge_kW": 7.2,
                "p_max_discharge_kW": 2.5,
            },
        ]
    )
    cluster_one = pd.DataFrame(
        {
            "cu_id": ["C1"],
            "p_max_ch_kW": [50],
            "p_max_ds_kW": [30],
            "efficiency": [0.95],
        }
    )
    cluster_two = pd.DataFrame(
        {
            "cu_id": ["C2"],
            "p_max_ch_kW": [60],
            "p_max_ds_kW": [40],
            "efficiency": [0.9],
        }
    )
    file_path = excel_builder(
        {"Fleet": fleet_df, "Cluster1": cluster_one, "Cluster2": cluster_two}
    )

    clusters, parsed_fleet = parse_xlsx_input(str(file_path))

    assert set(clusters.keys()) == {"1", "2"}, "Both cluster definitions should be discovered"
    assert parsed_fleet["use_target_soc"].tolist() == [1, 0]

    indexed = parsed_fleet.set_index("vehicle_id")
    opt_step = 1800  # 30 min resolution
    start_time = indexed["arrival_time"].min()
    horizon_end = int(
        ((indexed["departure_time"].max() - start_time).total_seconds() // opt_step) + 1
    )
    opt_horizon = list(range(0, horizon_end + 1))

    cluster_eff = {cid: df["efficiency"].mean() for cid, df in clusters.items()}

    def _per_vehicle(series_name):
        return indexed[series_name].to_dict()

    bcap = _per_vehicle("battery_capacity_kWh")
    inisoc = _per_vehicle("initial_soc")
    tarsoc = _per_vehicle("target_soc")
    minsoc = _per_vehicle("min_allowed_soc")
    maxsoc = _per_vehicle("max_allowed_soc")
    pmax_pos = _per_vehicle("p_max_charge_kW")
    pmax_neg = _per_vehicle("p_max_discharge_kW")
    ch_eff = {vid: cluster_eff[indexed.at[vid, "target_cluster"]] for vid in indexed.index}
    ds_eff = ch_eff.copy()

    deptime = {}
    for vid, row in indexed.iterrows():
        deptime[vid] = int(((row["departure_time"] - start_time).total_seconds()) // opt_step)
    arrtime = {}
    for vid, row in indexed.iterrows():
        arrtime[vid] = int(math.ceil(((row["arrival_time"] - start_time).total_seconds()) / opt_step))

    power_assignments = {
        0: {"EV_A": 3.0},
        1: {"EV_A": 2.5, "EV_B": 2.0},
        2: {"EV_A": 2.0, "EV_B": 2.0},
        3: {"EV_A": 1.0, "EV_B": 0.5},
    }
    soc_assignments = {
        0: {"EV_A": 0.3, "EV_B": 0.4},
        1: {"EV_A": 0.45, "EV_B": 0.5},
        2: {"EV_A": 0.6, "EV_B": 0.65},
        3: {"EV_A": 0.75, "EV_B": 0.8},
        4: {"EV_A": 0.85, "EV_B": 0.9},
    }
    solver = fake_solver(power_assignments=power_assignments, soc_assignments=soc_assignments)

    p_ev_pos, _, cluster_profile = compute_g2v_capability(
        solver,
        opt_step,
        opt_horizon,
        bcap,
        inisoc,
        tarsoc,
        minsoc,
        maxsoc,
        ch_eff,
        ds_eff,
        pmax_pos,
        pmax_neg,
        deptime,
        arrtime=arrtime,
    )

    assert solver.solve_called, "Solver should be exercised in the workflow"
    assert p_ev_pos[1]["EV_A"] == pytest.approx(2.5)
    assert cluster_profile[0] == pytest.approx(3.0)
    assert cluster_profile[3] == pytest.approx(1.5), "Cluster power should aggregate EV power with arrivals respected"
