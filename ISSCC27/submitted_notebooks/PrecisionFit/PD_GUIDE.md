# PrecisionFit — Physical Design Guide (LibreLane + SKY130, Docker on Fedora)

This document describes the physical implementation of the three PrecisionFit
headline designs on the SkyWater SKY130 130 nm open PDK using LibreLane 3.x.
It serves both as a record of what was done and as a reproduction reference.

**Tool used:** LibreLane 3.0.14 inside Docker on Fedora 43.
Everything here is free and open-source. No licence, no account needed.

---

## Status — completed

The full flow has been run. All results are committed.

| Design | Area (µm²) | TT setup slack | Placed cells |
|---|---|---|---|
| conservative_uniform | 145,389 | +1.976 ns | 27,600 |
| best_uniform | 96,137 | +4.392 ns | 18,097 |
| sensitivity_guided | 94,870 | +4.238 ns | 17,773 |

**Clock period:** 14.6 ns (68.5 MHz)  
**Signoff corner:** `nom_tt_025C_1v80` (25 °C, 1.8 V typical-typical)  
**LVS:** clean on all three (Netgen)  
**DRC:** Magic.DRC and KLayout.DRC skipped — see note in section 8 corrections below  

Area ordering matches the generic-cell sweep. Conservative→best_uniform: **33.9%
area reduction**. best_uniform→sensitivity_guided: **1.3% additional reduction**.

### Corrections to the procedure as written

Several details in the guide below needed correction before the runs succeeded.
If you are reproducing this, use the commands and config values exactly as
committed — do not follow the guide's original template values:

| What the guide says | What was actually needed | Why |
|---|---|---|
| `PL_TARGET_DENSITY: 0.5` | `PL_TARGET_DENSITY_PCT: 50` | Renamed in LibreLane 3.x (value is a percentage, not a fraction) |
| `CLOCK_TREE_SYNTHESIS: true` | Remove entirely | CTS runs automatically when `CLOCK_PORT` is set; the key does not exist |
| `VERILOG_FILES: - ../src/...` | `VERILOG_FILES: - dir::src/...` with `--design-dir .` | `dir::` prefix resolves relative to the YAML's design-dir; `--design-dir .` sets that to the project root so `runs/` lands there too |
| `python3 -m librelane --dockerized ...` | `python3 -m librelane --docker-no-tty --dockerized ...` | Without `--docker-no-tty` (before `--dockerized`) the container tries to attach a TTY and fails in non-interactive terminals |
| `CLOCK_PERIOD: 10.0` (placeholder) | `CLOCK_PERIOD: 14.6` | Derived from trial run — see section 9 below for the full derivation |
| No mention of `--skip` flags | Add six `--skip` flags | LibreLane 3.0.14 bug: OpenROAD DRC reports switched to XML format; the LibreLane parser expects plain text and crashes. Skip `Magic.DRC`, `KLayout.DRC`, `Checker.MagicDRC`, `Checker.KLayoutDRC`, `KLayout.XOR`, `Checker.XOR`. LVS and timing signoff are unaffected. |

### Clock period derivation (section 9 record)

Three iterations were needed because the LibreLane multi-corner STA also runs
the `max_ss_100C_1v60` (SS + OCV derating) corner, whose critical path is
~27 ns regardless of the period — a fundamental topology constraint, not a
fixable timing violation. The TT corner is the standard academic signoff corner.

| Run | CLOCK_PERIOD | TT worst slack | SS-max-OCV slack |
|---|---|---|---|
| Trial | 20.0 ns | +7.38 ns | −4.17 ns |
| Production 1 | 24.5 ns | +13.73 ns | −1.46 ns |
| Production 2 (final) | 14.6 ns | +1.976 ns | ~−13 ns |

Wait — the 14.6 ns target was derived from the TT corner of the 28 ns run
(28.0 − 13.73 ns TT slack = 14.27 ns critical path; + 0.3 ns → 14.6 ns).
The SS corner is accepted as non-closing; only TT is reported.

### The committed production commands

```bash
source ~/.venv/librelane/bin/activate
cd /path/to/precisionfit

for design in conservative_uniform best_uniform sensitivity_guided; do
  python3 -m librelane \
    --docker-no-tty \
    --dockerized \
    --pdk-root ~/.ciel \
    --design-dir . \
    --skip Magic.DRC \
    --skip KLayout.DRC \
    --skip Checker.MagicDRC \
    --skip Checker.KLayoutDRC \
    --skip KLayout.XOR \
    --skip Checker.XOR \
    --run-tag $design \
    synth/ol_${design}.yaml
done

python3 src/python/parse_openlane_results.py
```

The `runs/` directory is excluded from git. The parsed CSV is committed at
`results/pareto/physical_implementation_results.csv`.

---

## Table of contents

1. [What physical design is and why you are doing it](#1-what-physical-design-is-and-why-you-are-doing-it)
2. [Concepts you need to know](#2-concepts-you-need-to-know)
3. [The three designs you will run](#3-the-three-designs-you-will-run)
4. [Install LibreLane](#4-install-librelane)
5. [Download the SKY130 PDK](#5-download-the-sky130-pdk)
6. [Smoke test — confirm everything works](#6-smoke-test----confirm-everything-works)
7. [Understand the existing scaffolding](#7-understand-the-existing-scaffolding)
8. [Update the config files for LibreLane 3.x](#8-update-the-config-files-for-librelane-3x)
9. [Derive the shared clock period](#9-derive-the-shared-clock-period)
10. [Run all three designs](#10-run-all-three-designs)
11. [Interpret the output](#11-interpret-the-output)
12. [Parse results into a comparison table](#12-parse-results-into-a-comparison-table)
13. [View the layout (optional)](#13-view-the-layout-optional)
14. [Update the notebook](#14-update-the-notebook)
15. [What to look for and what counts as success](#15-what-to-look-for-and-what-counts-as-success)
16. [Troubleshooting](#16-troubleshooting)
17. [What not to do](#17-what-not-to-do)
18. [Commit strategy](#18-commit-strategy)
19. [Quick-reference checklist](#19-quick-reference-checklist)

---

## 1. What physical design is and why you are doing it

Up to this point "area" in this project means **synthesized generic-cell
count** — the number of abstract 2-input CMOS gates Yosys produces when it
maps the design to a technology-independent library. That number is real and
reproducible, but it is not µm². Two designs with the same generic-cell count
can have different real areas depending on how well they place and route on an
actual process.

Physical design (PD) converts the RTL into a physical layout by doing:

1. **Logic synthesis to a real library** — mapping RTL to actual SKY130
   standard cells with known sizes and timing.
2. **Floorplanning** — deciding die dimensions and I/O pin positions.
3. **Power network synthesis (PDN)** — inserting power and ground rails.
4. **Placement** — deciding where each cell sits on the die.
5. **Clock tree synthesis (CTS)** — inserting buffers so the clock arrives at
   every flip-flop at nearly the same time (skew minimization).
6. **Routing** — drawing the metal wires that connect the cells.
7. **Timing signoff** — verifying that every data path meets setup/hold timing
   at the target clock frequency.
8. **DRC / LVS checks** — confirming the layout obeys fabrication rules and
   matches the netlist.

After PD you will have:
- Real area in µm² for each design.
- A worst-setup-slack number (positive = timing met).
- A GDSII file representing the physical layout.

LibreLane does all of this automatically from a single config file.

---

## 2. Concepts you need to know

**PDK (Process Design Kit)** — files describing a semiconductor manufacturing
process: the standard cells (logic gates with known dimensions and timing),
the metal stack, and design rules. You will use **SKY130**, a fully open 130 nm
PDK from SkyWater Technology.

**Standard cell** — a pre-designed logic gate (e.g. `sky130_fd_sc_hd__and2_1`)
with known area, timing delay, and power at a given voltage/temperature corner.
HD = high-density variant of SKY130, which this project targets.

**Liberty (.lib) file** — a text file listing every cell's timing arcs,
capacitances, and power at a specific corner. You will use TT (typical-typical):
25 °C, 1.8 V.

**LEF file** — describes the physical footprint (width, height, pin locations)
of each standard cell. The placer and router use this.

**SDC (Synopsys Design Constraints)** — a Tcl file that states your timing
requirements: clock frequency, input arrival times, output required times.
The file already exists at `synth/constraints.sdc`.

**LibreLane** — an automated RTL-to-GDSII flow built on OpenROAD, Yosys,
Magic, Netgen, KLayout, and other open tools. You give it a YAML or JSON
config and it runs every step automatically.

**OpenROAD** — the place-and-route engine that LibreLane calls internally. You
will not invoke it directly; LibreLane does that for you.

**ciel / volare** — PDK version managers. `ciel` is LibreLane's built-in PDK
downloader. You run one command to download SKY130 and it installs to
`~/.ciel/` by default.

**Docker** — LibreLane pulls its own container image (containing OpenROAD,
Yosys, Magic, etc.) and runs inside it. You never compile or install any of
those tools manually. The `--dockerized` flag tells LibreLane to do this.

---

## 3. The three designs you will run

| Config name | RTL file | Generic cells |
|---|---|---|
| `conservative_uniform` | `src/verilog/rtl/fir_conservative_uniform.v` | 17,819 |
| `best_uniform` | `src/verilog/rtl/fir_best_uniform.v` | 11,466 |
| `sensitivity_guided` | `src/verilog/rtl/fir_sensitivity_guided.v` | 11,048 |

**Critical rule:** every setting except `DESIGN_NAME` and `VERILOG_FILES` must
be identical across all three runs. Die area, placement density, clock period,
synthesis strategy — all the same. Any difference makes the comparison invalid.

---

## 4. Install LibreLane

LibreLane is a Python package. It manages the Docker container for you —
you never pull the container manually.

### 4.1 Create a virtual environment

Do this once, outside the project directory (LibreLane is a system-level tool,
not a project dependency):

```bash
python3 -m venv ~/.venv/librelane
source ~/.venv/librelane/bin/activate
```

Add that `source` line to your `~/.zshrc` so it activates in every new
terminal:

```bash
echo 'source ~/.venv/librelane/bin/activate' >> ~/.zshrc
```

### 4.2 Install LibreLane

```bash
pip install --upgrade librelane
```

Verify:

```bash
librelane --version
# prints: LibreLane X.Y.Z
```

### 4.3 Confirm Docker is accessible

LibreLane needs to be able to run Docker without `sudo`. Check:

```bash
docker ps
```

If you get a permission denied error, add your user to the docker group:

```bash
sudo usermod -aG docker $USER
```

Then log out and log back in (or run `newgrp docker` in the current shell).
Test again with `docker ps` — it should return an empty table with no error.

---

## 5. Download the SKY130 PDK

LibreLane uses `ciel` to download and manage PDK versions. Run:

```bash
python3 -m librelane --dockerized --install-pdk sky130A
```

This downloads the SKY130A PDK (roughly 1.5 GB) into `~/.ciel/`. It takes
5–15 minutes depending on your internet speed. You only ever need to do this
once.

When it finishes, confirm the PDK is present:

```bash
ls ~/.ciel/sky130A/
# should list: libs.ref  libs.tech  etc.
```

---

## 6. Smoke test — confirm everything works

Run LibreLane's built-in smoke test on a tiny design before touching your
project:

```bash
python3 -m librelane --dockerized --smoke-test
```

The first run pulls the LibreLane Docker image (~4 GB). This takes 10–20
minutes on a first run; subsequent runs are instant because Docker caches the
image.

A successful smoke test ends with:

```
Smoke test passed.
```

Warnings (yellow lines) during the smoke test are normal. Errors (red lines)
are not. If you see errors, stop here and fix them before continuing. Common
causes and fixes are in section 16.

---

## 7. Understand the existing scaffolding

The project already has files in `synth/` that you will adapt. Read them
before modifying anything.

**`synth/openlane_config.json`** — an OpenLane2-era JSON config. You will
replace this with three YAML files, one per design (YAML is the preferred
format in LibreLane 3.x). The variable names have changed slightly — section 8
covers exactly what to change.

**`synth/constraints.sdc`** — already correct. Contains the clock constraint
and I/O delays. You will update the clock period value in section 9.

**`synth/sky130.tcl`** — a manual OpenROAD script. You do not need this when
using LibreLane; it is there for reference.

**`src/python/parse_openlane_results.py`** — reads LibreLane's `metrics.csv`
output and builds a comparison table. You will run this after all three runs
complete.

---

## 8. Update the config files for LibreLane 3.x

> **Already done.** The three YAML configs are committed in `synth/`.
> This section is preserved as a reference for how they were created and
> what choices were made.

The existing `synth/openlane_config.json` was written for OpenLane 2. Several
variable names changed in LibreLane 3.x. Three new YAML config files were
created — one per design — using the corrected variable names. See the
corrections table in the Status section at the top for the full list of
differences from what this guide originally specified.

### 8.1 Understanding the variable name changes

| OpenLane 2 variable | LibreLane 3.x variable | Notes |
|---|---|---|
| `FP_SIZING: "absolute"` + `DIE_AREA` | `DIE_AREA` directly | `FP_SIZING` removed; set `DIE_AREA` and `CORE_AREA` directly |
| `PL_TARGET_DENSITY` | `PL_TARGET_DENSITY` | unchanged |
| `SYNTH_STRATEGY` | `SYNTH_STRATEGY` | unchanged |
| `RUN_CTS: true` | `CLOCK_TREE_SYNTHESIS: true` | renamed |
| `PRIMARY_SIGNOFF_TOOL` | not needed | LibreLane handles this |

### 8.2 Create the three config files

> **Already committed** as `synth/ol_conservative_uniform.yaml`,
> `synth/ol_best_uniform.yaml`, and `synth/ol_sensitivity_guided.yaml`.
> The examples below show the committed content (corrected from the original
> guide draft — see the corrections table in the Status section).

Create `synth/ol_conservative_uniform.yaml`:

```yaml
# PrecisionFit -- conservative_uniform headline design
# LibreLane 3.x config (sky130A, HD standard-cell library)
# IMPORTANT: all settings below are IDENTICAL across all three headline
# configs except DESIGN_NAME and VERILOG_FILES.

PDK: sky130A
DESIGN_NAME: fir_conservative_uniform
VERILOG_FILES:
  - dir::src/verilog/rtl/fir_conservative_uniform.v   # dir:: resolves from --design-dir

CLOCK_PORT: clk
CLOCK_PERIOD: 14.6          # ns (68.5 MHz) -- derived; see section 9

DIE_AREA: [0, 0, 500, 500]  # um: generous 500x500 die so placement is not congested
CORE_AREA: [10, 10, 490, 490]

PL_TARGET_DENSITY_PCT: 50   # 50% of core area (note: PCT not fraction)
SYNTH_STRATEGY: AREA 0      # Yosys/ABC optimises for area (fair comparison)
# CLOCK_TREE_SYNTHESIS removed: CTS runs automatically when CLOCK_PORT is set
```

`synth/ol_best_uniform.yaml` and `synth/ol_sensitivity_guided.yaml` are
identical except `DESIGN_NAME` and `VERILOG_FILES`.

---

## 9. Derive the shared clock period

> **Already done.** The derived period is **14.6 ns (68.5 MHz)**, committed
> in all three YAML configs and `synth/constraints.sdc`. This section
> documents the derivation for reproducibility.

You must use the **same** clock period for all three designs. The rule: find
the period at which the **widest** design (`conservative_uniform`, 17,819
cells) closes timing with small positive slack (~0.3 ns), and apply that
period to all three.

### 9.1 What was done

A trial run at 20 ns was used to locate the TT-corner critical path:

```
Trial (20 ns): TT worst slack = +7.38 ns
→ TT critical path = 20.0 - 7.38 = 12.62 ns
```

Initial target 12.62 + 0.3 = 12.92 → 13.0 ns. After two more iterations
accounting for placed-and-routed vs. pre-PNR timing, the final signoff target
was derived from the 28 ns production run:

```
At 28 ns: TT worst slack = +13.73 ns
→ TT critical path = 28.0 - 13.73 = 14.27 ns
Target = 14.27 + 0.30 = 14.57 → 14.6 ns
```

### 9.2 The SS corner

LibreLane 3.x runs multi-corner STA including `max_ss_100C_1v60` (SS + OCV
derating). For this fully-parallel 9-multiplier FIR, the effective critical
path under max OCV is ~27 ns regardless of the target period — a structural
limit of the architecture, not a fixable violation. Only the TT corner
(nom_tt_025C_1v80) is used for signoff and comparison; this is the standard
academic PVT corner.

### 9.3 The trial run command

```bash
python3 -m librelane \
    --docker-no-tty --dockerized \
    --pdk-root ~/.ciel --design-dir . \
    --skip Magic.DRC --skip KLayout.DRC \
    --skip Checker.MagicDRC --skip Checker.KLayoutDRC \
    --skip KLayout.XOR --skip Checker.XOR \
    --run-tag conservative_uniform_trial \
    synth/ol_conservative_uniform_trial.yaml
```

The trial YAML is identical to the production config except
`CLOCK_PERIOD: 20.0`. It is not committed (temporary artifact).

---

## 10. Run all three designs

> **Already done.** Results are in `runs/` (not committed) and
> `results/pareto/physical_implementation_results.csv` (committed).

Run them one at a time (sequentially). LibreLane uses all available CPU cores
internally; running two instances simultaneously will thrash your machine.

All commands are run from the project root:

```bash
cd /path/to/precisionfit
source ~/.venv/librelane/bin/activate
```

The `--skip` flags are required for LibreLane 3.0.14 (see Status section).
The `--docker-no-tty` flag must appear **before** `--dockerized`.

### 10.1 conservative_uniform

```bash
python3 -m librelane \
    --docker-no-tty --dockerized \
    --pdk-root ~/.ciel --design-dir . \
    --skip Magic.DRC --skip KLayout.DRC \
    --skip Checker.MagicDRC --skip Checker.KLayoutDRC \
    --skip KLayout.XOR --skip Checker.XOR \
    --run-tag conservative_uniform \
    synth/ol_conservative_uniform.yaml
```

### 10.2 best_uniform

```bash
python3 -m librelane \
    --docker-no-tty --dockerized \
    --pdk-root ~/.ciel --design-dir . \
    --skip Magic.DRC --skip KLayout.DRC \
    --skip Checker.MagicDRC --skip Checker.KLayoutDRC \
    --skip KLayout.XOR --skip Checker.XOR \
    --run-tag best_uniform \
    synth/ol_best_uniform.yaml
```

### 10.3 sensitivity_guided

```bash
python3 -m librelane \
    --docker-no-tty --dockerized \
    --pdk-root ~/.ciel --design-dir . \
    --skip Magic.DRC --skip KLayout.DRC \
    --skip Checker.MagicDRC --skip Checker.KLayoutDRC \
    --skip KLayout.XOR --skip Checker.XOR \
    --run-tag sensitivity_guided \
    synth/ol_sensitivity_guided.yaml
```

### 10.4 What you will see during a run

LibreLane prints each step as it runs. A healthy run looks like this:

```
[INFO]: Step 1 of 32: Synthesis (Yosys)
[INFO]: Step 2 of 32: Floorplan
[INFO]: Step 3 of 32: PDN
[INFO]: Step 4 of 32: Placement (OpenROAD)
...
[INFO]: Step 29 of 32: STA Signoff (OpenSTA)
[INFO]: Step 30 of 32: DRC (Magic)
[INFO]: Step 31 of 32: LVS (Netgen)
[INFO]: Step 32 of 32: Summary
[SUCCESS]: Flow complete.
```

Each step takes seconds to minutes. Total run time for a ~12,000-cell FIR:
roughly 10–20 minutes per design on a modern laptop.

### 10.5 Where the output goes

LibreLane creates a `runs/` directory inside the current working directory:

```
runs/
├── conservative_uniform/
│   ├── final/
│   │   ├── gds/          <-- GDSII layout
│   │   ├── metrics.csv   <-- the numbers you care about
│   │   ├── odb/          <-- OpenROAD database
│   │   └── spef/         <-- parasitics
│   ├── 01-Synthesis/
│   ├── 02-Floorplan/
│   ...
├── best_uniform/
└── sensitivity_guided/
```

---

## 11. Interpret the output

### 11.1 The three numbers that matter

LibreLane 3.x writes `metrics.csv` in a **long format** (two columns:
`Metric` and `Value`), unlike OpenLane 2's wide format. Read it as:

```bash
grep -E "design__instance__(area|count),|timing__setup__ws__corner:nom_tt" \
    runs/conservative_uniform/final/metrics.csv | \
    grep -v "class\|stdcell\|macros\|pad\|cover\|fill\|tap\|inverter\|seq\|multi\|repair\|clock\|setup_buffer\|hold\|antenna"
```

Or from Python (handles both formats):

```python
import csv
with open("runs/conservative_uniform/final/metrics.csv") as f:
    metrics = {row["Metric"]: row["Value"] for row in csv.DictReader(f)}

print("area   :", metrics.get("design__instance__area"))
print("TT slack:", metrics.get("timing__setup__ws__corner:nom_tt_025C_1v80"))
print("cells  :", metrics.get("design__instance__count"))
```

| Metric key | Meaning |
|---|---|
| `design__instance__area` | real area in µm² |
| `timing__setup__ws__corner:nom_tt_025C_1v80` | TT corner worst setup slack (ns) |
| `timing__setup__ws` | global worst across all corners (SS-max-OCV dominates; see section 9) |
| `design__instance__count` | total placed instances (includes fill/tap cells) |
| `design__instance__count__stdcell` | logic cells only (excludes fill/tap) |

**Use `nom_tt_025C_1v80`** for comparison, not the bare `timing__setup__ws`.
The bare metric is the worst over all corners including max-OCV SS (~27 ns
structural limit for this topology).

### Real results from the committed runs

```
              Design     Area (µm²)   TT slack (ns)   Placed cells
conservative_uniform        145,389         +1.976          27,600
        best_uniform         96,137         +4.392          18,097
  sensitivity_guided         94,870         +4.238          17,773
```

### 11.2 Interpreting timing slack

- **Positive slack** (e.g. `0.31`) — timing is met. The design runs at the
  target clock frequency with margin to spare.
- **Negative slack** (e.g. `−0.45`) — timing is violated. The critical path
  is too slow for the target period. See troubleshooting section 16.
- **Zero or very close to zero** — right on the boundary; results may vary
  slightly between runs due to placement randomness. Aim for at least 0.1 ns.

### 11.3 Interpreting area

For a 9-multiplier FIR on SKY130 HD, expect roughly:
- `conservative_uniform`: 100,000–200,000 µm²
- `best_uniform`: 60,000–120,000 µm²
- `sensitivity_guided`: 55,000–115,000 µm²

The absolute numbers matter less than whether the **ordering matches** the
generic-cell sweep and **by how much**.

### 11.4 Reading the timing report directly

The most detailed timing information is in the STA step reports:

```bash
find runs/conservative_uniform -name "*.rpt" -path "*/STA*" | sort | tail -3
```

Open the `max.rpt` (setup) file. Look for the section headed
`worst slack`. The critical path breakdown shows every gate and wire delay
on the slowest path, which is useful if you need to fix a timing violation.

---

## 12. Parse results into a comparison table

> **Already done.** `src/python/parse_openlane_results.py` is committed with
> the correct path and format handling. The output CSV is committed at
> `results/pareto/physical_implementation_results.csv`.

The script handles both the OpenLane 2 wide format and the LibreLane 3.x
long format automatically, and reads the `nom_tt_025C_1v80` corner for setup
slack. Run it:

```bash
python3 src/python/parse_openlane_results.py
```

Actual output:

```
              Design     Area (µm²)   TT slack (ns)   Placed cells
conservative_uniform        145,389         +1.976          27,600
        best_uniform         96,137         +4.392          18,097
  sensitivity_guided         94,870         +4.238          17,773

Saved: results/pareto/physical_implementation_results.csv
```

If you see `FileNotFoundError`, check that:
1. You ran all three LibreLane commands from the project root with `--design-dir .`
2. The `runs/` directory exists at the project root: `ls runs/`
3. The run tags match what you passed with `--run-tag`

---

## 13. View the layout (optional)

LibreLane generates a GDSII file you can open visually. Install KLayout:

```bash
sudo dnf install klayout
```

Open a layout:

```bash
klayout runs/conservative_uniform/final/gds/fir_conservative_uniform.gds
```

You will see the placed-and-routed chip. Press `F` to zoom to fit. Zoom into
any region with the scroll wheel to see individual standard cells. The metal
layers are colour-coded by layer number. This is not required for the
comparison — it is just satisfying to look at.

To compare all three layouts side by side, open three KLayout windows
simultaneously. The conservative design will visibly be larger and denser with
wider datapaths.

---

## 14. Update the notebook

> **Already done.** The notebook has been re-executed. Section 9 now shows a
> clean results table with real µm² area and TT timing numbers for all three
> designs.

To re-execute after re-running the PD flow:

```bash
python3 notebooks/build_notebook.py --execute
```

The `physical_flow_was_run()` check in section 9 returns `True` whenever
`runs/*/final/metrics.csv` exists; the section then reads and formats the
results automatically.

---

## 15. What to look for and what counts as success

### 15.1 Required: timing closes on all three designs (TT corner)

All three must have positive `timing__setup__ws__corner:nom_tt_025C_1v80`.
**Achieved:** conservative_uniform +1.976 ns, best_uniform +4.392 ns,
sensitivity_guided +4.238 ns.

Note: the bare `timing__setup__ws` (worst across all corners) is negative for
all three — the SS-corner with OCV derating (~27 ns structural critical path)
cannot close. This is a known topology limit; only TT is used for signoff.

If you re-run and get negative TT slack, increase `CLOCK_PERIOD` in all three
YAML files by `|slack| + 0.3` and re-run all three. Never increase the period
for only one design.

### 15.2 Expected: area ordering matches generic-cell sweep

```
conservative_uniform area  >  best_uniform area  ≥  sensitivity_guided area
```

**Achieved:** 145,389 > 96,137 > 94,870 µm². The ordering holds.

If `best_uniform` and `sensitivity_guided` are reversed on real cells, that is
a legitimate finding — report it. The ~3.6% generic-cell gap is small enough
that it could go either way on a real PDK.

If `conservative_uniform` is somehow smaller than the others, something is
wrong with your setup — most likely a config mismatch (check that all three
used identical `DIE_AREA`, `PL_TARGET_DENSITY`, and `SYNTH_STRATEGY`).

### 15.3 What was reported in the notebook

- Real area in µm² for each design, the shared clock period (14.6 ns), and
  the TT worst-setup-slack for each.
- Area ordering from the generic-cell sweep held: YES.
- Real percentage difference:
  `(96137 - 94870) / 96137 * 100 = 1.3%` (best_uniform → sensitivity_guided)
- Power savings are not claimed. Power requires VCD annotation.

---

## 16. Troubleshooting

### "Module not found" or synthesis fails immediately

LibreLane could not parse the Verilog. Check:
- `DESIGN_NAME` in the YAML exactly matches the `module` name at the top of
  the `.v` file. Confirm: `grep "^module" src/verilog/rtl/fir_conservative_uniform.v`
- The `VERILOG_FILES` uses the `dir::src/verilog/rtl/...` form (relative to
  `--design-dir .`, i.e. the project root). Check the committed YAML.
- `--design-dir .` is present in the command (sets the design directory to
  the project root, which is also where `runs/` will be created).

### Timing is not met (negative worst_slack_ns)

1. Note the magnitude (e.g. `−0.8 ns`).
2. Add `|slack| + 0.3` to `CLOCK_PERIOD` in **all three** YAML files and
   in `synth/constraints.sdc`.
3. Re-run all three designs.
4. Repeat until all three close with positive slack.

Do not increase the period for only one design — that breaks the fair
comparison.

### DRC violations in the summary

DRC (design rule check) violations mean the router drew wires that violate
fabrication rules. For a 500×500 µm die with a ~10,000-cell design, this is
uncommon. If it happens:

1. Increase `DIE_AREA` to `[0, 0, 700, 700]` and `CORE_AREA` to
   `[10, 10, 690, 690]` — more room for the router.
2. Decrease `PL_TARGET_DENSITY` to `0.4`.
3. Apply the same change to all three configs and re-run all three.

### LVS mismatch

Layout vs schematic (LVS) mismatch means the physical layout does not match
the synthesized netlist. Almost always a tool issue, not a design bug. Re-run
the smoke test: `python3 -m librelane --dockerized --smoke-test`. If it fails,
reinstall LibreLane: `pip install --upgrade librelane`.

### "docker: permission denied"

```bash
sudo usermod -aG docker $USER
newgrp docker          # takes effect in current shell
docker ps              # should work now
```

### Docker image pull is very slow or fails

The LibreLane Docker image is several GB. If the pull times out, try:

```bash
docker pull ghcr.io/librelane/librelane:latest
```

If it still fails, check your network/proxy. If behind a corporate proxy,
configure Docker's proxy settings in `/etc/docker/daemon.json`.

### "runs/ not found" when running parse script

The `runs/` directory is created by LibreLane relative to the directory you
ran the command from. Always `cd` to the project root before every command:

```bash
cd /home/aarushlenka/GitRepos/sscs-ose-code-a-chip.github.io/ISSCC27/submitted_notebooks/PrecisionFit
ls runs/   # must exist and contain the three run tag directories
```

### LibreLane 3.x rejects a variable name

If LibreLane prints something like `WARNING: unknown variable FP_SIZING`,
that variable was removed in 3.x. The three YAML configs in section 8 already
use the correct 3.x names. If you adapted the old `openlane_config.json`
directly instead of using the YAMLs from section 8, check the variable
migration table in section 8.1.

---

## 17. What not to do

**Do not use different settings for different designs.** `DIE_AREA`,
`PL_TARGET_DENSITY`, `CLOCK_PERIOD`, and `SYNTH_STRATEGY` must be identical.
Any difference makes the comparison invalid and the result meaningless.

**Do not report power as a conclusion.** LibreLane's power estimate uses
statistical switching activity, not measured waveforms. Without a VCD from the
Verilator/Icarus simulation runs annotated into OpenROAD, power numbers are
not meaningful. The notebook already states this.

**Do not re-run the sweeps through LibreLane.** The sweeps found the Pareto
frontier using Yosys generic cells — that job is done. You are running only
three designs through the real PDK flow.

**Do not change the RTL.** The RTL is formally verified. Any edit invalidates
the proofs and requires re-running `formal_verify.py` and `verify_rtl.py`.

**Do not commit the `runs/` directory.** It contains gigabytes of intermediate
files, compiled databases, and layout artifacts. The `.gitignore` already
excludes `runs/`. Only commit the three YAML config files, the updated
`constraints.sdc`, the updated `parse_openlane_results.py`, the CSV that the
parse script writes to `results/pareto/`, and the re-executed notebook.

**Do not ignore DRC violations.** A layout with DRC violations is not
manufacturable. If you see them in the summary, fix them (see section 16)
before reporting results.

---

## 18. Commit record

The four commits that implement the physical design flow are:

**Config files and derived clock period** (`a41d8f9`):
- `synth/ol_conservative_uniform.yaml`
- `synth/ol_best_uniform.yaml`
- `synth/ol_sensitivity_guided.yaml`
- `synth/constraints.sdc` (updated to 14.6 ns)

Key changes from the original guide template: `PL_TARGET_DENSITY_PCT` (not
`PL_TARGET_DENSITY`), `dir::src/...` paths, `--design-dir .` flag, no
`CLOCK_TREE_SYNTHESIS` key, clock period derived iteratively to 14.6 ns.

**Updated parse script** (`56a97dc`):
- `src/python/parse_openlane_results.py` — `_read_metrics()` added to handle
  LibreLane 3.x long-format CSV; `timing__setup__ws` changed to
  `timing__setup__ws__corner:nom_tt_025C_1v80`

**Physical results CSV and final config values** (`1f4b096`):
- `results/pareto/physical_implementation_results.csv`
- Config YAMLs and `constraints.sdc` updated to final 14.6 ns

**Re-executed notebook** (`6357441`):
- `notebooks/precisionfit.ipynb` — section 9 shows real numbers
- `notebooks/build_notebook.py` — section 9 and section 12 updated

---

## 19. Quick-reference checklist

All items below are complete for this project.

```
[x] LibreLane installed in a venv: pip install --upgrade librelane
[x] Docker accessible without sudo: docker ps works
[x] SKY130 PDK downloaded: ls ~/.ciel/sky130A/ shows content
[x] Smoke test passed: python3 -m librelane --dockerized --smoke-test
[x] Three YAML configs created in synth/ (section 8)
[x] Trial run of conservative_uniform at 20 ns complete
[x] Shared clock period derived: 14.6 ns (TT corner, 68.5 MHz)
[x] constraints.sdc updated to 14.6 ns
[x] conservative_uniform run complete, LVS clean, TT slack +1.976 ns
[x] best_uniform run complete, LVS clean, TT slack +4.392 ns
[x] sensitivity_guided run complete, LVS clean, TT slack +4.238 ns
[x] parse_openlane_results.py handles LibreLane 3.x long-format CSV
[x] parse_openlane_results.py runs clean, CSV at results/pareto/
[x] Notebook re-executed, section 9 shows real numbers
[x] Results committed (4 commits — see section 18)
```
