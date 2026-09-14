// Generic Verilator harness for the PrecisionFit FIR DUT.
//
//   Usage: ./VFIR <input_vectors.txt> <output_vectors.txt>
//
// Reads one signed decimal sample per line from the input file, drives it into
// the DUT one per cycle with in_valid=1, and writes out_data (signed decimal)
// whenever out_valid=1, in order.
//
// This file is intentionally module-name agnostic: the build script compiles
// the DUT with `verilator --prefix VFIR`, so the generated class is always
// named VFIR no matter what the top module is called. That replaces the
// symlink/sed hack in the implementation guide with something that works for
// every generated config.
//
// Licensed under the Apache License, Version 2.0. See the repo LICENSE file.
#include <verilated.h>
#include "VFIR.h"

#include <fstream>
#include <iostream>
#include <vector>

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    if (argc < 3) {
        std::cerr << "usage: fir_tb <in_file> <out_file>\n";
        return 1;
    }

    VFIR* dut = new VFIR;

    std::ifstream fin(argv[1]);
    std::ofstream fout(argv[2]);
    if (!fin || !fout) {
        std::cerr << "could not open input or output vector file\n";
        return 1;
    }

    std::vector<long long> inputs;
    long long v;
    while (fin >> v) inputs.push_back(v);

    // reset
    dut->clk = 0;
    dut->rst_n = 0;
    dut->in_valid = 0;
    dut->in_data = 0;
    for (int i = 0; i < 8; i++) {
        dut->clk = !dut->clk;
        dut->eval();
    }
    dut->rst_n = 1;

    size_t idx = 0;
    // run enough cycles to flush the pipeline after the last input
    size_t total_cycles = inputs.size() + 32;

    for (size_t cyc = 0; cyc < total_cycles; cyc++) {
        // negedge: apply inputs
        dut->clk = 0;
        if (idx < inputs.size()) {
            dut->in_valid = 1;
            dut->in_data = inputs[idx];
            idx++;
        } else {
            dut->in_valid = 0;
        }
        dut->eval();

        // posedge: sample the registered outputs
        dut->clk = 1;
        dut->eval();
        if (dut->out_valid) {
            fout << (long long)dut->out_data << "\n";
        }
    }

    fout.close();
    delete dut;
    return 0;
}
