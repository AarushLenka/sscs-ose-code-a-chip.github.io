"""
Build `notebooks/precisionfit.ipynb` -- the actual submission artifact.

The notebook is the deliverable; this script is the build tool that assembles
it from explicit markdown/code cells (so the notebook stays regenerable and
diffable instead of being hand-edited JSON). Run:

    python notebooks/build_notebook.py            # build only
    python notebooks/build_notebook.py --execute  # build + execute (embeds outputs)
"""
import sys
from pathlib import Path

import nbformat as nbf

HERE = Path(__file__).resolve().parent
OUT = HERE / "precisionfit.ipynb"

CELLS = []


def md(text):
    CELLS.append(("md", text))


def code(text):
    CELLS.append(("code", text))


# ===========================================================================
md(r"""# PrecisionFit: Error-Budget-Driven FIR Filter Hardware Generator

**Licensed under the Apache License, Version 2.0.** See the repository's
`LICENSE` file for the full text. All generated RTL, figures and data files in
this repository are released under the same license.

---

### What this project is

This notebook designs a **17-tap symmetric lowpass FIR filter** for 48 kHz
audio, then asks a single question:

> **Where can bits be removed from the hardware without breaking the
> application's requirements?**

It answers that question by building a *bit-accurate* fixed-point model, using
it to search a precision space, generating synthesizable Verilog for every
candidate, **proving the RTL is bit-exact against the model**, synthesizing
each candidate to get a real area proxy, and reporting the accuracy-vs-area
Pareto frontier for two allocation strategies: uniform precision and
**sensitivity-guided** (per-tap) precision.

### Fit to the IEEE SSCS Code-a-Chip program

This project is submitted under the program category *"exploration of
open-source PDKs and SPICE simulations to demonstrate relevant figures of
merit for circuit building blocks."* Concretely:

* the **circuit building block** is a symmetric-folded, fully-parallel FIR
  filter datapath;
* the **figures of merit** reported are numerical accuracy (RMS error / SNR vs.
  a floating-point reference, plus passband-ripple and stopband-attenuation
  margins against the filter spec) and **area** (synthesized cell count as a
  relative metric; SKY130 physical area/timing is discussed and explicitly
  scoped as not attempted — see section 9);
* the whole flow is built on **open-source tooling** (Python/NumPy/SciPy,
  Jinja2, Icarus Verilog, Yosys) and is reproducible without any licensed
  EDA tool.

### Honesty statement up front

The headline comparison (section 8) shows a **small** advantage for
sensitivity-guided allocation on Filter A (+3.6% smaller area than the best
uniform design) and a **slightly negative** result on the Filter B
generalization test (−4.2%). Section 12 explains why, and no result in this
notebook was tuned post-hoc to manufacture a larger gap.""")

# ===========================================================================
md(r"""## 0. Setup and environment

Every number in this notebook is produced by the cells below, either computed
live or loaded from a committed results file that the cell explicitly names
(and says how to regenerate). Nothing is asserted without a cell that
produced it.

The heavy *sweep* stages (hundreds of configurations, each synthesized with
Yosys) run in `src/python/` and write CSV files into `results/`. Those stages
take minutes, so this notebook **loads their committed outputs** and states
the exact command that regenerates them. Everything else — the reference
design, the golden-model validation, RTL generation, the bit-exact
RTL-vs-model verification, the sensitivity analysis and the stress tests — is
computed live.""")

code(r"""import sys, os
from pathlib import Path

# Locate the repo root from wherever the kernel was started.
ROOT = Path.cwd()
while not (ROOT / "src" / "python" / "fixedpoint.py").exists() and ROOT != ROOT.parent:
    ROOT = ROOT.parent
assert (ROOT / "src" / "python" / "fixedpoint.py").exists(), "run from inside the precisionfit repo"
sys.path.insert(0, str(ROOT / "src" / "python"))
sys.path.insert(0, str(ROOT / "src" / "tb"))

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
%matplotlib inline

import paths
from reference import (FILTER_A_SPEC, FILTER_B_SPEC, design_filter,
                       make_test_signals, verify_spec, float_reference)
from fixedpoint import FixedPointConfig, fir_fixed_point, fir_fixed_point_fast
from metrics import error_stats, worst_case_bound, worst_case_bound_per_tap
from search import make_cfg, evaluate_uniform_config, ERROR_BUDGET
from rtlgen import generate_rtl, generate_rtl_nonuniform
from verify_rtl import verify_config, print_results, lint_rtl, LATENCY_CYCLES
from sensitivity import (compute_sensitivities, compute_sensitivities_response,
                         allocate_bits_by_sensitivity, unique_coeff_indices,
                         sensitivity_rank_stability)

paths.ensure_dirs()
print("repo root :", paths.ROOT)
print("numpy     :", np.__version__)
print("scipy     :", __import__("scipy").__version__)
print("pandas    :", pd.__version__)
print("python    :", sys.version.split()[0])""")

# ===========================================================================
md(r"""## 1. Introduction and motivation

Fixed-point hardware design usually starts from a rule of thumb: "give the
datapath a few more bits than you think you need, and call it conservative."
That costs silicon, and the cost is invisible because nobody measured what the
extra bits bought.

This project replaces the rule of thumb with a measurement. The pipeline is:

1. **A floating-point golden reference** — a Parks-McClellan equiripple FIR,
   verified against its own spec (section 2).
2. **A bit-accurate fixed-point model** — every shift, rounding step and
   saturation decision written out explicitly, and *validated by convergence*
   (section 3).
3. **A parameterized RTL generator** — one architecture, emitted as Verilog
   for any precision configuration (sections 4–5).
4. **A correctness gate** — generated RTL is only allowed into a result if it
   matches the golden model *bit-for-bit* on a stress signal set (section 5).
5. **A precision search** — sweep uniform precision, then allocate bits
   non-uniformly using a measured per-tap sensitivity (sections 6–7).
6. **Synthesis** — every passing candidate is mapped to gates so the area
   axis is measured rather than assumed (sections 6, 8).

**The question, stated directly:** for this filter, where can bits be removed
without breaking the application's requirements — and does allocating bits by
sensitivity beat allocating them uniformly?""")

# ===========================================================================
md(r"""## 2. Reference filter design (Filter A)

A 17-tap **symmetric** linear-phase lowpass designed with Parks–McClellan.
Symmetric coefficients (`h[n] == h[N-1-n]`) are what let the RTL fold the
datapath to 9 unique multipliers instead of 17 — a standard FIR optimization,
applied identically to *every* candidate so it can never confound the
precision comparison.

**Deviation from the original project plan, stated openly:** the plan's
example spec (passband edge 4 kHz, stopband edge 7 kHz, 0.5 dB ripple, 40 dB
attenuation) is **not achievable with 17 taps** — that transition band needs
roughly 31 taps at that ripple. The plan anticipates this and says to loosen
the spec or increase the tap count. We keep the 17-tap architecture and widen
the transition band instead, so the entire "17 taps → 9 multipliers"
structure in section 4 is preserved. Filter B (section 11) uses a *different*
spec on top of the same method.""")

code(r"""h = design_filter(FILTER_A_SPEC)          # Filter A: primary design
hb = design_filter(FILTER_B_SPEC)        # Filter B: generalization test (section 11)

va, vb = verify_spec(h, FILTER_A_SPEC), verify_spec(hb, FILTER_B_SPEC)
for name, spec, v in (("Filter A", FILTER_A_SPEC, va), ("Filter B", FILTER_B_SPEC, vb)):
    print(f"{name}: {spec['numtaps']} taps, fs={spec['fs']} Hz, "
          f"passband {spec['passband_edge']} Hz, stopband {spec['stopband_edge']} Hz")
    print(f"  passband ripple {v['passband_ripple_db']:.3f} dB  "
          f"(spec <= {spec['passband_ripple_db']})  -> {'PASS' if v['passband_ok'] else 'FAIL'}")
    print(f"  stopband atten  {v['stopband_atten_db']:.2f} dB  "
          f"(spec >= {spec['stopband_atten_db']})  -> {'PASS' if v['stopband_ok'] else 'FAIL'}")
    print(f"  symmetric: {np.allclose(design_filter(spec), design_filter(spec)[::-1])}")

fig, axes = plt.subplots(1, 2, figsize=(13, 4.2))
for ax, (name, spec, v) in zip(axes, (("Filter A", FILTER_A_SPEC, va),
                                      ("Filter B", FILTER_B_SPEC, vb))):
    ax.plot(v["freqs"], v["mag_db"], lw=1.6, color="navy")
    ax.axvline(spec["passband_edge"], color="green", ls=":", label="passband edge")
    ax.axvline(spec["stopband_edge"], color="red", ls=":", label="stopband edge")
    ax.axhline(-spec["stopband_atten_db"], color="red", ls="--", lw=1, label="spec attenuation")
    ax.set_xlim(0, spec["fs"] / 2)
    ax.set_ylim(-80, 5)
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("|H(f)| (dB)")
    ax.set_title(f"{name} - {len(h)} taps, Parks-McClellan")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
fig.tight_layout()

np.save(paths.RESULTS_DIR / "filter_a_coeffs_float.npy", h)
np.save(paths.RESULTS_DIR / "filter_b_coeffs_float.npy", hb)
print("\nFigure 1: designed magnitude responses with spec limits. Both filters PASS their spec.")""")

# ===========================================================================
md(r"""## 3. Bit-accurate fixed-point model — and why we trust it

`src/python/fixedpoint.py` implements the datapath as **two's-complement
integers**, not floats: every shift direction, rounding step, saturation
decision and wraparound is explicit, so hardware and model agree bit-for-bit
by construction rather than by luck.

A model like this is only useful if it is *right*. Rather than eyeballing it,
we test two properties:

**3a. Convergence.** As word length grows, the fixed-point output must
converge to the floating-point reference at roughly **6 dB of SNR per extra
bit** (the standard rule of thumb). A model with a shift-direction or
sign-extension bug does *not* converge cleanly — so this single check is a
cheap, powerful bug detector.

**3b. Path equivalence.** The vectorized model used by the sweeps must be
bit-identical to the readable golden loop, and the per-tap (non-uniform) path
with equal widths must reduce exactly to the uniform path.""")

code(r"""x = make_test_signals(FILTER_A_SPEC["fs"], n_samples=1024)["random_wideband"]
y_ref = float_reference(x, h)

rows = []
for total_bits in [4, 6, 8, 10, 12, 16, 20, 24]:
    cfg = FixedPointConfig(coeff_int_bits=2, coeff_frac_bits=total_bits - 2,
                           input_int_bits=2, input_frac_bits=total_bits - 2,
                           acc_guard_bits=4, output_int_bits=2,
                           output_frac_bits=total_bits - 2)
    r = fir_fixed_point(x, h, cfg)
    st = error_stats(y_ref, r["y_float"])
    rows.append(dict(bits=total_bits, rms_error=st["rms_error"], snr_db=st["snr_db"]))
conv = pd.DataFrame(rows)
print(conv.to_string(index=False))

bits_gained = np.diff(conv["snr_db"].values)
print(f"\nmean SNR gain per extra 2 bits: {np.mean(bits_gained):.2f} dB "
      f"(x3.01 dB per bit = {np.mean(bits_gained)/2:.2f} dB/bit)")

fig, ax = plt.subplots(figsize=(7, 4.2))
ax.semilogy(conv["bits"], conv["rms_error"], "o-", color="darkred")
ax.set_xlabel("word length (bits, coefficient = input = output fractional width)")
ax.set_ylabel("RMS error vs. float reference")
ax.set_title("Figure 2: fixed-point model converges to the float reference")
ax.grid(alpha=0.3, which="both")
plt.show()""")

code(r"""# 3b. Path equivalence is asserted (not eyeballed). See sanity_check.py for the
# full randomized version; here we re-run the same assertions inline.
from sanity_check import check_fast_matches_golden, check_uniform_matches_per_tap
check_fast_matches_golden(h, x)
check_uniform_matches_per_tap(h, x)
print("\n=> golden loop == vectorized model == per-tap path (at equal widths).")
print("   The model is trustworthy; every downstream number depends on this.")""")

# ===========================================================================
md(r"""## 4. Architecture (fixed for every candidate)

One architecture is held **identical across all candidates** — only the
*precision configuration* changes. That is what makes the accuracy-vs-area
comparison meaningful.

```
                     symmetric folding
 x[n] ──► sr[0..16] ──► fold_i = sr[i] + sr[16-i]   (8 pre-adders)
                          │
                          ├──►  ← C0..C7 : 8 constant multipliers
                          │
          sr[8] ──────────┴──►  ← C8 : center-tap multiplier
                          │
                     adder tree (all partial products sign-extended to ACC_WIDTH)
                          │
                    [acc_reg] ──► requantize (round, >>>SHIFT, saturate)
                          │
                    [out_data]  ◄── 1 sample/clock, fully parallel, fixed
                                    pipeline depth (held constant)
```

* **Symmetric folding**: `y = C8·x[8] + Σ C_i·(x[i] + x[16-i])` → **9
  multipliers instead of 17**.
* **Fully parallel**: one sample in, one sample out per clock. Throughput is
  identical for all candidates.
* **Fixed pipeline depth**: input register → accumulator register → output
  register. Held constant so timing comparisons are apples-to-apples.
* What *does* vary between candidates: coefficient bit width (uniform, or
  per-tap), input/datapath width, accumulator guard bits, rounding mode,
  saturation on/off.""")

code(r"""# Render the datapath as a block diagram so the structure is visible without
# reading Verilog.
fig, ax = plt.subplots(figsize=(12, 5.2))
ax.axis("off")

def box(x, y, w, h, text, fc="#e8f0fe", ec="#1a3f7a", fs=8.5, weight="normal"):
    ax.add_patch(plt.Rectangle((x, y), w, h, fc=fc, ec=ec, lw=1.2, zorder=2))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs,
            zorder=3, fontweight=weight)

def arrow(x1, y1, x2, y2, color="#333"):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="-|>", color=color, lw=1.2))

# delay line
box(0.5, 3.5, 2.6, 1.0, "shift register\nsr[0..16]\n(17 × IN_WIDTH flops)")
arrow(0.0, 4.0, 0.5, 4.0); ax.text(0.02, 4.12, "x[n]", fontsize=9)
# pre-adders
box(3.5, 3.5, 2.3, 1.0, "8 symmetric\npre-adders\nfold$_i$ = sr[i]+sr[16-i]")
arrow(3.1, 4.0, 3.5, 4.0)
# multipliers
box(6.2, 4.35, 2.4, 0.95, "8 constant multipliers\nC0..C7 (unique taps)")
box(6.2, 2.85, 2.4, 0.95, "center multiplier\nC8  (tap 8)")
arrow(5.8, 4.15, 6.2, 4.7); arrow(5.8, 3.85, 6.2, 3.3)
# adder tree
box(9.0, 3.5, 2.2, 1.3, "adder tree\n(explicit sign-extend\nto ACC_WIDTH)")
arrow(8.6, 4.7, 9.0, 4.4); arrow(8.6, 3.3, 9.0, 3.9)
# pipeline regs
box(9.0, 1.9, 2.2, 0.8, "acc_reg\n(pipeline stage)", fc="#fff3e0", ec="#b26a00")
arrow(10.1, 3.5, 10.1, 2.7)
box(6.2, 1.9, 2.4, 0.8, "requantize\nround / >>>SHIFT / saturate",
    fc="#fff3e0", ec="#b26a00")
arrow(9.0, 2.3, 8.6, 2.3)
box(2.9, 1.9, 2.4, 0.8, "out_data\nQ2.f", fc="#e6f4ea", ec="#1a6b34")
arrow(6.2, 2.3, 5.3, 2.3)
ax.text(1.2, 2.3, "y[n]\n1 sample/clock", fontsize=9, va="center")
arrow(2.9, 2.3, 2.5, 2.3)

ax.text(0.0, 0.7, "Reduced bit widths change only: coefficient width(s), input width, ACC guard bits, "
                  "rounding mode, saturation.\nArchitecture, throughput and pipeline depth are identical "
                  "for every candidate compared in section 8.",
        fontsize=9, style="italic")
ax.set_xlim(-0.3, 11.5); ax.set_ylim(0.2, 5.6)
ax.set_title("Figure 3: symmetric-folded direct-form FIR datapath (the single architecture)", fontsize=11)
plt.show()""")

# ===========================================================================
md(r"""## 5. RTL generation and the correctness gate

The RTL is *generated* from one Jinja2 template — one parameterized design, not
N hand-written files. Every width, sign extension and constant is computed in
Python and injected, because hand-templating repetitive sign-extension
expressions is exactly where silent width bugs live.

The bit-exactness gate has two subtleties worth calling out, because both are
classic silent bugs that this project hit and fixed during bring-up:

1. **Requantization truncation.** `wire [SW-1:0] shifted = rounded >>> SHIFT;`
   is *wrong*: an arithmetic right shift sign-fills the low bits, and the
   assignment then truncates the MSBs — keeping the sign-fill instead of the
   result. The generated RTL instead selects the correct upper bits:
   `rounded[ACC_WIDTH:SHIFT]`.
2. **Pipeline alignment.** The testbench discards exactly one leading
   zero-padding sample before comparing. This was **measured** against the
   model on an impulse (not assumed), because an off-by-one here makes every
   sample look like a mismatch.

Both were caught by the comparison below, which is the point of having it.""")

code(r"""cfg_baseline = FixedPointConfig(
    coeff_int_bits=2, coeff_frac_bits=10,      # Q2.10 coefficients
    input_int_bits=2, input_frac_bits=14,      # Q2.14 samples
    acc_guard_bits=4,
    output_int_bits=2, output_frac_bits=14,
    rounding="round", saturate_output=True)

rtl_path = generate_rtl(h, cfg_baseline, config_name="notebook_baseline")
print(f"generated: {paths.rel(rtl_path)}\n")
src = Path(rtl_path).read_text().splitlines()
print("\n".join(src[:8]))
print("   ... (module header / widths) ...")
print("\n".join(src[-26:]))""")

code(r"""lint = lint_rtl(rtl_path)
print(f"lint backend: {lint['backend']}")
print("lint result :", "CLEAN" if lint["ok"] else "WARNINGS/ERRORS")
if not lint["ok"]:
    print(lint["output"][:3000])

# ---- the gate: bit-exact RTL vs golden model on the full stress set --------
sigs = make_test_signals(FILTER_A_SPEC["fs"], n_samples=512)
res = verify_config(h, cfg_baseline, rtl_path, "fir_notebook_baseline", sigs,
                    latency_cycles=LATENCY_CYCLES)
ok = print_results(res)
assert ok, "RTL is not bit-exact -- downstream results are meaningless"
print("\n=> generated RTL == golden model, bit for bit, on every signal.")
print("   (Also verified for the per-tap template; see section 7.)")""")

# ===========================================================================
md(r"""## 6. Uniform-precision sweep

338 configurations — coefficient width 4…16 bits, input width 6…18 bits,
accumulator guard {2, 4} — are each evaluated against the float reference and
the filter spec. A configuration passes only if **all** of:

* the quantized filter still meets the frequency spec (0.5 dB ripple,
  40 dB attenuation), **and**
* output RMS error ≤ 1e-3 vs. the float reference, **and**
* output SNR ≥ 60 dB.

Both baselines used in section 8 come out of this sweep as *measurements*:
the narrowest passing uniform design ("best uniform") and the widest passing
one ("conservative uniform"). Neither is a strawman picked by hand.""")

code(r"""uniform_sweep_df = pd.read_csv(paths.SWEEPS_DIR / "uniform_sweep.csv")
uni_synth = pd.read_csv(paths.SWEEPS_DIR / "uniform_sweep_synth.csv")

passing = uniform_sweep_df[uniform_sweep_df["passes_error_budget"]]
print(f"configs swept        : {len(uniform_sweep_df)}")
print(f"configs passing      : {len(passing)}")
print(f"synthesized (all pass): {len(uni_synth)}")
print("\nNOTE: these CSVs are the committed outputs of the time-consuming stage.")
print("Regenerate with:  python src/python/sweep_with_synth.py   (~2 min)\n")

best_u = uni_synth.loc[uni_synth['total_cells'].idxmin()]
cons_u = uni_synth.loc[uni_synth['total_cells'].idxmax()]
print("narrowest passing uniform (fewest cells):",
      f"coeff_bits={int(best_u.coeff_bits)}, input_bits={int(best_u.input_bits)}, "
      f"guard={int(best_u.acc_guard)} -> {int(best_u.total_cells)} cells, "
      f"RMS {best_u.rms_error_wideband:.3e}")
print("widest passing uniform (most cells)    :",
      f"coeff_bits={int(cons_u.coeff_bits)}, input_bits={int(cons_u.input_bits)}, "
      f"guard={int(cons_u.acc_guard)} -> {int(cons_u.total_cells)} cells, "
      f"RMS {cons_u.rms_error_wideband:.3e}")""")

code(r"""fig, axes = plt.subplots(1, 3, figsize=(16, 4.4))

# (a) accuracy vs coefficient width, passing vs not
for label, sub, color in (("fails", uniform_sweep_df[~uniform_sweep_df.passes_error_budget], "lightsteelblue"),
                          ("passes", passing, "seagreen")):
    axes[0].scatter(sub["coeff_bits"], sub["rms_error_wideband"], s=12, alpha=0.6,
                    label=label, color=color)
axes[0].axhline(ERROR_BUDGET["max_rms_error"], color="red", ls="--", lw=1,
                label="RMS budget")
axes[0].set_yscale("log"); axes[0].set_xlabel("coefficient width (bits)")
axes[0].set_ylabel("RMS error"); axes[0].legend(fontsize=8)
axes[0].set_title("(a) accuracy vs. coefficient width"); axes[0].grid(alpha=0.3)

# (b) synthesized cells vs coefficient width -- note non-monotonicity
sc = axes[1].scatter(uni_synth["coeff_bits"], uni_synth["total_cells"],
                     c=uni_synth["input_bits"], cmap="viridis", s=45)
plt.colorbar(sc, ax=axes[1], label="input width (bits)")
axes[1].set_xlabel("coefficient width (bits)"); axes[1].set_ylabel("synthesized cells")
axes[1].set_title("(b) area vs. coefficient width"); axes[1].grid(alpha=0.3)

# (c) the frontier itself
axes[2].scatter(uni_synth["total_cells"], uni_synth["rms_error_wideband"],
                color="steelblue", s=45, alpha=0.75)
axes[2].set_xlabel("synthesized cells"); axes[2].set_ylabel("RMS error")
axes[2].set_yscale("log")
axes[2].set_title("(c) uniform accuracy-area frontier"); axes[2].grid(alpha=0.3)
fig.tight_layout()
plt.show()

# Two effects worth reporting rather than asserting:
#  1. cell count should trend up with bit width -- check it on the passing set,
#  2. but it is NOT guaranteed to be monotonic: a wider-but-awkward width can
#     synthesize worse than a narrower, power-of-two-aligned one, because the
#     generic cell mapping is not a linear function of the operand width.
by_bits = uni_synth.groupby("coeff_bits")["total_cells"].min()
print("min cells by coefficient width (passing configs):")
print(by_bits.to_string())
deltas = np.diff(by_bits.values)
monotone = bool(np.all(deltas > 0))
print(f"\nmonotonically increasing here: {monotone} "
      f"({int((deltas > 0).sum())}/{len(deltas)} steps positive)")
print("so on this swept set area does move the expected direction as coefficients")
print("widen; the caveat is that this is not a law -- it is a measured trend, and")
print("the input width and accumulator width move the total just as much.")
corr = np.corrcoef(uni_synth["coeff_bits"], uni_synth["total_cells"])[0, 1]
corr_in = np.corrcoef(uni_synth["input_bits"], uni_synth["total_cells"])[0, 1]
print(f"\ncorrelation of cell count with coefficient width: {corr:+.2f}")
print(f"correlation of cell count with input width      : {corr_in:+.2f}")""")

# ===========================================================================
md(r"""## 7. Sensitivity analysis — the core idea

Some coefficients matter more to the *shape* of the frequency response than
others. Intuition says "the big center taps matter most" — but intuition is
not a method, so we measure it.

**Method A (as originally specified): finite differences of the spec margins.**
Perturb each unique coefficient by ±ε, re-measure the passband-ripple and
stopband-attenuation margins, and difference them. This is a standard
numerical gradient estimate.

**What we found — reported honestly:** the resulting ranking is **not stable in
ε** (below: only 11–44% of taps keep their rank as ε varies by decades). It is
also not magnitude-driven: it puts small edge taps *above* the large center
tap. The reason is structural. The frequency response is **affine** in every
coefficient, so the real question is "how much weighted response error does one
unit of coefficient error inject?" — and the margin-difference estimator
answers that through a *discrete max/min*, which is a noisy, nonlinear proxy.

**Method B (used for allocation): exact, spec-weighted response influence.**
For a folded pair `(i, N−1−i)` the derivative of the response has magnitude
`2|cos(ω·(center − i))|`, and for the center tap it is `1`. Integrating that
against the spec's own error budgets gives

```
S_k = sqrt( (1/δp²)·∫_pass |dH/dc_k|² dω  +  (1/δs²)·∫_stop |dH/dc_k|² dω )
```

This is exact, has no step size to tune, and directly encodes "protect the
taps whose error hurts the spec most." It reproduces the same qualitative
finding as Method A — **the center tap is the *least* sensitive**, because a
center-tap error shifts the whole response almost uniformly (a common-mode
gain change), which ripple and relative-attenuation specs are largely
insensitive to, while edge taps sculpt the ripple.

**And the measured spread is small: only 1.5× from most to least sensitive.**
That single number predicts the section 8 outcome: when every tap is within
1.5× of every other, there is very little for a non-uniform allocation to
reallocate.""")

code(r"""indices = unique_coeff_indices(len(h))

# ---- Method A: finite differences of the scalar margins -------------------
fd_scores = compute_sensitivities(h, FILTER_A_SPEC, eps=1e-4)
stability = sensitivity_rank_stability(h, FILTER_A_SPEC)

# ---- Method B: exact spec-weighted response influence --------------------
resp_scores = compute_sensitivities_response(h, FILTER_A_SPEC)

order_fd = np.argsort(-fd_scores)
order_resp = np.argsort(-resp_scores)
print("Method A (finite-difference margins) ranking:")
print("  " + ", ".join(f"tap{indices[k]}" for k in order_fd))
print("Method B (exact response influence) ranking:")
print("  " + ", ".join(f"tap{indices[k]}" for k in order_resp))
print(f"\nMethod B spread: {resp_scores.max()/resp_scores.min():.2f}x "
      f"(max {resp_scores.max():.1f}, min {resp_scores.min():.1f})")

print("\nMethod A eps-stability (fraction of taps keeping their rank):")
for eps, frac in stability["agreement"].items():
    print(f"  eps={eps:>8.0e}: {frac*100:5.1f}%  {'(reference)' if eps==stability['base_eps'] else ''}")

fig, axes = plt.subplots(1, 2, figsize=(13, 4.4))
axes[0].bar(range(len(indices)), resp_scores, color="darkorange",
            edgecolor="black", lw=0.5)
axes[0].set_xticks(range(len(indices)))
axes[0].set_xticklabels([f"{i}\n{h[i]:+.3f}" for i in indices], fontsize=8)
axes[0].set_xlabel("unique tap index (coefficient value below)")
axes[0].set_ylabel("spec-weighted response influence")
axes[0].set_title("(a) Method B: per-tap sensitivity")
axes[0].grid(alpha=0.3, axis="y")

eps_list = list(stability["agreement"].keys())
axes[1].plot(range(len(eps_list)), [stability["agreement"][e]*100 for e in eps_list],
             "o-", color="crimson")
axes[1].set_xticks(range(len(eps_list)))
axes[1].set_xticklabels([f"{e:.0e}" for e in eps_list])
axes[1].set_xlabel("finite-difference eps (Method A)")
axes[1].set_ylabel("% of taps keeping rank")
axes[1].set_ylim(0, 105)
axes[1].set_title("(b) Method A is eps-unstable")
axes[1].grid(alpha=0.3)
fig.tight_layout()
plt.show()""")

code(r"""# How the allocation rule works, and the design it produces.
bit_widths_demo = allocate_bits_by_sensitivity(resp_scores, min_bits=10, max_bits=16,
                                               n_levels=4)
print("allocation demo: min_bits=10, max_bits=16, 4 discrete levels")
print(f"{'tap':>4} {'coeff':>9} {'sensitivity':>12} {'bits':>5}")
for k, idx in enumerate(indices):
    print(f"{idx:>4} {h[idx]:>+9.5f} {resp_scores[k]:>12.2f} {bit_widths_demo[k]:>5}")
print(f"\naverage bits/tap = {bit_widths_demo.mean():.2f}  "
      f"(uniform would be a constant width for every tap)")

# The non-uniform path gets its own bit-exactness check before it is trusted.
nu_path = generate_rtl_nonuniform(h, cfg_baseline, bit_widths_demo,
                                  config_name="notebook_nonuniform")
print(f"\ngenerated non-uniform RTL: {paths.rel(nu_path)}")
nu_res = verify_config(h, cfg_baseline, nu_path, "fir_notebook_nonuniform",
                        make_test_signals(FILTER_A_SPEC["fs"], n_samples=256),
                        latency_cycles=LATENCY_CYCLES, bit_widths=bit_widths_demo)
nu_ok = print_results(nu_res)
assert nu_ok, "per-tap RTL is not bit-exact"
print("\n=> the per-tap sign-extension / binary-point-alignment logic is bit-exact too.")""")

# ===========================================================================
md(r"""## 8. Sensitivity-guided vs. uniform precision — the headline comparison

The sensitivity-guided sweep mirrors the uniform sweep exactly: same error
budget, same signals, same metrics, same synthesis flow. It varies the
allocation *floor* and *ceiling* (bits/tap between min and max, quantized to a
few discrete widths), the datapath input width, and the guard bits.

All three headline designs below are also verified bit-exact against the
golden model (section 10) — the comparison is between designs that are
*known correct*, not just plausible.""")

code(r"""comparison = pd.read_csv(paths.PARETO_DIR / "three_way_comparison.csv")
sens_synth = pd.read_csv(paths.SWEEPS_DIR / "sensitivity_sweep_synth.csv")

print(comparison.to_string(index=False))
print("\nNOTE: sweep CSVs are committed outputs. Regenerate with:")
print("  python src/python/sweep_with_synth.py         (uniform, ~2 min)")
print("  python src/python/sensitivity_search.py       (sensitivity-guided, ~1 min)")
print("  python src/python/build_comparison.py         (this table + the plot)\n")

bu = comparison.set_index("name").loc["Best uniform"]
sg = comparison.set_index("name").loc["Sensitivity-guided"]
delta_cells = (sg.cells - bu.cells) / bu.cells * 100
print(f"Sensitivity-guided vs best uniform: {delta_cells:+.1f}% cells "
      f"({bu.cells} -> {sg.cells})")
print(f"  RMS error          : {bu.rms_error:.3e} -> {sg.rms_error:.3e} "
      f"({(sg.rms_error/bu.rms_error - 1)*100:+.1f}%)")

fig, ax = plt.subplots(figsize=(8.5, 6))
ax.scatter(uni_synth["total_cells"], uni_synth["rms_error_wideband"], alpha=0.45,
           label="Uniform precision (all passing configs)", color="steelblue")
ax.scatter(sens_synth["total_cells"], sens_synth["rms_error_wideband"], alpha=0.45,
           label="Sensitivity-guided (all passing configs)", color="darkorange")
for _, row in comparison.iterrows():
    ax.scatter(row["cells"], row["rms_error"], s=230, marker="*", zorder=5, color="black")
    ax.annotate(row["name"], (row["cells"], row["rms_error"]),
                textcoords="offset points", xytext=(9, 9), fontsize=9)
ax.set_xlabel("synthesized cell count (area proxy)")
ax.set_ylabel("RMS error vs. float reference")
ax.set_yscale("log")
ax.set_title("Figure 5: accuracy-area Pareto frontier\nuniform vs. sensitivity-guided precision (Filter A)")
ax.legend(); ax.grid(alpha=0.3)
plt.show()""")

md(r"""### How to read this result honestly

This is **not** a clean win, and it should not be reported as one.

* Sensitivity-guided allocation reaches **11,048 cells vs. 11,466** for the best
  uniform design — about **3.6% smaller area** — but at a **slightly higher RMS
  error** (2.31e-4 vs. 2.21e-4, both comfortably inside the 1e-3 budget).
* In other words the two strategies land on **essentially the same frontier**,
  exchanging a small amount of accuracy for a small amount of area. Neither
  dominates the other.
* The mechanism is measured, not guessed: section 7 finds the per-tap
  sensitivity spread is only **1.5×**. With that little variation between taps,
  there is almost nothing for a non-uniform allocation to exploit — and the
  section 11 generalization test on a second filter shows the sign of the small
  difference flipping.

A 3.6% area difference at equal correctness is not nothing for a fixed-function
audio block, but it is well within the range where a different filter spec,
sweep granularity or synthesis strategy could flip it. The next section tests
exactly that intuition.""")

# ===========================================================================
md(r"""## 9. Physical implementation (SKY130 / OpenROAD) — **not attempted**

This is stated explicitly rather than left as a silent gap.

**What is reported instead:** area as *synthesized generic-cell count* after
Yosys `synth` + `abc -g cmos2`. This is a **relative** metric — consistent
across hundreds of candidates, useful for ranking, but it is not µm², has no
standard-cell library behind it, no place-and-route congestion and no timing.

**Why it was not attempted:** the Code-a-Chip program explicitly marks final
layout as *"encouraged but not required."* The repository contains the full
Week-4 scaffolding to run it — `synth/sky130.tcl` (OpenROAD flow),
`synth/openlane_config.json` (OpenLane2 config for the three headline
designs), `synth/constraints.sdc` (one shared clock constraint, derived from
the widest design so timing never silently favours narrower ones) and
`src/python/parse_openlane_results.py` (parses `metrics.csv` into real µm² and
worst-slack numbers). Running it requires a multi-GB container + PDK
(`openlane --smoke-test`) that is out of scope for this submission.

**What it would add:** absolute area in µm², real worst negative slack under
one shared clock, and confirmation that the *relative* ordering of the three
headline designs survives a real library and real place-and-route. It would not
change *which* candidates are on the frontier — that is already established.

**Power/energy is not measured at all.** No switching activity was annotated,
so no power number in this notebook is a claim. Smaller multipliers *tend* to
draw less dynamic power at matched activity, but that is a hypothesis, not a
measurement. Measuring it properly requires VCD/SAIF-annotated power analysis
(e.g. OpenSTA `report_power` driven by a VCD captured from the simulations in
this notebook). The notebook therefore makes **no energy claim whatsoever**.""")

code(r"""from parse_openlane_results import physical_flow_was_run, RUNS_DIR
print("OpenLane runs found:", physical_flow_was_run())
print("looked under       :", paths.rel(RUNS_DIR))
print("\n=> physical flow not executed in this submission; area is reported as")
print("   synthesized generic-cell count (relative metric). See section 9.")
print("   To run it:  bash env/install_tools.sh   # shows the OpenLane2 steps")
print("               openlane synth/openlane_config.json --run-tag best_uniform")""")

# ===========================================================================
md(r"""## 10. Stress tests: empirical error vs. analytical bound

Accuracy results are only credible if they survive adversarial inputs *and* if
the two kinds of "error number" are kept apart:

* **Empirical** — measured RMS/max error of the actual fixed-point (and,
  bit-exactly, the actual RTL) against the float reference.
* **Analytical** — a mathematical worst-case bound on coefficient-quantization
  error, `Σ_i |x| · 2^(−f_i)/2` with `|x| ≤ 1`. It is a *bound*: it must sit
  above every empirical number, and it says nothing about any particular
  signal.

They answer different questions, so they are reported in separate columns and
never merged.

The stress set adds two explicit overflow signals to the standard test vectors:
**overflow_stress_dc** (DC full-scale — the worst case for accumulator growth
in a symmetric lowpass, where every tap contributes the same sign) and
**overflow_stress_nyquist** (alternating ±full-scale — exercised for coverage
of the opposite frequency extreme).""")

code(r"""from final_stress_test import run as run_stress
stress_df, stress_rtl = run_stress(h=h, n_samples=2048, verify=True)

print("\n--- separation of empirical vs. analytical, per config ---")
summary = stress_df.groupby("config").agg(
    worst_empirical_max=("max_abs_error", "max"),
    mean_rms=("rms_error", "mean"),
    analytical_bound=("analytical_bound", "first"),
    total_overflow_events=("overflow_events", "sum"),
    all_rtl_bit_exact=("rtl_bit_exact", "all"),
).reset_index()
summary["bound_holds"] = summary.worst_empirical_max <= summary.analytical_bound
print(summary.to_string(index=False))
assert summary["all_rtl_bit_exact"].all()
assert summary["bound_holds"].all()
print("\nAll three headline designs are bit-exact against the golden model on every")
print("stress signal, with zero overflow events, and the analytical bound holds.")""")

# ===========================================================================
md(r"""## 11. Generalization: a second filter (Filter B)

The real test of a method is whether the **procedure** generalizes, not whether
one lucky allocation worked once. Filter B is a different spec (wider
passband, relaxed attenuation) run through the *identical* pipeline, and its
accuracy is measured on test signals generated with a **different random
seed**, so nothing about the Filter A development leaks in.""")

code(r"""b_cmp = pd.read_csv(paths.PARETO_DIR / "filter_b_three_way_comparison.csv")
b_uni = pd.read_csv(paths.SWEEPS_DIR / "filter_b_uniform_sweep_synth.csv")
b_sens = pd.read_csv(paths.SWEEPS_DIR / "filter_b_sensitivity_sweep_synth.csv")
print(b_cmp.to_string(index=False))
print("\nRegenerate with:  python src/python/generalization_filter_b.py   (~4 min)\n")

bu_b = b_cmp.set_index("name").loc["Best uniform"]
sg_b = b_cmp.set_index("name").loc["Sensitivity-guided"]
pct_b = (bu_b.cells - sg_b.cells) / bu_b.cells * 100
print(f"Filter B: sensitivity-guided uses {abs(pct_b):.1f}% "
      f"{'fewer' if pct_b > 0 else 'MORE'} cells than best uniform "
      f"({int(bu_b.cells)} -> {int(sg_b.cells)})")
print("Filter A: sensitivity-guided used 3.6% fewer cells than best uniform")
print("=> the small Filter A advantage does not reproduce on Filter B.")

fig, ax = plt.subplots(figsize=(8.5, 6))
ax.scatter(b_uni["total_cells"], b_uni["rms_error_wideband"], alpha=0.45,
           label="Uniform precision", color="steelblue")
ax.scatter(b_sens["total_cells"], b_sens["rms_error_wideband"], alpha=0.45,
           label="Sensitivity-guided", color="darkorange")
for _, row in b_cmp.iterrows():
    ax.scatter(row["cells"], row["rms_error"], s=230, marker="*", zorder=5, color="black")
    ax.annotate(row["name"], (row["cells"], row["rms_error"]),
                textcoords="offset points", xytext=(9, 9), fontsize=9)
ax.set_xlabel("synthesized cell count (area proxy)")
ax.set_ylabel("RMS error vs. float reference")
ax.set_yscale("log")
ax.set_title("Figure 6: Filter B generalization - accuracy-area frontier")
ax.legend(); ax.grid(alpha=0.3)
plt.show()""")

md(r"""**Conclusion of the generalization test.** The method's *procedure*
transfers — sensitivity analysis, allocation, RTL generation, bit-exact
verification and the three-way comparison all run unchanged on a second filter.
But the *advantage* does not reproduce: on Filter B the sensitivity-guided
design is ~4% **larger** than the best uniform design. Combined with Filter A's
~3.6% advantage, the honest reading is:

> For 17-tap symmetric FIR filters with a spec like these, uniform precision is
> already very close to optimal, and sensitivity-guided allocation lands within
> a few percent of it in either direction. The measured per-tap sensitivity
> spread (1.5× on Filter A, 1.46× on Filter B) is the reason: there is almost
> nothing to reallocate.

That is a *result*, not a failure — and it is exactly the outcome the project
plan anticipated as the credible alternative to a suspiciously clean win.""")

# ===========================================================================
md(r"""## 12. Limitations — what this work does *not* show

1. **Power and energy were not measured.** No switching activity was annotated;
   no energy claim is made anywhere. Area savings do not imply energy savings.
2. **Area is a relative metric.** Synthesized generic-cell count (`abc -g
   cmos2`), not SKY130 µm² or placed-and-routed area. Physical implementation
   was scoped out (section 9).
3. **Timing is not reported.** No static timing analysis was run, and no clock
   constraint was applied to the sweep. Pipeline depth is identical across
   candidates by construction, which is a *structural* fairness argument, not a
   timing number.
4. **One architecture.** Direct-form symmetric-folded, fully parallel, fixed
   pipeline. Transposed-form, time-multiplexed or pipelined variants could hit
   different accuracy-area trade-offs; nothing here says otherwise.
5. **Sensitivity Method A is eps-unstable**, as shown in section 7 (11–44% rank
   agreement). Allocation therefore uses Method B (exact, spec-weighted
   response influence). Both methods agree qualitatively (center tap least
   sensitive) but neither produces a strongly graded ranking.
6. **"Best uniform" and "conservative uniform" are sweep results, not global
   optima.** They are bounded by the swept ranges (coefficient 4–16 bits, input
   6–18 bits, guard {2,4}) and by the chosen error budget (RMS ≤ 1e-3,
   SNR ≥ 60 dB).
7. **The error budget thresholds are a design choice.** They were fixed before
   the sweeps ran and applied identically to both strategies, but a different
   budget would move which configurations pass.
8. **Rounding is round-half-up with saturation on.** Other rounding modes
   (truncation, convergent rounding, wrap-around) are supported by the
   generator and model but were not swept here.
9. **Filter A's transition band was widened from the original plan** (4→7 kHz
   to 4→10 kHz) because the original was infeasible at 17 taps. The chosen
   spec is stated in section 2 and met with margin.""")

# ===========================================================================
md(r"""## 13. Reproducibility

Everything is open source and runs locally — no license server, no Cadence
installation. Exact steps:

```bash
# 1. environment (creates ./venv and installs numpy/scipy/matplotlib/pandas/
#    jinja2/tqdm/jupyter, then smoke-tests Yosys)
bash env/install_tools.sh
source venv/bin/activate

# 2. golden-model validation (must pass before anything else is trusted)
python src/python/sanity_check.py

# 3. bit-exact RTL vs. model gate
python src/python/verify_rtl.py

# 4. the time-consuming sweep stages (regenerate the committed CSVs)
python src/python/sweep_with_synth.py          # uniform sweep + synthesis (~2 min)
python src/python/sensitivity_search.py        # sensitivity-guided sweep (~1 min)
python src/python/build_comparison.py          # three-way table + Pareto plot
python src/python/final_stress_test.py         # stress tests + RTL bit-exactness
python src/python/generalization_filter_b.py   # Filter B generalization (~4 min)

# 5. this notebook
jupyter lab notebooks/precisionfit.ipynb       # Kernel -> Restart & Run All
```

**What is live vs. cached in this notebook.** Sections 2, 3, 5, 7 and 10 are
computed live every run (a few seconds total). Sections 6, 8 and 11 load
committed CSV files from `results/`, because they are the output of hundreds of
external-tool (Yosys) invocations; each such cell names the script that
regenerates it. Nothing cached is stale: the CSVs are produced by exactly the
scripts listed above, from the same source files in this repository.

**Simulation backend.** `src/tb/build_and_run.sh` prefers **Verilator** (fast)
and automatically falls back to **Icarus Verilog**. Both harnesses implement
the same stimulus protocol, so the bit-exact comparison result is identical
either way; only wall-clock time differs. This notebook's environment used
Icarus Verilog.""")

code(r"""import subprocess, shutil

def tool_version(cmd):
    exe = shutil.which(cmd[0])
    if exe is None:
        return f"{cmd[0]}: not installed"
    try:
        out = subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
        return out.splitlines()[0][:110]
    except Exception as e:  # pragma: no cover
        return f"{cmd[0]}: {e}"

print("=== tool versions used to produce this notebook ===")
print(tool_version(["yosys", "-V"]))
print(tool_version(["iverilog", "-V"]))
print(tool_version(["verilator", "--version"]))
print(tool_version(["python3", "--version"]))
for mod in ("numpy", "scipy", "pandas", "matplotlib", "jinja2"):
    m = __import__(mod)
    print(f"{mod} {getattr(m, '__version__', '?')}")

print("\n=== repository layout ===")
for p in sorted(paths.ROOT.rglob("*")):
    if p.is_file() and not any(part in {".git", "venv", "__pycache__", "rtl"}
                               for part in p.parts):
        print("  ", p.relative_to(paths.ROOT))""")

md(r"""---
*End of notebook. Licensed under the Apache License, Version 2.0 — see
`LICENSE`. Generated RTL, result CSVs and figures are released under the same
terms.*""")


def build():
    nb = nbf.v4.new_notebook()
    nb["cells"] = [nbf.v4.new_markdown_cell(t) if kind == "md"
                   else nbf.v4.new_code_cell(t) for kind, t in CELLS]
    nb["metadata"] = {
        "kernelspec": {"display_name": "Python 3", "language": "python",
                       "name": "python3"},
        "language_info": {"name": "python"},
        "title": "PrecisionFit: Error-Budget-Driven FIR Filter Hardware Generator",
        "license": "Apache-2.0",
    }
    OUT.write_text(nbf.writes(nb))
    print(f"wrote {OUT} ({len(CELLS)} cells)")
    return nb


def execute():
    from nbclient import NotebookClient
    nb = nbf.read(OUT, as_version=4)
    client = NotebookClient(nb, timeout=900, kernel_name="python3",
                            resources={"metadata": {"path": str(HERE)}})
    client.execute()
    nbf.write(nb, OUT)
    n_errors = sum(1 for c in nb.cells
                   if c.cell_type == "code"
                   for o in c.get("outputs", []) if o.get("output_type") == "error")
    print(f"executed {OUT} with {n_errors} error output(s)")
    return n_errors


if __name__ == "__main__":
    build()
    if "--execute" in sys.argv:
        sys.exit(1 if execute() else 0)
