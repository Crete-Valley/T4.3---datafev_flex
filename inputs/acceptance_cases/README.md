# Acceptance Input Matrix

This directory contains deterministic Stage-1 and Stage-2 test fixtures used by
`docs/acceptance_test_checklist.md`.

## Generate / Refresh

```bash
venv/bin/python scripts/generate_acceptance_inputs.py
```

## Rollback / Remove

```bash
venv/bin/python scripts/generate_acceptance_inputs.py --clean
```

## Contents

- Stage-1 fixtures:
  - `stage1_baseline_multi_cluster.xlsx`
  - `stage1_single_cluster_short_horizon.xlsx`
  - `stage1_no_v2g_cluster2.xlsx`
- Stage-2 absolute fixtures:
  - `stage2_absolute_accept_baseline.xlsx`
  - `stage2_absolute_out_of_envelope.xlsx`
  - `stage2_absolute_timestep_mismatch.xlsx`
  - `stage2_absolute_unknown_cluster.xlsx`
- Stage-2 flex-band fixtures:
  - `stage2_flex_band_accept_baseline.xlsx`
  - `stage2_flex_band_out_of_envelope.xlsx`
  - `stage2_flex_band_invalid_range.xlsx`

For expected outcomes and execution examples, use `manifest.csv` in this
directory and the acceptance checklist document.

The same fixture set is also used for `best_effort` Stage-2 checks (for
example with `stage2_absolute_out_of_envelope.xlsx`).

## One-Command Run

```bash
scripts/run_acceptance_matrix.sh
```
