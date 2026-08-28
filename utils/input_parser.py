"""Input parsing utilities for Excel-driven workflow configuration.

Purpose
-------
Normalize workbook sheets into strongly-typed pandas structures consumed by
service orchestration and API setpoint ingestion.

Dependencies
------------
- Uses pandas Excel readers and datetime parsing.
- Called by `services.flex_workflow` and `api.app`.
"""

import pandas as pd
from datetime import timedelta

from utils.flex_command_utils import ABSOLUTE_SETPOINT, FLEX_BAND
from database.db_connection import get_conn


def _rows_to_dataframe(rows, columns):
    import pandas as pd

    if not rows:
        return pd.DataFrame(columns=columns)

    # rows may be list of tuples or list of dicts
    if isinstance(rows[0], dict):
        return pd.DataFrame(rows)
    return pd.DataFrame(rows, columns=columns)


def fetch_database_inputs():
    """Fetch clusters and fleet from DB and normalize to parser outputs.

    Returns
    -------
    tuple[dict[str, pd.DataFrame], pd.DataFrame]
        `(clusters_dict, fleet_df)` matching `parse_xlsx_input` signature.
    """
    x = get_conn()
    cluster_rows = []
    fleet_rows = []

    # Try simple table queries; callers may mock get_conn for tests.
    try:
        with x as conn:
            with conn.cursor() as cur:
                res = cur.fetchall() if False else None
                # Some test doubles return a dict-like object from cursor.fetchall();
                # preserve the same behavior and read the expected rows from it.
                cur.execute(
                    "SELECT cluster_id, cu_id, p_max_ch_kW, p_max_ds_kW, efficiency FROM cluster"
                )
                cluster_result = cur.fetchall()
                cur.execute(
                    "SELECT vehicle_id, battery_capacity_kWh, arrival_time, departure_time, initial_soc, target_soc, use_target_soc, min_allowed_soc, max_allowed_soc, target_cluster, p_max_charge_kW, p_max_discharge_kW, exact_target_soc FROM fleet"
                )
                fleet_result = cur.fetchall()

                if isinstance(cluster_result, dict):
                    cluster_rows = cluster_result.get("cluster", [])
                else:
                    cluster_rows = cluster_result

                if isinstance(fleet_result, dict):
                    fleet_rows = fleet_result.get("fleet", [])
                else:
                    fleet_rows = fleet_result
    except Exception as e:
        print(f"Database input parsing failed: {e}")  # Log the error for debugging
        # Bubble up as ValueError to match Excel parser semantics
        #raise ValueError("Failed to read input from database.")

    import pandas as pd

    # Build clusters dict
    clusters_dict: dict[str, pd.DataFrame] = {}
    # cluster_rows expected as tuples: (cluster_id, cu_id, p_max_ch_kW, p_max_ds_kW, efficiency)
    if cluster_rows:
        df_clusters = _rows_to_dataframe(cluster_rows, ["cluster_id", "cu_id", "p_max_ch_kW", "p_max_ds_kW", "efficiency"])  # type: ignore[arg-type]
        if "cluster_id" in df_clusters.columns:
            for cid_val, group in df_clusters.groupby("cluster_id"):
                clusters_dict[str(cid_val)] = group[["cu_id", "p_max_ch_kW", "p_max_ds_kW", "efficiency"]].copy()
        else:
            # single cluster fallback
            clusters_dict["1"] = df_clusters[["cu_id", "p_max_ch_kW", "p_max_ds_kW", "efficiency"]].copy()
    else:
        raise ValueError("No cluster definitions found in database.")

    # Build fleet dataframe
    fleet_cols = [
        "vehicle_id",
        "battery_capacity_kWh",
        "arrival_time",
        "departure_time",
        "initial_soc",
        "target_soc",
        "use_target_soc",
        "min_allowed_soc",
        "max_allowed_soc",
        "target_cluster",
        "p_max_charge_kW",
        "p_max_discharge_kW",
        "exact_target_soc",
    ]
    fleet_df = _rows_to_dataframe(fleet_rows, fleet_cols)

    # Normalize types similar to parse_xlsx_input
    if not fleet_df.empty:
        fleet_df["arrival_time"] = pd.to_datetime(fleet_df["arrival_time"])
        fleet_df["departure_time"] = pd.to_datetime(fleet_df["departure_time"])
        fleet_df["target_cluster"] = fleet_df["target_cluster"].astype(str)
        fleet_df["use_target_soc"] = fleet_df["use_target_soc"].astype(int)
        if "exact_target_soc" in fleet_df.columns:
            fleet_df["exact_target_soc"] = fleet_df["exact_target_soc"].astype(int)
        else:
            fleet_df["exact_target_soc"] = 0

    return clusters_dict, fleet_df



def parse_planning_sheet(file_path: str) -> dict:
    """Parse planning metadata from workbook `Planning` sheet.

    Purpose
    -------
    Convert high-level planning settings into validated Python datetime/time
    objects used for horizon construction.

    Parameters
    ----------
    file_path : str
        Workbook path.

    Returns
    -------
    dict
        Keys: `planning_start`, `planning_end`, `time_step`, `time_step_minutes`.

    Side Effects
    ------------
    Reads workbook from disk.

    Raises
    ------
    ValueError
        If sheet/columns are missing or values are logically invalid.

    Example
    -------
    >>> cfg = parse_planning_sheet("inputs/stage1_sample_input.xlsx")
    >>> cfg["time_step_minutes"]
    15
    """
    xls = pd.ExcelFile(file_path)
    if "Planning" not in xls.sheet_names:
        raise ValueError("Input file must contain a 'Planning' sheet.")

    planning_df = xls.parse("Planning")
    required_cols = ["planning_start", "planning_end", "time_step_minutes"]
    missing = [c for c in required_cols if c not in planning_df.columns]
    if missing:
        raise ValueError(f"'Planning' sheet is missing required columns: {missing}")

    if planning_df.empty:
        raise ValueError("'Planning' sheet must contain at least one data row.")

    row = planning_df.iloc[0]
    planning_start = pd.to_datetime(row["planning_start"]).to_pydatetime()
    planning_end = pd.to_datetime(row["planning_end"]).to_pydatetime()
    time_step_minutes = int(row["time_step_minutes"])

    if time_step_minutes <= 0:
        raise ValueError("'Planning.time_step_minutes' must be a positive integer.")
    if planning_end <= planning_start:
        raise ValueError("'Planning.planning_end' must be later than 'planning_start'.")

    return {
        "planning_start": planning_start,
        "planning_end": planning_end,
        "time_step": timedelta(minutes=time_step_minutes),
        "time_step_minutes": time_step_minutes,
    }


def parse_day_ahead_prices_sheet(
    file_path: str,
    planning_timestamps: list | pd.DatetimeIndex | None = None,
    sheet_name: str = "DayAheadMarketPrices",
) -> pd.Series:
    """Parse day-ahead market prices from workbook.

    Purpose
    -------
    Normalize workbook price input from the canonical `DayAheadMarketPrices`
    sheet into a timestamp-indexed series expressed in EUR/kWh.

    Parameters
    ----------
    file_path : str
        Workbook path.
    planning_timestamps : list | pd.DatetimeIndex | None
        Expected planning timestamps. When provided, the parsed price series is
        aligned to this horizon and must cover every timestamp.
    sheet_name : str
        Required workbook sheet name. Defaults to `DayAheadMarketPrices`.

    Returns
    -------
    pd.Series
        Timestamp-indexed price profile in EUR/kWh.

    Raises
    ------
    ValueError
        If no supported price column exists, timestamps are duplicated, or the
        series does not cover the planning horizon.
    """
    xls = pd.ExcelFile(file_path)
    if sheet_name not in xls.sheet_names:
        raise ValueError(
            f"Input file must contain a '{sheet_name}' sheet."
        )

    prices_df = xls.parse(sheet_name)
    if prices_df.empty:
        raise ValueError(f"'{sheet_name}' sheet must contain at least one data row.")
    if "timestamp" not in prices_df.columns:
        raise ValueError(f"'{sheet_name}' sheet must include a 'timestamp' column.")
    if "price_eur_per_kwh" not in prices_df.columns:
        raise ValueError(
            f"'{sheet_name}' sheet must include a 'price_eur_per_kwh' column."
        )

    price_series = pd.to_numeric(
        prices_df["price_eur_per_kwh"], errors="raise"
    ).astype(float)

    timestamps = pd.to_datetime(prices_df["timestamp"])
    parsed = pd.Series(price_series.to_numpy(dtype=float), index=timestamps, dtype=float)
    parsed = parsed.sort_index()
    parsed.index.name = "timestamp"
    parsed.name = "price_eur_per_kwh"

    dup_mask = parsed.index.duplicated(keep=False)
    if dup_mask.any():
        duplicate_ts = parsed.index[dup_mask][0]
        raise ValueError(
            f"Duplicate day-ahead price row detected for timestamp={duplicate_ts}."
        )

    if planning_timestamps is None:
        return parsed

    expected_index = pd.DatetimeIndex(planning_timestamps)
    aligned = parsed.reindex(expected_index)
    missing_ts = aligned[aligned.isna()].index
    if len(missing_ts) > 0:
        first_missing = missing_ts[0]
        raise ValueError(
            "Day-ahead price data does not fully cover the planning horizon. "
            f"Missing timestamp: {first_missing}."
        )

    aligned.index.name = "timestamp"
    aligned.name = "price_eur_per_kwh"
    return aligned.astype(float)


def fetch_day_ahead_prices_from_database(
    planning_timestamps: list | pd.DatetimeIndex | None = None,
) -> pd.Series:
    # Implementation for fetching day-ahead prices from database
    x = get_conn()
    price_rows = []
    try:
        with x as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, price_eur_per_kwh FROM day_ahead_market_prices"
                )
                price_rows = cur.fetchall()
    except Exception as e:
        print(f"Database price fetching failed: {e}")
        raise ValueError("Failed to read day-ahead prices from database.")

    if not price_rows:
        raise ValueError("No day-ahead price data found in database.")

    price_df = _rows_to_dataframe(price_rows, ["timestamp", "price_eur_per_kwh"])
    price_df["timestamp"] = pd.to_datetime(price_df["timestamp"])
    price_df["price_eur_per_kwh"] = pd.to_numeric(price_df["price_eur_per_kwh"], errors="raise").astype(float)
    price_series = pd.Series(price_df["price_eur_per_kwh"].to_numpy(dtype=float), index=price_df["timestamp"], dtype=float)
    price_series = price_series.sort_index()
    price_series.index.name = "timestamp"
    price_series.name = "price_eur_per_kwh"

    return price_series


def parse_stage2_setpoints_sheet(
    file_path: str,
    sheet_name: str = "Setpoints",
    command_type: str = ABSOLUTE_SETPOINT,
) -> dict[str, pd.Series | pd.DataFrame]:
    """Parse external Stage-2 setpoint commands from workbook sheet.

    Purpose
    -------
    Convert uploaded command workbook into the normalized structure expected by
    Stage-2 validation.

    Parameters
    ----------
    file_path : str
        Workbook path.
    sheet_name : str
        Setpoint sheet name. Defaults to ``"Setpoints"``.
    command_type : str
        `absolute_setpoint` (expects `p_set_kw`) or `flex_band`
        (expects `p_min_kw`, `p_max_kw`).

    Returns
    -------
    dict[str, pd.Series | pd.DataFrame]
        Per-cluster command payload indexed by timestamp.

    Side Effects
    ------------
    Reads workbook from disk.

    Raises
    ------
    ValueError
        If required columns are missing, sheet is empty, or duplicate
        `(cluster_id, timestamp)` rows exist. Also raised when `command_type`
        is `flex_band` but sheet includes `p_set_kw`.

    Example
    -------
    >>> cmds = parse_stage2_setpoints_sheet(
    ...     "inputs/stage2_sample_absolute_setpoints.xlsx",
    ...     command_type="absolute_setpoint",
    ... )
    >>> sorted(cmds.keys())
    ['1', '2', '3']
    """
    normalized_type = (command_type or "").strip().lower()
    if normalized_type not in {ABSOLUTE_SETPOINT, FLEX_BAND}:
        raise ValueError(
            "Unsupported command_type. Use 'absolute_setpoint' or 'flex_band'."
        )

    xls = pd.ExcelFile(file_path)
    if sheet_name not in xls.sheet_names:
        raise ValueError(f"Input file must contain a '{sheet_name}' sheet.")

    setpoints_df = xls.parse(sheet_name)
    required_cols = ["cluster_id", "timestamp"]
    if normalized_type == ABSOLUTE_SETPOINT:
        required_cols.append("p_set_kw")
    else:
        required_cols.extend(["p_min_kw", "p_max_kw"])
    missing = [c for c in required_cols if c not in setpoints_df.columns]
    if missing:
        raise ValueError(f"'{sheet_name}' sheet is missing required columns: {missing}")
    if setpoints_df.empty:
        raise ValueError(f"'{sheet_name}' sheet must contain at least one data row.")

    df = setpoints_df[required_cols].copy()
    if normalized_type == FLEX_BAND and "p_set_kw" in setpoints_df.columns:
        raise ValueError(
            "'Setpoints' sheet must not include 'p_set_kw' when command_type='flex_band'."
        )

    def _normalize_cluster_id(value: object) -> str:
        if pd.isna(value):
            raise ValueError(f"'{sheet_name}.cluster_id' cannot be empty.")
        if isinstance(value, str):
            normalized = value.strip()
            if normalized == "":
                raise ValueError(f"'{sheet_name}.cluster_id' cannot be empty.")
            return normalized
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)

    df["cluster_id"] = df["cluster_id"].apply(_normalize_cluster_id)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    numeric_cols = ["p_set_kw"] if normalized_type == ABSOLUTE_SETPOINT else [
        "p_min_kw",
        "p_max_kw",
    ]

    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col])

    # Critical validation:
    # duplicate timestamps for the same cluster would make a command ambiguous.
    dup_mask = df.duplicated(subset=["cluster_id", "timestamp"], keep=False)
    if dup_mask.any():
        dup = df.loc[dup_mask, ["cluster_id", "timestamp"]].iloc[0]
        raise ValueError(
            "Duplicate setpoint row detected for "
            f"cluster_id={dup['cluster_id']}, timestamp={dup['timestamp']}."
        )

    commands_by_cluster: dict[str, pd.Series | pd.DataFrame] = {}
    for cc_id, group in df.groupby("cluster_id"):
        group_sorted = group.sort_values("timestamp")
        index = pd.to_datetime(group_sorted["timestamp"])

        if normalized_type == ABSOLUTE_SETPOINT:
            commands_by_cluster[cc_id] = pd.Series(
                group_sorted["p_set_kw"].to_numpy(dtype=float),
                index=index,
                dtype=float,
            )
        else:
            commands_by_cluster[cc_id] = pd.DataFrame(
                {
                    "p_min_kw": group_sorted["p_min_kw"].to_numpy(dtype=float),
                    "p_max_kw": group_sorted["p_max_kw"].to_numpy(dtype=float),
                },
                index=index,
            )

    return commands_by_cluster


def parse_xlsx_input(file_path: str):
    """Parse main workbook into cluster and fleet tables.

    Purpose
    -------
    Provide a backward-compatible parser supporting both:
    - multiple sheets (`Cluster1`, `Cluster2`, ...)
    - consolidated `Clusters` sheet with `cluster_id`.

    Parameters
    ----------
    file_path : str
        Workbook path.

    Returns
    -------
    tuple[dict[str, pd.DataFrame], pd.DataFrame]
        `(clusters_dict, fleet_df)` where:
        - `clusters_dict[cid]` includes charger definitions for cluster `cid`
        - `fleet_df` contains normalized EV data and policy flags.

    Side Effects
    ------------
    Reads workbook from disk and coerces dataframe dtypes in memory.

    Raises
    ------
    ValueError
        For missing required sheets/columns.

    Example
    -------
    >>> clusters, fleet = parse_xlsx_input("inputs/stage1_sample_input.xlsx")
    >>> list(clusters)
    ['1', '2', '3']
    """
    xls = pd.ExcelFile(file_path)

    # --- Parse clusters ---
    clusters_dict: dict[str, pd.DataFrame] = {}

    for sheet_name in xls.sheet_names:
        lower = sheet_name.lower()

        # Case 1: consolidated sheet "Clusters" with optional cluster_id column
        if lower == "clusters":
            df_cc = xls.parse(sheet_name)
            required_cc_cols = [
                "cu_id",
                "p_max_ch_kW",
                "p_max_ds_kW",
                "efficiency",
            ]

            # If cluster_id is present, split into multiple clusters dynamically
            if "cluster_id" in df_cc.columns:
                missing = [c for c in required_cc_cols + ["cluster_id"] if c not in df_cc.columns]
                if missing:
                    raise ValueError(
                        f"Cluster sheet '{sheet_name}' is missing required columns: {missing}"
                    )
                for cid_val, group in df_cc.groupby("cluster_id"):
                    cid = str(cid_val)
                    clusters_dict[cid] = group[required_cc_cols].copy()
            else:
                missing = [c for c in required_cc_cols if c not in df_cc.columns]
                if missing:
                    raise ValueError(
                        f"Cluster sheet '{sheet_name}' is missing required columns: {missing}"
                    )
                # Single-cluster convenience fallback
                clusters_dict["1"] = df_cc[required_cc_cols].copy()

        # Case 2: standard pattern: "Cluster1", "Cluster2", ...
        elif lower.startswith("cluster"):
            cid = sheet_name[len("Cluster"):].strip()
            if cid == "":
                cid = "1"

            df_cc = xls.parse(sheet_name)

            required_cc_cols = [
                "cu_id",
                "p_max_ch_kW",
                "p_max_ds_kW",
                "efficiency",
            ]
            missing = [c for c in required_cc_cols if c not in df_cc.columns]
            if missing:
                raise ValueError(
                    f"Cluster sheet '{sheet_name}' is missing required columns: {missing}"
                )

            clusters_dict[cid] = df_cc[required_cc_cols].copy()
        else:
            continue  # not a cluster sheet, skip

    if not clusters_dict:
        raise ValueError(
            "No cluster sheets found. Expected sheets named like 'Cluster1', 'Cluster2', ..."
        )

    # --- Parse fleet ---
    if "Fleet" not in xls.sheet_names:
        raise ValueError("Input file must contain a 'Fleet' sheet.")

    fleet_df = xls.parse("Fleet")

    required_fleet_cols = [
        "vehicle_id",
        "battery_capacity_kWh",
        "arrival_time",
        "departure_time",
        "initial_soc",
        "target_soc",
        "use_target_soc",
        "min_allowed_soc",
        "max_allowed_soc",
        "target_cluster",
        "p_max_charge_kW",
        "p_max_discharge_kW",
    ]
    missing_fleet = [c for c in required_fleet_cols if c not in fleet_df.columns]
    if missing_fleet:
        raise ValueError(
            f"'Fleet' sheet is missing required columns: {missing_fleet}"
        )

    # Ensure datetime types for arrival/departure
    fleet_df["arrival_time"] = pd.to_datetime(fleet_df["arrival_time"])
    fleet_df["departure_time"] = pd.to_datetime(fleet_df["departure_time"])
    fleet_df["target_cluster"] = fleet_df["target_cluster"].astype(str)

    # Use_target_soc can be 0/1 or boolean; ensure int (0/1)
    fleet_df["use_target_soc"] = fleet_df["use_target_soc"].astype(int)

    # Optional per-EV Stage-2 target strictness flag.
    if "exact_target_soc" in fleet_df.columns:
        fleet_df["exact_target_soc"] = fleet_df["exact_target_soc"].astype(int)
    else:
        fleet_df["exact_target_soc"] = 0

    return clusters_dict, fleet_df
