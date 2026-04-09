import run_local_workflow


def test_resolve_stage2_profile_supports_stress_samples():
    absolute_profile = run_local_workflow._resolve_stage2_profile(
        "absolute_from_file_stress"
    )
    flex_profile = run_local_workflow._resolve_stage2_profile(
        "flex_band_from_file_stress"
    )

    assert absolute_profile["command_type"] == "absolute_setpoint"
    assert absolute_profile["setpoints_file"].endswith(
        "stage2_sample_absolute_setpoints_stress.xlsx"
    )
    assert flex_profile["command_type"] == "flex_band"
    assert flex_profile["setpoints_file"].endswith(
        "stage2_sample_flex_band_commands_stress.xlsx"
    )


def test_describe_stage2_objective_for_best_effort():
    description = run_local_workflow._describe_stage2_objective(
        command_type="absolute_setpoint",
        tracking_mode="best_effort",
    )

    assert "tracking deviation" in description
    assert "charging cost" in description


def test_describe_stage2_objective_for_strict_flex_band():
    description = run_local_workflow._describe_stage2_objective(
        command_type="flex_band",
        tracking_mode="strict",
    )

    assert "flex-band" in description
    assert "cheaper timesteps" in description


def test_describe_stage2_objective_for_strict_absolute():
    description = run_local_workflow._describe_stage2_objective(
        command_type="absolute_setpoint",
        tracking_mode="strict",
    )

    assert "aggregate power is fixed" in description
    assert "cycling" in description
