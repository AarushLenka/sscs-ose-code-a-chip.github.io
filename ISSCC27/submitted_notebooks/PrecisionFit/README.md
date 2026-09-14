# PrecisionFit: Error-Budget-Driven FIR Filter Hardware Generator

**Licensed under the Apache License, Version 2.0** — see [`LICENSE`](LICENSE).
The generated RTL, result CSVs and figures are released under the same terms.

**The submission artifact is [`notebooks/precisionfit.ipynb`](notebooks/precisionfit.ipynb).**
The material in `src/` is the development/verification layer the notebook calls
into; the notebook alone tells the whole story and can be read top to bottom
without opening any `.py` file.

---

## What this is

A 17-tap symmetric lowpass FIR for 48 kHz audio, taken from a floating-point
reference all the way to synthesized Verilog, asking one question:

> **Where can bits be removed without breaking the application's requirements —
> and does allocating bits by per-tap sensitivity beat allocating them
> uniformly?**

Every candidate design is (a) evaluated against an explicit **error budget**
(RMS ≤ 1e-3, SNR ≥ 60 dB vs. the float reference) *and* the filter's frequency
spec (0.5 dB ripple, 40 dB attenuation), (b) **proven bit-exact against a
bit-accurate fixed-point model** in RTL simulation, and (c) **synthesized with
Yosys** so the area axis is measured rather than assumed.

## Headline result (Filter A)

| design | synthesized cells | RMS error | SNR |
|---|---|---|---|
| Conservative uniform (widest passing) | 17,819 | 3.69e-5 | 76.6 dB |
| Best uniform (narrowest passing) | 11,466 | 2.21e-4 | 61.0 dB |
| **Sensitivity-guided (per-tap)** | **11,048** | 2.31e-4 | 60.7 dB |

Sensitivity-guided allocation lands **3.6% smaller** than the best uniform
design at a **4.2% higher RMS error** — i.e. the two strategies sit on
essentially the *same* accuracy–area frontier, exchanging a little accuracy for
a little area. **This is not a clean win and is not reported as one.**

The reason is measured, not guessed: the per-tap sensitivity spread is only
**1.5×**, so there is little for a non-uniform allocation to reallocate. The
Filter B generalization test (a different spec, different test-signal seed)
flips the sign of that small difference (−4.2% cells), confirming that the
effect is within sweep noise. Both outcomes are reported.

## Correctness

The project's central discipline is that **no RTL result is trusted unless it
matches the golden model bit-for-bit**, as raw integer bit patterns, on
impulse / step / full-scale chirp / two noise types / multitone / an explicit
overflow-stress signal. Both the uniform and the per-tap (per-coefficient
width) paths pass this gate, including on the three headline designs.

Two real, silent bugs were found by that gate during bring-up and are documented
in the notebook (section 5):

1. `shifted = rounded >>> SHIFT` assigned into a narrower wire truncates the
   *sign-fill*, not the result — the generator selects the correct upper bits
   instead.
2. The testbench's pipeline alignment (one leading zero-padding sample) was
   established by **measurement** against an impulse response, not assumed.

## Repository layout

```
precisionfit/
├── LICENSE                       Apache-2.0
├── README.md
├── env/install_tools.sh          environment setup + toolchain smoke test
├── notebooks/
│   ├── precisionfit.ipynb        
│   └── build_notebook.py         regenerates/tests the notebook from cells
├── src/python/
│   ├── paths.py                  central path resolution (cwd-independent)
│   ├── reference.py              float64 reference filter (A + B), test signals
│   ├── fixedpoint.py             bit-accurate golden model (uniform + per-tap)
│   ├── metrics.py                error stats, spec margins, analytical bounds
│   ├── sanity_check.py           convergence + path-equivalence checks
│   ├── rtlgen.py                 Verilog generator (uniform + per-tap widths)
│   ├── verify_rtl.py             BIT-EXACT RTL-vs-model correctness gate
│   ├── synth_yosys.py            Yosys wrapper, parses cell-count JSON
│   ├── search.py                 uniform precision sweep
│   ├── sweep_with_synth.py       uniform sweep + synthesis (Filter A)
│   ├── sensitivity.py            per-tap sensitivity (both methods) + allocation
│   ├── sensitivity_search.py     sensitivity-guided sweep + synthesis
│   ├── build_comparison.py       three-way table + Pareto plot
│   ├── final_stress_test.py      stress set + empirical-vs-analytical separation
│   ├── generalization_filter_b.py Filter B generalization test
│   └── parse_openlane_results.py OpenLane metrics parser (Week 4, optional)
├── src/verilog/
│   ├── fir_symmetric.v.j2        the single RTL template (per-tap capable)
│   ├── fir_nonuniform.v.j2       thin include wrapper (same template, no drift)
│   └── rtl/                      generated .v files (regenerated on demand)
├── src/tb/
│   ├── fir_tb.cpp                Verilator harness (fast path)
│   ├── fir_tb.v.j2               Icarus harness template (fallback path)
│   ├── tb_utils.py               vector I/O + testbench rendering
│   └── build_and_run.sh          builds/runs, auto-selecting the backend
├── synth/
│   ├── yosys_synth.tcl           generic-cell synthesis (used by the sweeps)
│   ├── sky130.tcl                OpenROAD/SKY130 flow (Week 4, optional)
│   ├── openlane_config.json      OpenLane2 config for the 3 headline designs
│   └── constraints.sdc           shared timing constraint (Week 4, optional)
├── results/
│   ├── sweeps/                   sweep CSVs (+ Filter B)
│   └── pareto/                   three-way tables, Pareto plots, stress results
└── report/                       (writeup/plots for submission)
```

## Reproducing everything

```bash
bash env/install_tools.sh
source venv/bin/activate

# correctness first — both must pass before any result means anything
python src/python/sanity_check.py     # model converges, all code paths agree
python src/python/verify_rtl.py       # generated RTL is bit-exact vs the model

# the sweep stages that produce the committed CSVs
python src/python/sweep_with_synth.py          # uniform + synthesis   (~2 min)
python src/python/sensitivity_search.py        # sensitivity-guided    (~1 min)
python src/python/build_comparison.py          # three-way table + Pareto plot
python src/python/final_stress_test.py         # stress + bit-exactness on the 3 winners
python src/python/generalization_filter_b.py   # Filter B              (~4 min)

# the deliverable
python notebooks/build_notebook.py --execute   # rebuild + execute the notebook
jupyter lab notebooks/precisionfit.ipynb       # or Kernel -> Restart & Run All
```

Scripts resolve all paths from their own location (`src/python/paths.py`), so
they work from any working directory. Generated Verilog is written into
`src/verilog/rtl/` and is deterministic from its configuration — the sweeps'
intermediate netlists are not committed, but the CSVs that record their results
are.

### Simulation backend

`src/tb/build_and_run.sh` prefers **Verilator** (fast) and automatically falls
back to **Icarus Verilog**. Both harnesses implement the same stimulus
protocol, so the bit-exact verdict is identical either way; only wall-clock
time differs. The notebook was executed with Icarus Verilog 12 (Verilator was
not available in that environment).

## Scope and honest limitations

* **Power/energy was not measured.** No switching activity was annotated, so no
  energy claim is made anywhere, and area savings are *not* claimed to imply
  energy savings.
* **Area is a relative metric**: synthesized generic-cell count
  (`synth` + `abc -g cmos2`), consistent across candidates but not µm². The
  full SKY130/OpenROAD flow is scaffolded in `synth/` and explicitly **not
  attempted** — the Code-a-Chip program marks final layout as encouraged but
  not required. The notebook says so in section 9 rather than leaving a gap.
* **No timing analysis** was run; pipeline depth is identical across candidates
  by construction, which is a structural fairness argument rather than a
  timing number.
* **One architecture** (direct-form, symmetric-folded, fully parallel, fixed
  pipeline). Transposed-form, folded or time-multiplexed variants could land
  elsewhere.
* The guide's finite-difference *margin* sensitivity ranking is **ε-unstable**
  (11–44% rank agreement). Allocation therefore uses an exact, spec-weighted
  response-influence measure; both are implemented, compared and reported.
* "Best" and "conservative" uniform are **sweep results bounded by the swept
  ranges**, not global optima.
* Filter A's transition band was widened from the original plan (4→7 kHz to
  4→10 kHz) because the original spec is infeasible at 17 taps; the deviation
  and its reason are stated in the notebook (section 2).

## Scope statement

This is a complete submission at the "synthesized generic-cell" fidelity level:
rock-solid RTL generation, bit-exact verification against a validated golden
model, real synthesized area numbers, a three-way comparison, stress tests with
analytical bounds kept separate from empirical measurements, and a
generalization test — all reproducible with open-source tools only.
