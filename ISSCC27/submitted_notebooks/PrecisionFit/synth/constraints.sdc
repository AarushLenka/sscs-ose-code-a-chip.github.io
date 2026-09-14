# Shared timing constraint for all three candidate designs (Week 4, optional).
#
# The rule the guide sets: pick ONE period that the WIDEST candidate
# (conservative uniform) closes with small positive slack, and apply it to all
# three. Choosing a period only the narrow designs meet would silently reward
# narrower designs on timing for a reason unrelated to the accuracy-vs-area
# claim.
#
# The 10.0 ns (100 MHz) below is the STARTING point; derive the real number by
# running the widest config through OpenSTA at a deliberately loose period
# (e.g. 20 ns), reading the worst slack, and tightening until slack is
# small-but-positive. Record the derived period in the notebook.
#
# Licensed under the Apache License, Version 2.0. See the repo LICENSE file.

create_clock -name clk -period 10.0 [get_ports clk]   ;# 100 MHz, starting point

set_input_delay  -clock clk 1.0 [all_inputs]
set_output_delay -clock clk 1.0 [all_outputs]
set_clock_uncertainty 0.2 [get_clocks clk]
