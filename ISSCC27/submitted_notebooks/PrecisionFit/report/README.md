# Submission figures and tables

This directory holds the figures/tables quoted in the notebook, copied here for
convenience when writing up the submission. The notebook is the authoritative
artifact; everything here is regenerated from it (or from the scripts it calls).

| file | content |
|---|---|
| `fig5_filter_a_pareto_frontier.png` | Filter A accuracy-area frontier, uniform vs. sensitivity-guided |
| `fig6_filter_b_pareto_frontier.png` | Filter B generalization test, same comparison |
| `three_way_comparison.csv` | the three Filter A headline designs (area, RMS error, SNR) |
| `filter_b_three_way_comparison.csv` | the same table for Filter B |

Also relevant (written directly by the scripts into `../results/`):

* `../results/pareto/stress_test.csv` — per-signal empirical error and the
  separate analytical worst-case bound for all three headline designs
* `../results/sweeps/*_synth.csv` — every accuracy-passing configuration with
  its synthesized cell count (the raw data behind both frontier plots)

**Power/energy is not measured anywhere in this project** — see section 9 and
section 12 of the notebook.
