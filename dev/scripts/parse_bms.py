#!/usr/bin/env python3
"""Parse the CTC BMS manual into a register map.

Two row shapes in the PDF:
  R  : NUM  desc  signed  R   <visible> <bit> <factor> <name>  x x x...
  RW : NUM  desc  signed  RW  <max> <min> <step> <visible> <bit> <factor> <name> ...
and 32-bit values occupy two registers, the second being a nameless
"(<< 16) MSB" continuation row.
"""
import json
import re
import sys

from pathlib import Path

from pypdf import PdfReader

# The manual is copyrighted and NOT committed to this repo; pass its path as the
# first argument (or keep a copy next to this default).
PDF = sys.argv[1] if len(sys.argv) > 1 else \
    "/path/to/BMS-Manual-16260016.pdf"
OUT = Path(__file__).resolve().parents[1] / "bms_registers.json"

reader = PdfReader(PDF)
text = "\n".join((reader.pages[i].extract_text() or "") for i in range(23, 45))
flat = re.sub(r"\s+", " ", text)

ROW = re.compile(
    # Register number. Bounded to 60000-62999: the tables on these pages only go
    # that high, and a looser pattern picks up stray numbers from prose (65535
    # out of "max value 65535" became a phantom register).
    r"\b(6[0-2]\d{3})\s+"
    # description: must not run past the next register number, or a row can
    # swallow its neighbour and steal its name.
    r"((?:(?!\b6[0-5]\d{3}\b).)+?)\s+"
    r"([01])\s+"                     # signed
    r"(RW|R)\s+"                     # access
    r"((?:6\d{4}\s+)+)"              # 1 (R) or 4 (RW) register references
    r"(\d+)\s+"                      # bit
    r"(\d+(?:,\d+)?)\s+"             # factor: 1 / 0,1 / 0,5 / 0,01
    r"([A-Za-z]\w*)"                 # camelCase name
)

# A continuation row: a register number followed by text containing "(<< 16) MSB"
# before any further register number turns up.
MSB = re.compile(
    r"\b(6[0-2]\d{3})\s+((?:(?!\b6[0-2]\d{3}\b).){0,90}?\(<<\s*16\)\s*MSB)", re.I)

regs = {}
for m in ROW.finditer(flat):
    num = int(m.group(1))
    desc = re.sub(r"\s+", " ", m.group(2)).strip()
    regs[num] = {
        "name": m.group(8),
        "description": desc[:160],
        "signed": m.group(3) == "1",
        "access": m.group(4),
        "factor": float(m.group(7).replace(",", ".")),
        "words": 1,
    }

# Mark 32-bit pairs: a nameless "(<< 16) MSB" row means the PREVIOUS register is
# the LSB half of a 32-bit value and consumes two registers.
msb_addrs = {int(m.group(1)) for m in MSB.finditer(flat)}
for a in sorted(msb_addrs):
    lsb = a - 1
    if lsb in regs:
        regs[lsb]["words"] = 2
    regs.pop(a, None)          # the MSB row is not a register in its own right

json.dump({str(k): v for k, v in sorted(regs.items())},
          open(OUT, "w"),
          indent=1, ensure_ascii=False)

acc = {}
for d in regs.values():
    acc[d["access"]] = acc.get(d["access"], 0) + 1
print(f"parsed {len(regs)} registers  access={acc}", file=sys.stderr)
print(f"32-bit pairs: {sorted(a for a, d in regs.items() if d['words'] == 2)}", file=sys.stderr)
print(f"factors: {sorted({d['factor'] for d in regs.values()})}", file=sys.stderr)
