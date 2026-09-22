"""
Regenerate the committed RTL files from the current templates and check that
the simulation-visible text is unchanged. Used when the template gains
ifdef-guarded sections (formal layer) that must not perturb the design.

Committed artifacts touched (all regenerated in place):
  src/verilog/rtl/fir_baseline_conservative.v   (verify_rtl.py demo config)
  src/verilog/rtl/fir_best_uniform.v            (headline: best uniform)
  src/verilog/rtl/fir_conservative_uniform.v    (headline: conservative)
  src/verilog/rtl/fir_sensitivity_guided.v      (headline: sensitivity-guided)
  src/verilog/rtl/fir_notebook_baseline.v       (notebook Q2.10 demo)
  src/verilog/rtl/fir_notebook_nonuniform.v     (notebook per-tap demo)
"""
import numpy as np

import paths
from reference import FILTER_A_SPEC, design_filter
from fixedpoint import FixedPointConfig
from rtlgen import generate_rtl, generate_rtl_nonuniform
from build_comparison import headline_configs


def sim_visible_lines(text: str):
    """Lines a simulator/synthesizer sees: code minus comments, and minus any
    `ifdef FORMAL ... `endif region (the formal layer is invisible to sim)."""
    lines, stack = [], []          # stack of active `ifdef names (nested!)
    for raw in text.splitlines():
        line = raw.split("//", 1)[0].rstrip()
        s = line.strip()
        if not s:
            continue
        if s.startswith("`ifdef") or s.startswith("`ifndef"):
            stack.append(s.split()[1] if len(s.split()) > 1 else "?")
            continue
        if s.startswith("`endif"):
            if stack:
                stack.pop()
            continue
        if "FORMAL" in stack:      # inside the formal-only region
            continue
        lines.append(line)
    return lines


def main():
    paths.ensure_dirs()
    h = design_filter(FILTER_A_SPEC)

    # 1. Headline designs (module/config names must match the committed files).
    headline_configs(h)   # returns rebuilt paths; also rewrites the .v files

    # 2. verify_rtl.py demo config -> fir_baseline_conservative.v
    cfg_demo = FixedPointConfig(
        coeff_int_bits=2, coeff_frac_bits=10,
        input_int_bits=2, input_frac_bits=14,
        acc_guard_bits=4,
        output_int_bits=2, output_frac_bits=14,
        rounding="round", saturate_output=True,
    )
    generate_rtl(h, cfg_demo, config_name="baseline_conservative")

    # 3. Notebook-flow demos (values must match the committed files exactly).
    cfg_nb = FixedPointConfig(
        coeff_int_bits=2, coeff_frac_bits=10,
        input_int_bits=2, input_frac_bits=14,
        acc_guard_bits=4,
        output_int_bits=2, output_frac_bits=14,
        rounding="round", saturate_output=True,
    )
    generate_rtl(h, cfg_nb, config_name="notebook_baseline")
    bits = np.array([14, 14, 14, 12, 16, 12, 16, 10, 10])
    generate_rtl_nonuniform(h, cfg_nb, bits, config_name="notebook_nonuniform")

    # 4. Drift check: for every RTL file, the diff against git HEAD must be
    # confined to comment lines or the formal block (`ifdef FORMAL ... `endif).
    import subprocess
    files = sorted(p.name for p in paths.RTL_DIR.glob("*.v"))
    bad = []
    for name in files:
        p = paths.RTL_DIR / name
        old = subprocess.run(["git", "show", f"HEAD:./{p.relative_to(paths.ROOT)}"],
                             cwd=paths.ROOT, capture_output=True, text=True).stdout
        if not old:
            bad.append((name, "no HEAD version"))
            continue
        old_sim = sim_visible_lines(old)
        new_sim = sim_visible_lines(p.read_text())
        if old_sim != new_sim:
            added = [l for l in new_sim if l not in old_sim]
            bad.append((name, f"sim-visible drift, first added line: {added[:1]}"))
    if bad:
        for name, why in bad:
            print(f"DRIFT  {name}: {why}")
        raise SystemExit(1)
    print(f"All {len(files)} RTL files: simulation-visible text unchanged "
          "(edits confined to comments / the formal block).")


if __name__ == "__main__":
    main()
