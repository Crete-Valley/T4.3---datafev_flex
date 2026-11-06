BEGIN;

CREATE TABLE IF NOT EXISTS public.cluster (
    cluster_id              character varying(100) COLLATE pg_catalog."default" NOT NULL,
    cu_id                   character varying(100) COLLATE pg_catalog."default" NOT NULL,
    p_max_ch_kW             double precision NOT NULL DEFAULT 0,
    p_max_ds_kW             double precision NOT NULL DEFAULT 0,
    efficiency              double precision NOT NULL DEFAULT 0,
    CONSTRAINT clusters_pkey PRIMARY KEY (cluster_id)
);

CREATE TABLE IF NOT EXISTS public.fleet (
    vehicle_id              character varying(100) COLLATE pg_catalog."default" NOT NULL,
    battery_capacity_kWh    double precision NOT NULL,
    arrival_time            timestamp without time zone NOT NULL,
    departure_time          timestamp without time zone NOT NULL,
    initial_soc             double precision NOT NULL,
    target_soc              double precision NOT NULL,
    use_target_soc          boolean NOT NULL,
    min_allowed_soc         double precision NOT NULL,
    max_allowed_soc         double precision NOT NULL,
    target_cluster          character varying(100) COLLATE pg_catalog."default" NOT NULL,
    p_max_charge_kW         double precision NOT NULL,
    p_max_discharge_kW      double precision NOT NULL,
    exact_target_soc        boolean NOT NULL DEFAULT false,
    CONSTRAINT fleet_pkey PRIMARY KEY (vehicle_id)
);

CREATE TABLE IF NOT EXISTS public.day_ahead_market_prices (
    "timestamp"             timestamp without time zone NOT NULL,
    price_eur_per_kwh       double precision NOT NULL,
    CONSTRAINT day_ahead_market_prices_pkey PRIMARY KEY ("timestamp")
);

CREATE TABLE IF NOT EXISTS public.cluster_forecast (
    cluster_id              character varying(100) COLLATE pg_catalog."default" NOT NULL,
    "timestamp"             timestamp without time zone NOT NULL,
    downward_capability_kW  double precision NOT NULL DEFAULT 0,
    upward_capability_kW    double precision NOT NULL DEFAULT 0,
    connected_evs           integer NOT NULL DEFAULT 0,
    cluster_power_kW        double precision NOT NULL DEFAULT 0,
    CONSTRAINT cluster_forecast_pkey PRIMARY KEY (cluster_id, "timestamp")
);

CREATE TABLE IF NOT EXISTS public.charging_schedule (
    vehicle_id                  character varying(100) COLLATE pg_catalog."default" NOT NULL,
    cluster_id                  character varying(100) COLLATE pg_catalog."default" NOT NULL,
    "arrival_time_ts"           timestamp without time zone NOT NULL,
    "departure_time_ts"         timestamp without time zone NOT NULL,
    initial_soc                 double precision NOT NULL,
    target_soc                  double precision NOT NULL,
    scheduled_departure_soc     double precision NOT NULL,
    charged_energy_kWh          double precision NOT NULL,
    total_charging_cost_eur     double precision NOT NULL,
    CONSTRAINT charging_schedule_pkey PRIMARY KEY (vehicle_id, cluster_id, "arrival_time_ts")
);

END;
