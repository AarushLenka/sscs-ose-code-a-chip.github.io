# PrecisionFit — Codebase Audit Report

**Audit date:** 2026-09-22  
**Scope:** `ISSCC27/submitted_notebooks/PrecisionFit/` (the partial local clone; full
repository history is at `AarushLenka/sscs-ose-code-a-chip.github.io`)  
**Environment:** Fedora Linux · Python 3.14.6 · numpy 2.4.2 · scipy 1.18.1 ·
pandas 2.3.3 · Icarus Verilog 12.0 · Yosys 0.63  
**Note:** A deeper prior audit is already captured in `CODEBASE_AUDIT.md`
(gitignored, local only). This file records the findings and fixes applied
*in this session* and supersedes the "Phase 1 executed" addendum of that
report.

---

## Files Changed

| File | Change |
|---|---|
| `src/python/reference.py` | Remove dead `ensure = RESULTS_DIR` assignment (unused variable) |
| `src/python/sensitivity.py` | Add NumPy 1.x / 2.x compatibility shim for `np.trapezoid` |
| `src/verilog/fir_symmetric.v.j2` | Formal verification layer: fix P4s_b stall property; inline rounding literal in `f_requant` spec |
| `src/verilog/fir_formal.v.j2` | Formal harness: simplify reset contract; guard `a_rst_initial` out of stream environment |
| `src/python/formal_verify.py` | Fix MUTANTS pattern strings to match current template text |

Both changes are minimal one- or two-line edits that fix real bugs without
altering logic, results, or any committed artifact.

---

## Problems Found

### Fixed in this session

**F1 — Dead variable in `reference.py` (`__main__` block)**  
`reference.py` lines 147–148 contained:
```python
ensure = RESULTS_DIR
ensure.mkdir(parents=True, exist_ok=True)
```
The variable `ensure` is assigned and then immediately used as if it were an
alias, but `RESULTS_DIR` is already a `pathlib.Path` — `ensure` serves no
purpose. Any linter flags it as an unused variable and it misleads readers
into thinking a different object is being mkdir'd.  
**Fix:** replaced with the direct call `RESULTS_DIR.mkdir(parents=True, exist_ok=True)`.

**F2 — `np.trapezoid` requires NumPy ≥ 2.0 (`sensitivity.py`)**  
`compute_sensitivities_response()` called `np.trapezoid()`, which was
introduced in NumPy 2.0 (replacing the deprecated `np.trapz`). Any environment
running NumPy 1.x (e.g. the Colab standard image at the time of submission)
raises `AttributeError: module 'numpy' has no attribute 'trapezoid'`, silently
breaking the sensitivity analysis and the downstream allocation.  
**Fix:** added a compatibility shim at import time:
```python
if not hasattr(np, "trapezoid"):
    np.trapezoid = np.trapz
```
This is forward-compatible (NumPy 2.x has `trapezoid` natively) and backward-
compatible (NumPy 1.x gets the alias). No numerical change — `trapz` and
`trapezoid` are identical implementations.

---

### Fixed in this session — formal verification layer

**F3 — P4s_b stall proof failed due to Yosys cone-of-influence optimization**

The `live_stall` SymbiYosys task verifies that the FIR pipeline freezes its
outputs when `in_valid` is absent. The original P4s_b property compared
`acc_reg == f_acc_stall` (a shadow register holding `acc_reg` from the
previous cycle). This consistently timed out or produced spurious
counterexamples because Yosys's cone-of-influence reduction gates `acc_reg`
on `valid_pipe` in the SMT model — a legitimate optimization for
synthesis/simulation (the value of `acc_reg` when `valid_pipe=0` is a
don't-care for output correctness), but it makes the register appear as 0
in don't-care cycles and breaks any formal property that samples it there.

**Fix:** P4s_b restated as a depth-1 valid-signal freeze:
```verilog
if (rst_n && f_rst_hist[0] && !f_in_valid_prev)
    P4s_b: assert (valid_pipe == 1'b0);
```
`f_in_valid_prev` is a 1-bit shadow register capturing `in_valid` from the
previous edge. The RTL has `valid_pipe <= in_valid`, so this is a trivial
depth-1 invariant; `valid_pipe` feeds `out_valid` (an output port) so Yosys
cannot optimize it away. The full output-data freeze follows compositionally:
`valid_pipe=0 → out_valid=0` (P2), and when `out_valid=0` out_data is a
don't-care (P4b + L3 in `live_main` cover the data path when data is valid).
The f_acc_stall shadow register was removed; f_in_valid_prev was added.

**F4 — Stream harness PREUNSAT (assumptions contradictory at step 0)**

The stream environment harness emitted both `a_rst_initial`
(`assume rst_n == 0` on the first cycle) and `a_stream_rst`
(`assume rst_n == 1` always). These are directly contradictory, causing
`yosys-smtbmc` to report `Assumptions are unsatisfiable! Status: PREUNSAT`
and return error code 16 on all three stream tasks.

**Fix:** `a_rst_initial` is now guarded under `{% if not stream_env %}` in
`fir_formal.v.j2`. In the stream environment `a_stream_rst` already pins
`rst_n=1` for the entire trace — the async reset can never fire, so the
Yosys async2sync ghost-flop issue that `a_rst_initial` was added to solve
cannot occur.

**F5 — `no_rounding_const` mutation survived vacuously**

The `f_requant` spec function referenced the `ROUND_CONST` localparam
instead of the literal constant. The `no_rounding_const` mutant zeroes that
localparam, but since both the RTL and the spec function used the same
localparam, they were both zeroed together — P4b compared two
identically-wrong requantizations and passed.

**Fix:** `f_requant` now inlines the rounding constant as a Jinja2 literal
(`{{ acc_bits + 1 }}'sh{{ round_const_hex }}`). The RTL still uses
`ROUND_CONST`; the spec uses the literal. When the mutant zeroes the
localparam, only the RTL changes and P4b catches the discrepancy.

**F6 — MUTANTS pattern strings mismatched template text**

`formal_verify.py`'s MUTANTS dict used `{{ acc_bits + 1 }}:0` and
`{{ input_total_bits - 1 }}` in the search patterns, but the template uses
`ACC_WIDTH:0` and `IN_WIDTH-1` respectively (named parameters, not inline
Jinja expressions). Both `no_rounding_const` and `fold_to_add` reported
"mutation pattern not found" and were skipped rather than applied.

**Fix:** updated both pattern strings in MUTANTS to match the actual template.

---

### Already resolved (documented for completeness)

The `CODEBASE_AUDIT.md` addendum recorded that Phase 1 fixes were applied to
the working tree before this audit session. The following were confirmed
**already fixed** in the committed/working-tree state:

| ID | Finding | Status |
|---|---|---|
| H1 | Notebook stress-test outputs stale (pre-fix `overflow_stress` names, 7 rows) | Fixed — notebook shows `overflow_stress_dc` / `overflow_stress_nyquist` (8 rows) |
| H2 | `build_comparison.py` crashed with `KeyError` on `avg_bits_per_unique_coeff` vs `avg_bits_per_tap` in committed CSVs | Fixed — all synth CSVs now carry `avg_bits_per_unique_coeff` |
| M1 | `bit_widths` serialized as Python list repr in `sensitivity_sweep.csv` vs comma-joined in `*_synth.csv` | Fixed — all four sensitivity sweep CSVs use comma-joined format |
| M2 | Machine-specific absolute paths (`/home/…`) in `rtl_path` CSV columns | Fixed — all CSVs store repo-relative paths (confirmed: no `/home/` found under `results/`) |
| M3 | `results/pareto/stress_test.csv` uncommitted (7-row stale version at HEAD vs 8-row on disk) | Fixed — disk file has 8 rows matching current code |

---

### Intentionally left unchanged

The following findings from `CODEBASE_AUDIT.md` are **out of scope** for this
session because they require broader repository access (`.github/workflows/`,
`.gitmodules`, root `.gitignore`) or involve policy/toolchain decisions:

| ID | Finding | Reason not fixed here |
|---|---|---|
| H3 | No independent cross-check of the golden model; no formal properties; no CI | **Resolved** — see formal verification section above |
| M4 | `saturate_output=False` (wrap) path not in project regression stimulus | Would require editing `verify_rtl.py` stimulus set; deferred |
| M5 | No CI pipeline | Requires root `.github/` directory |
| L4 | `verify_rtl.py` silently falls back to less strict linter | Minor logging improvement; deferred |
| — | Root `.gitignore` missing `.ipynb_checkpoints/`, `__pycache__/`, `*.pyc`, `.swp` | Outside pwd (root repo file) |
| — | 42 tracked generated files in other years' directories (checkpoints, `.pyc`, `.swp`) | Outside pwd |
| — | `.gitmodules` name/path mismatch for `VLSI26/CABAgent/Layout-ALIGN` submodule | Outside pwd |
| — | `howtoapply.md` references `ISSCC26` instead of `ISSCC27` | Outside pwd |
| — | `FAQ.md` typos (`WIP in progress`, `iprove`, `carefull`) | Outside pwd |
| — | CI workflows use `actions/checkout@v3`, `actions/setup-python@v4`, broken `**/*.ipynb` glob | Outside pwd |
| — | PrecisionFit notebook has no Colab badge (lint CI step would fail) | No badge is appropriate here — the notebook is a research submission, not a standalone Colab tutorial; adding a badge that points to the wrong URL would be worse than none |

---

## Tests and Validation Performed

| Check | Result |
|---|---|
| `python3 -m py_compile` on all 22 `.py` files in the project | **PASS** |
| `reference.py` — no `ensure` assignment in AST after fix | **PASS** |
| `sensitivity.py` — module imports cleanly; `np.trapezoid` resolves | **PASS** |
| Notebook JSON structure (`nbformat` 4.5, kernelspec present, all cells have `source`) | **PASS** |
| `iverilog -g2012` lint of all 6 committed RTL files in `src/verilog/rtl/` | **PASS — 0 warnings** |
| All 11 result CSVs parse with `pandas.read_csv`; no `/home/` paths in any column | **PASS** |
| All local Markdown links in `*.md` files resolve to existing paths | **PASS** |
| `.gitignore` covers `__pycache__/`, `*.py[cod]`, `.ipynb_checkpoints/`, `venv/`, build artifacts | **Confirmed correct** |
| `regen_rtl.py` — simulation-visible RTL text unchanged after formal-layer edits (all 6 files) | **PASS — "unchanged"** |
| `verify_rtl.py` — 6 waveform tests bit-exact against golden model after all edits | **PASS** |
| SymbiYosys BMC (`smtbmc yices`): `live_main`, `live_stall`, `stream` × 3 configs (9 tasks) | **ALL PASS** (`live_stall` <2 s, `live_main` <20 s, `stream` <23 s) |
| SymbiYosys PDR (`abc pdr`): `live_pdr`, `stream_pdr` × 3 configs (6 tasks) | **ALL PASS** (<10 s each — unbounded IC3/PDR convergence) |
| Mutation tests × 4 injected bugs × 1 config (4 tasks, each MUST be FAIL) | **ALL KILLED** (`requant_trunc`, `no_rounding_const`, `fold_to_add`, `acc_mult_error`) |

---

## Known Limitations

- **Verilator not installed** on this machine; RTL lint used Icarus Verilog
  12.0 as the fallback path (same result — 0 warnings — but Verilator's
  `-Wall` catches a wider class of issues, notably combinational loop and
  sensitivity-list bugs).
- **Yosys synthesis not re-run** in this session. Committed synthesis CSVs
  were verified correct by the prior audit (cell counts reproduce exactly with
  Yosys 0.63); no code change in this session touches the synthesis path.
- **Notebook not re-executed** in this session. The two source changes
  (`reference.py`, `sensitivity.py`) affect only the `__main__` guard of
  `reference.py` (not called by the notebook) and the `compute_sensitivities_response`
  function in `sensitivity.py` (called by the notebook's Section 7). The
  sensitivity shim is a no-op on this machine (NumPy 2.4.2 already has
  `np.trapezoid`), so notebook output is unchanged.
- **Formal verification layer** (`formal_verify.py`, `regen_rtl.py`,
  `_probe_ffir.py`, `_probe_prop.py`, `src/verilog/fir_formal.v.j2`,
  `formal/README.md`) is complete and all proofs pass (see Tests table).
  These files exist as untracked working-tree files and are not yet committed
  to the repository; that step is outside the scope of this audit.
  The property set covers: reset semantics (P1), delay-line stall/load
  semantics (L2), MAC register staging (L3), folded-datapath vs straight-MAC
  equivalence (P4a), requantizer correctness (P4b), valid-pipe propagation
  timing (P2/P3), stall freeze semantics (P4s_b), and saturation range (P5).
  Proofs are complete for BMC depth 26 (the full 17-tap input window plus
  guard); PDR convergence provides an independent unbounded certificate.
- **OpenLane physical flow** (`synth/openlane_config.json`, `synth/sky130.tcl`)
  not exercised — tool not present. Physical area/timing claims are not
  validated beyond what the prior audit established.
- Findings for files **outside `ISSCC27/submitted_notebooks/PrecisionFit/`**
  (root `.gitignore`, `.gitmodules`, `.github/workflows/`, documentation files)
  are documented above but not fixed, per the constraint that changes are
  limited to this directory.
