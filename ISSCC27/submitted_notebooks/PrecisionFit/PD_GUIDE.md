# PrecisionFit — Physical Design Guide (LibreLane + SKY130, Docker on Fedora)

This document walks you through taking the three PrecisionFit headline designs
from generated Verilog all the way to placed-and-routed layouts on the
SkyWater SKY130 open PDK, producing real area (µm²), real worst-setup-slack
(ns), and GDSII files.

**Tool used:** LibreLane 3.x — the successor to OpenLane 2, renamed in early
2026. Same codebase, same commands, just a different name. The tool runs
entirely inside a Docker container that LibreLane pulls and manages for you.
You already have Docker 29 on Fedora 43, so you are ready to start.

Everything here is free and open-source. No licence, no account needed.

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

The existing `synth/openlane_config.json` was written for OpenLane 2. Several
variable names changed in LibreLane 3.x. Create three new YAML config files
— one per design — using the updated variable names.

### 8.1 Understanding the variable name changes

| OpenLane 2 variable | LibreLane 3.x variable | Notes |
|---|---|---|
| `FP_SIZING: "absolute"` + `DIE_AREA` | `DIE_AREA` directly | `FP_SIZING` removed; set `DIE_AREA` and `CORE_AREA` directly |
| `PL_TARGET_DENSITY` | `PL_TARGET_DENSITY` | unchanged |
| `SYNTH_STRATEGY` | `SYNTH_STRATEGY` | unchanged |
| `RUN_CTS: true` | `CLOCK_TREE_SYNTHESIS: true` | renamed |
| `PRIMARY_SIGNOFF_TOOL` | not needed | LibreLane handles this |

### 8.2 Create the three config files

Create `synth/ol_conservative_uniform.yaml`:

```yaml
# PrecisionFit -- conservative_uniform headline design
# LibreLane 3.x config (sky130A, HD standard-cell library)
# IMPORTANT: all settings below are IDENTICAL across all three headline
# configs except DESIGN_NAME and VERILOG_FILES. Do not change anything
# else without applying the same change to all three.

PDK: sky130A
DESIGN_NAME: fir_conservative_uniform
VERILOG_FILES:
  - ../src/verilog/rtl/fir_conservative_uniform.v

CLOCK_PORT: clk
CLOCK_PERIOD: 10.0          # ns -- placeholder; derive real value in section 9

DIE_AREA: [0, 0, 500, 500]  # um: generous 500x500 die so placement is not congested
CORE_AREA: [10, 10, 490, 490]

PL_TARGET_DENSITY: 0.5      # cells occupy 50% of the core area
SYNTH_STRATEGY: AREA 0      # tell Yosys/ABC to optimise for area (fair comparison)
CLOCK_TREE_SYNTHESIS: true
```

Create `synth/ol_best_uniform.yaml`:

```yaml
PDK: sky130A
DESIGN_NAME: fir_best_uniform
VERILOG_FILES:
  - ../src/verilog/rtl/fir_best_uniform.v

CLOCK_PORT: clk
CLOCK_PERIOD: 10.0          # ns -- same value as conservative_uniform

DIE_AREA: [0, 0, 500, 500]
CORE_AREA: [10, 10, 490, 490]

PL_TARGET_DENSITY: 0.5
SYNTH_STRATEGY: AREA 0
CLOCK_TREE_SYNTHESIS: true
```

Create `synth/ol_sensitivity_guided.yaml`:

```yaml
PDK: sky130A
DESIGN_NAME: fir_sensitivity_guided
VERILOG_FILES:
  - ../src/verilog/rtl/fir_sensitivity_guided.v

CLOCK_PORT: clk
CLOCK_PERIOD: 10.0          # ns -- same value as conservative_uniform

DIE_AREA: [0, 0, 500, 500]
CORE_AREA: [10, 10, 490, 490]

PL_TARGET_DENSITY: 0.5
SYNTH_STRATEGY: AREA 0
CLOCK_TREE_SYNTHESIS: true
```

Leave `CLOCK_PERIOD: 10.0` for now. Section 9 tells you how to derive the
correct value and where to update it.

---

## 9. Derive the shared clock period

You must use the **same** clock period for all three designs. The rule: find
the period at which the **widest** design (`conservative_uniform`, 17,819
cells) closes timing with small positive slack (~0.3 ns), and apply that
period to all three.

### 9.1 Run a trial at a loose period

The `conservative_uniform` design is the slowest (most cells, widest
multipliers). Run it at 20 ns (50 MHz) first — the critical path will be much
shorter than 20 ns and you will see a large positive slack that tells you
where the real critical path is.

From the project root:

```bash
cd /home/aarushlenka/GitRepos/sscs-ose-code-a-chip.github.io/ISSCC27/submitted_notebooks/PrecisionFit

python3 -m librelane --dockerized \
    --pdk-root ~/.ciel \
    --run-tag conservative_uniform_trial \
    synth/ol_conservative_uniform.yaml
```

The `--pdk-root ~/.ciel` points LibreLane at the PDK you downloaded in
section 5. You must pass this flag on every `librelane` invocation.

Wait for it to complete (5–15 minutes). Then find the timing report:

```bash
find runs/conservative_uniform_trial -name "*.rpt" | grep -i timing | head -5
```

Open the setup (max-path) report. Find this line:

```
wns <value>   # worst negative slack — positive here means timing was easy
```

With `CLOCK_PERIOD = 20.0` and a design whose critical path is ~7 ns, you
will see something like `wns 12.5`.

### 9.2 Calculate the target period

```
critical_path_delay  ≈  CLOCK_PERIOD  −  wns
                     ≈  20.0  −  12.5  =  7.5 ns

target_period        =  critical_path_delay  +  0.3 ns (small positive slack)
                     =  7.5  +  0.3  =  7.8 ns
```

Round up to one decimal place. This is your shared clock period. Write it
down.

### 9.3 Update the period everywhere

**In all three YAML files** — change `CLOCK_PERIOD: 10.0` to your derived
value (e.g. `CLOCK_PERIOD: 7.8`).

**In `synth/constraints.sdc`** — change this line:

```tcl
create_clock -name clk -period 10.0 [get_ports clk]
```

to:

```tcl
create_clock -name clk -period 7.8 [get_ports clk]
```

Use the same number in both places. They describe the same constraint; they
must match.

---

## 10. Run all three designs

Run them one at a time (sequentially). LibreLane uses all available CPU cores
internally; running two instances simultaneously will thrash your machine.

All commands are run from the project root:

```bash
cd /home/aarushlenka/GitRepos/sscs-ose-code-a-chip.github.io/ISSCC27/submitted_notebooks/PrecisionFit
```

### 10.1 conservative_uniform

```bash
python3 -m librelane --dockerized \
    --pdk-root ~/.ciel \
    --run-tag conservative_uniform \
    synth/ol_conservative_uniform.yaml
```

### 10.2 best_uniform

```bash
python3 -m librelane --dockerized \
    --pdk-root ~/.ciel \
    --run-tag best_uniform \
    synth/ol_best_uniform.yaml
```

### 10.3 sensitivity_guided

```bash
python3 -m librelane --dockerized \
    --pdk-root ~/.ciel \
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

After each run, read `runs/<tag>/final/metrics.csv`:

```bash
python3 - <<'EOF'
import pandas as pd, sys
tag = sys.argv[1] if len(sys.argv) > 1 else "conservative_uniform"
df = pd.read_csv(f"runs/{tag}/final/metrics.csv")
for col in ["design__instance__area", "timing__setup__ws", "design__instance__count"]:
    val = df[col].iloc[0] if col in df.columns else "NOT FOUND"
    print(f"{col:40s}: {val}")
EOF
```

Or pass the tag as argument:

```bash
python3 - conservative_uniform <<'EOF'
...
EOF
```

| Column | Meaning |
|---|---|
| `design__instance__area` | real area in µm² |
| `timing__setup__ws` | worst setup slack in ns (positive = timing met) |
| `design__instance__count` | number of placed standard cells |

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

The script `src/python/parse_openlane_results.py` reads the three
`metrics.csv` files and builds one comparison table.

The script currently looks for runs under `openlane2/runs/` (the old
OpenLane2 path). You need to update it to point at `runs/` (LibreLane's
default output directory). Open the file:

```bash
# line to change:
RUNS_DIR = paths.ROOT / "openlane2" / "runs"
# change to:
RUNS_DIR = paths.ROOT / "runs"
```

Make that edit:
<br>
```python
# src/python/parse_openlane_results.py  -- change this one line
RUNS_DIR = paths.ROOT / "runs"
```

Then run:

```bash
python3 src/python/parse_openlane_results.py
```

Expected output (numbers are illustrative):

```
              config    area_um2  worst_slack_ns  cell_count
conservative_uniform  142300.0            0.31       17102
        best_uniform   89200.0            0.28       11004
  sensitivity_guided   85500.0            0.35       10621

Saved: results/pareto/physical_implementation_results.csv
```

If you see `FileNotFoundError`, check that:
1. You ran all three LibreLane commands from the project root.
2. The `runs/` directory exists at the project root: `ls runs/`
3. The run tags in the script match what you passed with `--run-tag`.

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

The notebook's section 9 is already scaffolded to call
`parse_openlane_results.physical_flow_was_run()`. Now that you have completed
the PD runs and updated `RUNS_DIR` in the script, that function returns `True`
and the section will display the real numbers.

After updating `parse_openlane_results.py` and confirming the CSV is written,
re-execute the notebook:

```bash
python3 notebooks/build_notebook.py --execute
```

Or in JupyterLab: Kernel → Restart & Run All.

Section 9 will now show a comparison table with real µm² area and timing
numbers instead of the "not attempted" placeholder.

---

## 15. What to look for and what counts as success

### 15.1 Required: timing closes on all three designs

All three must have positive `worst_slack_ns`. If any fails, increase
`CLOCK_PERIOD` in all three YAML files by `|slack| + 0.3` and re-run all
three. Never increase the period for only one design.

### 15.2 Expected: area ordering matches generic-cell sweep

```
conservative_uniform area  >  best_uniform area  ≥  sensitivity_guided area
```

If `best_uniform` and `sensitivity_guided` are reversed on real cells, that is
a legitimate finding — report it. It means the non-uniform per-tap widths
interact with the SKY130 placer/router differently than the generic-cell count
predicted. The ~3.6% generic-cell gap is small enough that it could go either
way on a real PDK.

If `conservative_uniform` is somehow smaller than the others, something is
wrong with your setup — most likely a config mismatch (check that all three
used identical `DIE_AREA`, `PL_TARGET_DENSITY`, and `SYNTH_STRATEGY`).

### 15.3 What to report in the notebook

- Real area in µm² for each design, the shared clock period, and the
  worst-setup-slack for each.
- Whether the area ordering from the generic-cell sweep held.
- The real percentage difference:
  `(best_uniform_area - sensitivity_guided_area) / best_uniform_area * 100`
- Do **not** claim power savings from area reduction. Power requires VCD
  annotation. The notebook already has a note about this and it should stay.

---

## 16. Troubleshooting

### "Module not found" or synthesis fails immediately

LibreLane could not parse the Verilog. Check:
- `DESIGN_NAME` in the YAML exactly matches the `module` name at the top of
  the `.v` file. Open `src/verilog/rtl/fir_conservative_uniform.v` and confirm
  the first non-comment line is `module fir_conservative_uniform (`.
- The `VERILOG_FILES` path is correct relative to the YAML config. The configs
  live in `synth/` and the RTL is at `../src/verilog/rtl/...` — one level up.
  Verify: `ls synth/../src/verilog/rtl/fir_conservative_uniform.v`

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

## 18. Commit strategy

One commit per logical unit. Suggested order and messages:

**After creating the config files and deriving the clock period:**

```bash
git add synth/ol_conservative_uniform.yaml \
        synth/ol_best_uniform.yaml \
        synth/ol_sensitivity_guided.yaml \
        synth/constraints.sdc
git commit -m "Add LibreLane 3.x config files for the three headline designs

One YAML per design (conservative_uniform, best_uniform, sensitivity_guided).
All settings identical across the three except DESIGN_NAME and VERILOG_FILES,
ensuring a fair area/timing comparison. Clock period derived by running
conservative_uniform at 20 ns and tightening to the critical path + 0.3 ns.
constraints.sdc updated to match. Converted from the old OpenLane2 JSON
format; FP_SIZING removed, DIE_AREA/CORE_AREA set directly, RUN_CTS renamed
to CLOCK_TREE_SYNTHESIS."
```

**After updating parse_openlane_results.py:**

```bash
git add src/python/parse_openlane_results.py
git commit -m "Update parse_openlane_results.py for LibreLane runs directory

RUNS_DIR changed from 'openlane2/runs' to 'runs' to match LibreLane's
default output location."
```

**After all three PD runs complete:**

```bash
git add results/pareto/physical_implementation_results.csv
git commit -m "Add physical implementation results (LibreLane, SKY130)

Real area (um^2) and worst setup slack for all three headline designs.
All three close timing at <X.X> ns (<Y> MHz). Area ordering [matches /
does not match] the generic-cell sweep result."
```

**After re-executing the notebook:**

```bash
git add notebooks/precisionfit.ipynb
git commit -m "Re-execute notebook: add physical implementation section

Section 9 now shows real SKY130 area and timing numbers from LibreLane runs.
Power column noted as statistical estimate only (no VCD annotation)."
```

---

## 19. Quick-reference checklist

```
[ ] LibreLane installed in a venv: pip install --upgrade librelane
[ ] Docker accessible without sudo: docker ps works
[ ] SKY130 PDK downloaded: ls ~/.ciel/sky130A/ shows content
[ ] Smoke test passed: python3 -m librelane --dockerized --smoke-test
[ ] Three YAML configs created in synth/ (section 8)
[ ] Trial run of conservative_uniform at 20 ns complete
[ ] Shared clock period derived and written into all three YAMLs
[ ] constraints.sdc updated to match the derived period
[ ] conservative_uniform run complete, no [ERROR], positive slack
[ ] best_uniform run complete, no [ERROR], positive slack
[ ] sensitivity_guided run complete, no [ERROR], positive slack
[ ] RUNS_DIR updated in parse_openlane_results.py
[ ] parse_openlane_results.py runs clean, CSV written to results/pareto/
[ ] Notebook re-executed, section 9 shows real numbers
[ ] Results committed (4 commits — see section 18)
```
