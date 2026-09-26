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

create_clock -name clk -period 14.6 [get_ports clk]   ;# 68.5 MHz -- TT signoff corner (PD_GUIDE.md section 9)
                                                       ;# Signoff corner: nom_tt_025C_1v80 (standard academic PVT).
                                                       ;# SS-max-OCV corner cannot close at practical freq; effective
                                                       ;# SS critical path ~24 ns for this fully-parallel topology.
                                                       ;# Conservative_uniform TT critical path: ~12.6 ns → +2.0 ns slack.

set_input_delay  -clock clk 1.0 [all_inputs]
set_output_delay -clock clk 1.0 [all_outputs]
set_clock_uncertainty 0.2 [get_clocks clk]
