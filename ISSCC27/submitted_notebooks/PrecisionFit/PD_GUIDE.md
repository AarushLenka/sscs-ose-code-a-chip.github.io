# PrecisionFit — Physical Design Guide (OpenLane2 + SKY130)

This document walks you through taking the three PrecisionFit headline designs
all the way from the generated Verilog files to placed-and-routed layouts on
the SkyWater SKY130 open PDK, producing real area (µm²), real timing (worst
setup slack), and gate-level netlists you can hand to OpenROAD for further
analysis.

Everything here uses only free, open-source tools. No licence dongle, no
commercial EDA tool, no university account needed.

---

## Table of contents

1. [What you are doing and why](#1-what-you-are-doing-and-why)
2. [Concepts you need to know first](#2-concepts-you-need-to-know-first)
3. [The three designs you will run](#3-the-three-designs-you-will-run)
4. [Install the tools](#4-install-the-tools)
5. [Verify the installation](#5-verify-the-installation)
6. [Understand the existing scaffolding](#6-understand-the-existing-scaffolding)
7. [Derive the shared clock period](#7-derive-the-shared-clock-period)
8. [Run conservative_uniform through OpenLane2](#8-run-conservative_uniform-through-openlane2)
9. [Interpret the output](#9-interpret-the-output)
10. [Run best_uniform and sensitivity_guided](#10-run-best_uniform-and-sensitivity_guided)
11. [Parse results into a comparison table](#11-parse-results-into-a-comparison-table)
12. [Update the notebook](#12-update-the-notebook)
13. [What to look for and what counts as a success](#13-what-to-look-for-and-what-counts-as-a-success)
14. [Troubleshooting common errors](#14-troubleshooting-common-errors)
15. [What not to do](#15-what-not-to-do)
16. [Commit strategy](#16-commit-strategy)

---

## 1. What you are doing and why

Up to this point, "area" in this project means **synthesized generic-cell
count** — the number of 2-input CMOS gates Yosys produces when it maps the
design to a technology-independent library. That number is real and
reproducible, but it is not µm². Two designs with the same generic-cell count
can have different real areas depending on how well they place and route.

Physical design (PD) is the process of:

1. **Logic synthesis to a real library** — mapping the RTL to actual SKY130
   standard cells (flip-flops, NAND gates, muxes, etc.) with known drive
   strengths and timing arcs.
2. **Floorplanning** — deciding the die size and where the I/O pins go.
3. **Placement** — deciding where each standard cell sits on the die.
4. **Clock tree synthesis (CTS)** — inserting buffers so the clock arrives at
   every flip-flop at nearly the same time.
5. **Routing** — drawing the metal wires that connect the cells.
6. **Timing signoff** — checking that every data path meets setup and hold
   timing at the target clock frequency.

After PD you will have:
- Real area numbers in µm² for each of the three headline designs.
- A real worst-setup-slack number (positive = timing met, negative = violated).
- A GDSII file (the physical layout) that could, in principle, be sent to a
  fab.

The tool that does all of this automatically for SKY130 is **OpenLane2**.

---

## 2. Concepts you need to know first

**PDK (Process Design Kit)** — a collection of files that describe a
semiconductor manufacturing process: the standard cells (their schematics,
layouts, timing models), the metal stack, the design rules. You will use
**SKY130**, an open PDK from SkyWater Technology for their 130 nm process.

**Standard cell** — a pre-designed, pre-characterized logic gate (e.g.
`sky130_fd_sc_hd__and2_1`) with known area, timing, and power. The HD
(high-density) variant of SKY130 is what this project targets.

**Liberty file (.lib)** — a text file that lists every standard cell's timing
arcs (input→output delays), capacitances, and power numbers at a specific
corner (temperature + voltage). You will use the TT (typical-typical) corner:
25 °C, 1.8 V.

**LEF file** — describes the physical footprint (width, height, pin positions)
of each standard cell. The router uses this to place cells without overlap.

**SDC (Synopsys Design Constraints)** — a Tcl script that tells the tool your
timing requirements: clock frequency, input arrival times, output required
times. The file already exists at `synth/constraints.sdc`.

**OpenLane2** — an automated RTL-to-GDSII flow built on top of OpenROAD,
Yosys, Magic, Netgen, and other open-source tools. You give it a JSON config
file and it runs the entire synthesis + PD + signoff sequence automatically.

**OpenROAD** — the underlying place-and-route engine inside OpenLane2. You
will not invoke it directly; OpenLane2 calls it for you.

**Nix** — a package manager used by OpenLane2 to install itself and all its
dependencies in a reproducible, isolated way. You do not need to know Nix
deeply; you only need to install it and use one command.

---

## 3. The three designs you will run

| Config name | RTL file | Generic cells (from sweep) |
|---|---|---|
| `conservative_uniform` | `src/verilog/rtl/fir_conservative_uniform.v` | 17,819 |
| `best_uniform` | `src/verilog/rtl/fir_best_uniform.v` | 11,466 |
| `sensitivity_guided` | `src/verilog/rtl/fir_sensitivity_guided.v` | 11,048 |

You must run them with **identical settings** — same die area, same target
density, same clock period — so that any area or timing difference in the PD
results comes only from the designs themselves, not from the tool settings.
This is non-negotiable for a fair comparison.

---

## 4. Install the tools

### 4.1 Install Nix

OpenLane2 is distributed and installed through Nix. You only need to do this
once on your machine.

```bash
sh <(curl -L https://nixos.org/nix/install) --daemon
```

When it finishes, close your terminal and open a new one. Verify:

```bash
nix --version
# should print something like: nix (Nix) 2.x.x
```

If the command is not found, your shell profile was not updated. Add this to
`~/.zshrc` (or `~/.bashrc`) and re-open the terminal:

```bash
. /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh
```

### 4.2 Enable the Nix flakes feature

OpenLane2 requires Nix flakes. Create or edit `~/.config/nix/nix.conf`:

```
experimental-features = nix-command flakes
```

Then verify:

```bash
nix flake --help 2>&1 | head -1
# should print: Usage: nix flake ...
```

### 4.3 Install OpenLane2

This downloads OpenLane2 and the entire SKY130 PDK (around 3–5 GB total).
Choose a permanent home for it — it does not need to be inside this project.

```bash
# pick a directory, e.g. your home folder
cd ~
git clone https://github.com/efabless/openlane2
cd openlane2
```

Now install and verify it runs (this compiles the full tool stack the first
time and takes 20–40 minutes):

```bash
nix run .#openlane -- --version
# should print: OpenLane X.Y.Z
```

You do not need to run `pip install`, `make`, or any other build step. Nix
handles the entire dependency closure.

### 4.4 Download the SKY130 PDK

OpenLane2 can download the PDK for you. From inside the openlane2 clone:

```bash
nix run .#openlane -- --pdk sky130A --list-pdks
```

If it says SKY130 is not present it will offer to install it. Alternatively,
use volare (the PDK version manager that ships with OpenLane2):

```bash
nix run .#openlane -- --smoke-test
```

This runs a built-in self-test on a tiny design and confirms that both the
tool and the PDK are working. It takes 5–10 minutes. You should see
`Smoke test passed` at the end.

---

## 5. Verify the installation

Before touching the PrecisionFit project, confirm OpenLane2 works end-to-end
by running its built-in smoke test:

```bash
cd ~/openlane2
nix run .#openlane -- --smoke-test
```

Expected last line: `Smoke test passed.`

If you see errors here, fix the installation before proceeding. Common causes:

- Nix flakes not enabled (see 4.2).
- Disk full — the PDK + tool closures are large; you need at least 15 GB free.
- Proxy / firewall blocking the PDK download. If so, set `NIX_CONFIG` or
  configure a Nix substituter; see the OpenLane2 documentation.

---

## 6. Understand the existing scaffolding

The project already has three files in `synth/` that you will use directly.
Read them before modifying anything.

**`synth/openlane_config.json`** — the OpenLane2 configuration. The current
active design is `fir_best_uniform`. You will create three copies, one per
headline design. Key fields:

| Field | Meaning |
|---|---|
| `DESIGN_NAME` | must match the Verilog module name exactly |
| `VERILOG_FILES` | path to the RTL file, relative to the config |
| `CLOCK_PORT` | name of the clock port in the RTL (`clk`) |
| `CLOCK_PERIOD` | in nanoseconds — **one shared value for all three runs** |
| `DIE_AREA` | `"0 0 500 500"` means a 500×500 µm die |
| `PL_TARGET_DENSITY` | 0.5 = cells occupy 50% of the core area |
| `SYNTH_STRATEGY` | `AREA 0` tells Yosys/ABC to optimise for area |

**`synth/constraints.sdc`** — the timing constraints. The clock period here
must match `CLOCK_PERIOD` in the JSON. Section 7 below explains how to derive
the right value.

**`synth/sky130.tcl`** — an OpenROAD script for driving the flow manually
(path B from the comment). You will not use this directly; OpenLane2 path A is
simpler and less error-prone.

**`src/python/parse_openlane_results.py`** — a script that reads the
`metrics.csv` that OpenLane2 produces after each run and builds a comparison
table. You will run this after all three PD runs are complete.

---

## 7. Derive the shared clock period

You must use the **same** clock period for all three designs. If you let each
design pick its own tightest-possible period, you would be measuring timing
performance differences, not area differences, and the comparison would be
meaningless.

The rule: find the period at which the **widest** design (`conservative_uniform`,
17,819 cells) closes timing with small positive slack (say, 0.1–0.5 ns), and
use that period for all three.

### 7.1 Run conservative_uniform at a loose period first

Start with `CLOCK_PERIOD = 20.0` (50 MHz) in a trial run. This gives the
widest design plenty of room — the goal is not to close timing here, just to
see where the critical path is.

Create `synth/ol_conservative_uniform_trial.json`:

```json
{
  "DESIGN_NAME": "fir_conservative_uniform",
  "VERILOG_FILES": "dir::../src/verilog/rtl/fir_conservative_uniform.v",
  "CLOCK_PORT": "clk",
  "CLOCK_PERIOD": 20.0,
  "FP_SIZING": "absolute",
  "DIE_AREA": "0 0 500 500",
  "PL_TARGET_DENSITY": 0.5,
  "SYNTH_STRATEGY": "AREA 0",
  "RUN_CTS": true
}
```

Run it from the PrecisionFit project root:

```bash
cd /home/aarushlenka/GitRepos/sscs-ose-code-a-chip.github.io/ISSCC27/submitted_notebooks/PrecisionFit

nix run ~/openlane2#openlane -- \
    synth/ol_conservative_uniform_trial.json \
    --run-tag conservative_uniform_trial
```

OpenLane2 creates a directory `openlane2/runs/conservative_uniform_trial/`.
Inside it, find the timing report:

```bash
find openlane2/runs/conservative_uniform_trial -name "*.rpt" | grep timing | head -5
```

Open the setup timing report. Look for the line that says:

```
wns <value>   # worst negative slack
```

With a 20 ns period, `wns` will be a large positive number (e.g. 12.5 ns),
meaning you have 12.5 ns of slack to give back. The critical path delay is
roughly `20 - wns`.

### 7.2 Calculate the target period

```
critical_path_delay  ≈  CLOCK_PERIOD  −  wns
target_period        =  critical_path_delay  +  0.3   (small positive slack)
```

Example: if `wns = 12.5` with `CLOCK_PERIOD = 20.0`:
```
critical_path_delay = 20.0 − 12.5 = 7.5 ns
target_period       = 7.5 + 0.3   = 7.8 ns
```

Round up to one decimal place (e.g. 7.8 ns). This is your shared clock period.
Write it down.

### 7.3 Update both config and SDC

Edit `synth/constraints.sdc` — change the period on this line:

```tcl
create_clock -name clk -period 7.8 [get_ports clk]   ;# replace 10.0 with your value
```

You will use this same value in every `CLOCK_PERIOD` field in the three JSON
configs below.

---

## 8. Run conservative_uniform through OpenLane2

### 8.1 Create the config file

Create `synth/ol_conservative_uniform.json`:

```json
{
  "DESIGN_NAME": "fir_conservative_uniform",
  "VERILOG_FILES": "dir::../src/verilog/rtl/fir_conservative_uniform.v",
  "CLOCK_PORT": "clk",
  "CLOCK_PERIOD": 7.8,
  "FP_SIZING": "absolute",
  "DIE_AREA": "0 0 500 500",
  "PL_TARGET_DENSITY": 0.5,
  "SYNTH_STRATEGY": "AREA 0",
  "RUN_CTS": true,
  "PRIMARY_SIGNOFF_TOOL": "opensta"
}
```

Replace `7.8` with the period you derived in section 7.

### 8.2 Run OpenLane2

From the PrecisionFit project root:

```bash
cd /home/aarushlenka/GitRepos/sscs-ose-code-a-chip.github.io/ISSCC27/submitted_notebooks/PrecisionFit

nix run ~/openlane2#openlane -- \
    synth/ol_conservative_uniform.json \
    --run-tag conservative_uniform
```

### 8.3 What happens during the run

OpenLane2 will print each step as it runs. A healthy run looks like this
(each step takes seconds to minutes):

```
[INFO]: Running Synthesis...           # Yosys maps RTL to SKY130 cells
[INFO]: Running Floorplan...           # die area defined, I/O pins placed
[INFO]: Running Placement...           # cells placed on the grid
[INFO]: Running CTS...                 # clock tree built
[INFO]: Running Routing...             # wires drawn
[INFO]: Running DRC...                 # design rule checks
[INFO]: Running LVS...                 # netlist vs layout check
[INFO]: Running STA...                 # final timing analysis
[INFO]: Flow complete.
```

The whole run for a ~12,000-cell FIR takes roughly 5–15 minutes on a modern
laptop.

### 8.4 What success looks like

At the end you should see no lines that say `[ERROR]` or `[CRITICAL]`. A line
like:

```
[INFO]: Flow complete.
```

means OpenLane2 finished. This does not automatically mean timing was met —
you need to check that separately (section 9).

---

## 9. Interpret the output

All output lives under `openlane2/runs/conservative_uniform/`.

### 9.1 Find the metrics file

```bash
find openlane2/runs/conservative_uniform -name "metrics.csv" | head -3
```

Open it in a terminal:

```bash
python3 -c "
import pandas as pd
df = pd.read_csv('openlane2/runs/conservative_uniform/final/metrics.csv')
print(df[['design__instance__area', 'timing__setup__ws', 'design__instance__count']].T)
"
```

The three numbers you care about:

| Column | Meaning |
|---|---|
| `design__instance__area` | real area in µm² |
| `timing__setup__ws` | worst setup slack in ns (positive = timing met) |
| `design__instance__count` | number of standard cells placed |

### 9.2 Interpreting timing

- `timing__setup__ws > 0` — timing is met. The design closes at the target
  frequency.
- `timing__setup__ws < 0` — timing is violated. The design is too slow for the
  target period. You have two options: increase `CLOCK_PERIOD` (lower the
  target frequency) or increase `PL_TARGET_DENSITY` to allow more room for
  the router to find shorter paths.
- `timing__setup__ws = 0` — right on the boundary; in practice this will vary
  run to run due to random placement seeds. Aim for a small positive value.

### 9.3 Interpreting area

The `design__instance__area` number is in µm². For a 9-multiplier FIR on
SKY130 HD, expect roughly 50,000–200,000 µm² depending on the word widths.
The absolute number matters less than the **relative** number across the three
designs, which is the entire point.

### 9.4 Viewing the layout (optional)

OpenLane2 generates a GDSII file:

```bash
find openlane2/runs/conservative_uniform -name "*.gds" | head -3
```

Open it with KLayout (install with your package manager: `sudo dnf install
klayout` on Fedora, `sudo apt install klayout` on Ubuntu):

```bash
klayout openlane2/runs/conservative_uniform/final/gds/fir_conservative_uniform.gds
```

You should see the placed-and-routed layout of the FIR filter. Zoom in to see
individual standard cells. This is not required for the comparison — it is just
satisfying to look at.

---

## 10. Run best_uniform and sensitivity_guided

### 10.1 Create the two remaining config files

Create `synth/ol_best_uniform.json` (replace `7.8` with your derived period):

```json
{
  "DESIGN_NAME": "fir_best_uniform",
  "VERILOG_FILES": "dir::../src/verilog/rtl/fir_best_uniform.v",
  "CLOCK_PORT": "clk",
  "CLOCK_PERIOD": 7.8,
  "FP_SIZING": "absolute",
  "DIE_AREA": "0 0 500 500",
  "PL_TARGET_DENSITY": 0.5,
  "SYNTH_STRATEGY": "AREA 0",
  "RUN_CTS": true,
  "PRIMARY_SIGNOFF_TOOL": "opensta"
}
```

Create `synth/ol_sensitivity_guided.json`:

```json
{
  "DESIGN_NAME": "fir_sensitivity_guided",
  "VERILOG_FILES": "dir::../src/verilog/rtl/fir_sensitivity_guided.v",
  "CLOCK_PORT": "clk",
  "CLOCK_PERIOD": 7.8,
  "FP_SIZING": "absolute",
  "DIE_AREA": "0 0 500 500",
  "PL_TARGET_DENSITY": 0.5,
  "SYNTH_STRATEGY": "AREA 0",
  "RUN_CTS": true,
  "PRIMARY_SIGNOFF_TOOL": "opensta"
}
```

**Every field except `DESIGN_NAME` and `VERILOG_FILES` must be identical
across all three JSON files.** Double-check this before running. If the die
area, density, or clock period differ, the comparison is invalid.

### 10.2 Run both

```bash
cd /home/aarushlenka/GitRepos/sscs-ose-code-a-chip.github.io/ISSCC27/submitted_notebooks/PrecisionFit

nix run ~/openlane2#openlane -- \
    synth/ol_best_uniform.json \
    --run-tag best_uniform

nix run ~/openlane2#openlane -- \
    synth/ol_sensitivity_guided.json \
    --run-tag sensitivity_guided
```

Run them one at a time (sequentially), not simultaneously — OpenLane2 uses all
available CPU cores internally, and running two in parallel will thrash your
machine.

---

## 11. Parse results into a comparison table

After all three runs complete, the script `src/python/parse_openlane_results.py`
reads the three `metrics.csv` files and produces one comparison table.

The script looks for runs under `openlane2/runs/` relative to the project root.
Run it from the project root:

```bash
cd /home/aarushlenka/GitRepos/sscs-ose-code-a-chip.github.io/ISSCC27/submitted_notebooks/PrecisionFit
python3 src/python/parse_openlane_results.py
```

Expected output (numbers will differ):

```
           config    area_um2  worst_slack_ns  cell_count
conservative_uniform  142300.0            0.31       17102
        best_uniform   89200.0            0.28       11004
  sensitivity_guided   85500.0            0.35       10621

Saved: results/pareto/physical_implementation_results.csv
```

If you get `FileNotFoundError: no OpenLane metrics found`, check that the run
tags in the script match the `--run-tag` values you used. Open
`src/python/parse_openlane_results.py` and look at the `tags` list near the
top of `parse()` — it expects exactly `conservative_uniform`, `best_uniform`,
and `sensitivity_guided`.

---

## 12. Update the notebook

The notebook's section 9 (physical implementation) is already scaffolded to
show the PD results if they exist, and to print a clean "not attempted" message
if they do not. Now that you have run the flow, re-execute the notebook so that
section displays the real numbers.

```bash
python3 notebooks/build_notebook.py --execute
```

Or open it in JupyterLab and run Kernel → Restart & Run All:

```bash
jupyter lab notebooks/precisionfit.ipynb
```

The cell that calls `parse_openlane_results.physical_flow_was_run()` will now
return `True` and the comparison table and area chart will appear with real
values instead of the placeholder message.

---

## 13. What to look for and what counts as a success

### 13.1 Timing

All three designs must close timing (positive `worst_slack_ns`). If any one
does not, you need to increase `CLOCK_PERIOD` in all three configs and re-run
all three. Do not increase the period for only one design — that breaks the
fair comparison.

### 13.2 Area ordering

The expected ordering from the generic-cell sweep is:

```
conservative_uniform  >  best_uniform  ≥  sensitivity_guided
```

If the real SKY130 area reverses the ordering of `best_uniform` and
`sensitivity_guided`, that is a legitimate and interesting result — it means
the non-uniform coefficient widths interact with the standard-cell placer
differently than the generic-cell count predicted. Report it honestly.

If `conservative_uniform` is somehow smaller than the others, something is
wrong with the run (likely a config mismatch — check that all three used
the same die area and density).

### 13.3 What to report

In the notebook, report:

- The real area in µm² for each design.
- The worst setup slack for each, and the shared clock period.
- Whether the area ordering from the generic-cell sweep held.
- The ratio: `(best_uniform - sensitivity_guided) / best_uniform * 100` — is
  the ~3.6% generic-cell advantage visible in the real layout, or has it
  shrunk/reversed?
- Do **not** report power numbers as a claim. `report_power` inside OpenROAD
  uses statistical switching activity assumptions, not measured activity.
  The power column in the metrics is not meaningful without a VCD from
  simulation. The notebook already has a note about this.

---

## 14. Troubleshooting common errors

### "Module not found" or "Cannot find design"

OpenLane2 could not parse the RTL. Check:
- `DESIGN_NAME` exactly matches the `module` name in the `.v` file.
- `VERILOG_FILES` path is correct relative to the JSON config file location.
  The `dir::` prefix means "every `.v` file in this directory"; if you point
  at a single file, use a plain relative path without `dir::`.

Open the offending `.v` file and confirm the first non-comment line is
`module fir_conservative_uniform (` (or whichever name you used).

### "Timing not met" / negative worst slack

Do not panic. Steps:

1. Note the `wns` value (e.g. −0.8 ns).
2. Increase `CLOCK_PERIOD` by `|wns| + 0.3` in **all three** JSON files and
   in `synth/constraints.sdc`.
3. Re-run all three designs.
4. Repeat until all three close timing.

If you cannot close timing even at 50 ns (20 MHz), the problem is something
other than the critical path — check for combinational loops with:

```bash
find openlane2/runs/<tag> -name "*.log" | xargs grep -l "combinational loop" 2>/dev/null
```

### "DRC violations"

Design rule check (DRC) violations mean the router drew wires that break the
fabrication rules. For a design this size with a generous die area (500×500
µm), DRC violations are rare. If they appear:

1. Increase `DIE_AREA` to `"0 0 700 700"` (more room for the router).
2. Decrease `PL_TARGET_DENSITY` to `0.4`.
3. Re-run the failing design only (do not change the other two yet — wait
   until you find settings that work, then apply the same settings to all
   three).

### "LVS mismatch"

Layout vs schematic (LVS) means the placed-and-routed netlist does not match
the synthesized netlist. This is almost always a tool issue, not a design bug.
Try:

```bash
nix run ~/openlane2#openlane -- --smoke-test
```

If the smoke test fails, your OpenLane2 installation is broken. Re-install.

### "Nix: error: cached failure of attribute"

A previous failed download is cached. Run:

```bash
nix store gc
```

Then retry.

### "openlane2/runs/ not found" when running parse script

The runs directory is created by OpenLane2 relative to wherever you called it
from. You must run both the OpenLane2 command and `parse_openlane_results.py`
from the same directory (the PrecisionFit project root). Check:

```bash
ls openlane2/runs/
```

If it does not exist, OpenLane2 was invoked from a different directory. The
simplest fix is to always `cd` to the project root before every command.

---

## 15. What not to do

**Do not use different settings for different designs.** Die area, target
density, clock period, and synthesis strategy must be identical. Any difference
makes the comparison invalid.

**Do not report power as a conclusion.** OpenROAD's `report_power` is not
measured switching activity — it is a statistical estimate. Without a VCD
annotation from the actual simulation runs, power numbers are not meaningful
and the notebook already states this explicitly.

**Do not re-run the sweeps.** The sweeps (`sweep_with_synth.py`,
`sensitivity_search.py`) used Yosys with a generic library to find the Pareto
frontier. That job is done. You are now running only the three headline
designs through the real SKY130 flow. Running all 36 + 24 passing configs
through OpenLane2 would take hours and is not the goal.

**Do not change the RTL.** The RTL has been formally verified. Any edit
invalidates the formal proofs and requires re-running `formal_verify.py` and
`verify_rtl.py` before the results can be trusted.

**Do not commit the `openlane2/` directory.** It contains gigabytes of
intermediate files, compiled PDK data, and tool binaries. The `.gitignore`
already excludes `openlane2/` and `runs/`. Only commit the three JSON config
files, the updated `constraints.sdc`, the `metrics.csv` copy that
`parse_openlane_results.py` saves under `results/pareto/`, and the re-executed
notebook.

---

## 16. Commit strategy

Make one commit per logical unit. Suggested order:

**After deriving the clock period and creating the config files:**

```bash
git add synth/ol_conservative_uniform.json \
        synth/ol_best_uniform.json \
        synth/ol_sensitivity_guided.json \
        synth/constraints.sdc
git commit -m "Add OpenLane2 config files for the three headline designs

One JSON per design (conservative_uniform, best_uniform, sensitivity_guided).
Settings are identical across all three except DESIGN_NAME and VERILOG_FILES,
ensuring a fair area comparison. Clock period derived by running
conservative_uniform at 20 ns and tightening to the critical path + 0.3 ns.
constraints.sdc updated to match."
```

**After all three PD runs complete and parse_openlane_results.py runs clean:**

```bash
git add results/pareto/physical_implementation_results.csv
git commit -m "Add physical implementation results (SKY130, OpenLane2)

Real area (um^2) and worst setup slack for all three headline designs.
All three close timing at the shared clock period. Area ordering matches
the generic-cell sweep: conservative_uniform > best_uniform >= sensitivity_guided."
```

**After re-executing the notebook:**

```bash
git add notebooks/precisionfit.ipynb
git commit -m "Re-execute notebook: include physical implementation results

Section 9 now shows real SKY130 area and timing numbers from the OpenLane2
runs. Power column noted as non-measured (no VCD annotation)."
```

---

## Quick-reference checklist

```
[ ] Nix installed and flakes enabled
[ ] OpenLane2 cloned and smoke test passes
[ ] Trial run of conservative_uniform at 20 ns period completed
[ ] Shared clock period derived and written into constraints.sdc
[ ] ol_conservative_uniform.json created with derived period
[ ] ol_best_uniform.json created with same settings
[ ] ol_sensitivity_guided.json created with same settings
[ ] All three JSON files have identical settings except DESIGN_NAME/VERILOG_FILES
[ ] conservative_uniform run completed, no [ERROR] lines
[ ] best_uniform run completed, no [ERROR] lines
[ ] sensitivity_guided run completed, no [ERROR] lines
[ ] All three have positive worst_slack_ns
[ ] parse_openlane_results.py runs clean, CSV saved
[ ] Notebook re-executed, section 9 shows real numbers
[ ] Results committed (3 commits, see section 16)
```
