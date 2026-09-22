# Formal Verification (SymbiYosys)

This directory is created by `src/python/formal_verify.py` for generated
harnesses and `.sby` task files. Nothing here needs to be committed; the
layer is fully regenerable from the templates and the driver:

```bash
python3 src/python/formal_verify.py            # proofs + mutation tests
python3 src/python/formal_verify.py --proofs   # proofs only
```

What is proven, on every committed FIR configuration (see
`src/python/formal_verify.py`):

| Property | Statement |
|---|---|
| P1 | reset (`rst_n` low) clears taps/accumulator/valid/output within one cycle |
| P2 | `out_valid == $past(valid_pipe)` (clean 2-stage pipeline) |
| P3 | no `out_valid` without `in_valid` exactly two cycles earlier |
| P4 | `acc_reg`/`out_data` equal an independent per-tap shadow MAC bit-for-bit |
| P5 | `out_valid` implies `out_data` within the saturation range |
| L1/L2 | helper lemmas (shadow delay line, shadow deep state) that let k-induction close P4 |

The properties live in the DUT template's `ifdef FORMAL block
(`src/verilog/fir_symmetric.v.j2`) and are invisible to simulation, lint and
synthesis — verified by the drift check in `src/python/regen_rtl.py` and by
byte-identical synthesis cell counts.

Engines: `abc pdr` (unbounded IC3/PDR) and `smtbmc yices` (BMC with an
independent SMT solver, Z3 also works). All properties are safety properties
whose violating traces are bounded well below the BMC depth used, so the
bounded runs are complete proofs for this design.
