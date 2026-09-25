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

create_clock -name clk -period 24.5 [get_ports clk]   ;# 40.8 MHz -- derived from trial run (PD_GUIDE.md section 9)
                                                       ;# Trial at 20 ns: SS-corner worst slack = -3.95 ns
                                                       ;# critical path ≈ 23.95 ns, target = 23.95 + 0.3 = 24.5 ns

set_input_delay  -clock clk 1.0 [all_inputs]
set_output_delay -clock clk 1.0 [all_outputs]
set_clock_uncertainty 0.2 [get_clocks clk]
