import pandas as pd

from utils.flex_command_utils import (
    generate_midpoint_flex_band_commands,
    generate_midpoint_setpoint_commands,
    validate_stage2_commands,
    validate_setpoint_commands,
)


def _sample_envelope():
    index = pd.date_range("2024-01-01 08:00", periods=3, freq="15min")
    return pd.DataFrame(
        {
            "downward_capability_kW": [10.0, 8.0, 6.0],
            "upward_capability_kW": [4.0, 4.0, 2.0],
        },
        index=index,
    )


def test_generate_midpoint_setpoint_commands_returns_in_envelope_values():
    envelope = _sample_envelope()
    commands = generate_midpoint_setpoint_commands({"1": envelope})

    expected = pd.Series([3.0, 2.0, 2.0], index=envelope.index)
    pd.testing.assert_series_equal(commands["1"], expected, check_names=False)


def test_generate_midpoint_flex_band_commands_returns_degenerate_bands():
    envelope = _sample_envelope()
    commands = generate_midpoint_flex_band_commands({"1": envelope})

    expected_midpoint = pd.Series([3.0, 2.0, 2.0], index=envelope.index)
    pd.testing.assert_series_equal(
        commands["1"]["p_min_kw"], expected_midpoint, check_names=False
    )
    pd.testing.assert_series_equal(
        commands["1"]["p_max_kw"], expected_midpoint, check_names=False
    )


def test_validate_setpoint_commands_accepts_valid_command():
    envelope = _sample_envelope()
    command = pd.Series([0.0, 1.0, 2.0], index=envelope.index)

    accepted, status = validate_setpoint_commands({"1": command}, {"1": envelope})

    assert "1" in accepted
    assert status.loc[0, "status"] == "accepted"


def test_validate_setpoint_commands_rejects_out_of_envelope_command():
    envelope = _sample_envelope()
    # Last value exceeds upper bound (6.0).
    command = pd.Series([0.0, 1.0, 6.5], index=envelope.index)

    accepted, status = validate_setpoint_commands({"1": command}, {"1": envelope})

    assert "1" not in accepted
    row = status.loc[status["cluster_id"] == "1"].iloc[0]
    assert row["status"] == "rejected"
    assert row["reason"] == "OUT_OF_ENVELOPE"


def test_validate_stage2_commands_allows_out_of_envelope_when_not_enforced():
    envelope = _sample_envelope()
    command = pd.Series([0.0, 1.0, 6.5], index=envelope.index)

    accepted, status = validate_stage2_commands(
        {"1": command},
        {"1": envelope},
        command_type="absolute_setpoint",
        enforce_envelope=False,
    )

    assert "1" in accepted
    row = status.loc[status["cluster_id"] == "1"].iloc[0]
    assert row["status"] == "accepted"


def test_validate_setpoint_commands_rejects_timestep_mismatch():
    envelope = _sample_envelope()
    command = pd.Series([0.0, 1.0], index=envelope.index[:2])

    accepted, status = validate_setpoint_commands({"1": command}, {"1": envelope})

    assert "1" not in accepted
    row = status.loc[status["cluster_id"] == "1"].iloc[0]
    assert row["status"] == "rejected"
    assert row["reason"] == "TIMESTEP_MISMATCH"


def test_validate_stage2_commands_accepts_valid_flex_band():
    envelope = _sample_envelope()
    band = pd.DataFrame(
        {
            "p_min_kw": [-3.0, -2.5, -1.0],
            "p_max_kw": [4.0, 4.0, 3.0],
        },
        index=envelope.index,
    )

    accepted, status = validate_stage2_commands(
        {"1": band},
        {"1": envelope},
        command_type="flex_band",
    )

    assert "1" in accepted
    assert status.loc[0, "status"] == "accepted"
    assert set(accepted["1"].columns) == {"p_min_kw", "p_max_kw"}


def test_validate_stage2_commands_rejects_invalid_flex_band_range():
    envelope = _sample_envelope()
    band = pd.DataFrame(
        {
            "p_min_kw": [0.0, 2.0, -1.0],
            "p_max_kw": [1.0, 1.0, 2.0],  # second row invalid: min > max
        },
        index=envelope.index,
    )

    accepted, status = validate_stage2_commands(
        {"1": band},
        {"1": envelope},
        command_type="flex_band",
    )

    assert "1" not in accepted
    row = status.loc[status["cluster_id"] == "1"].iloc[0]
    assert row["status"] == "rejected"
    assert row["reason"] == "INVALID_BAND_RANGE"


def test_validate_stage2_commands_rejects_p_set_column_for_flex_band():
    envelope = _sample_envelope()
    band = pd.DataFrame(
        {
            "p_min_kw": [-3.0, -2.5, -1.0],
            "p_max_kw": [4.0, 4.0, 3.0],
            "p_set_kw": [0.0, 0.0, 0.0],
        },
        index=envelope.index,
    )

    accepted, status = validate_stage2_commands(
        {"1": band},
        {"1": envelope},
        command_type="flex_band",
    )

    assert "1" not in accepted
    row = status.loc[status["cluster_id"] == "1"].iloc[0]
    assert row["status"] == "rejected"
    assert row["reason"] == "INVALID_COMMAND_SCHEMA"
