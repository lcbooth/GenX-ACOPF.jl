"""
Convert the TAMU Texas7k case (2022-09-23 version) to PyPSA-USA's CSV input format.

    TEXAS7K_RAW_DIR=<folder> python convert_texas7k_2022_to_pypsausa.py

Reads Texas7k_20220923.RAW (PSS/E, rev 35) and Texas7k_20220923.AUX (PowerWorld) from
$TEXAS7K_RAW_DIR (default: this folder) and writes buses.csv, bus2sub.csv, sub.csv,
branches.csv and an empty links.csv to $TEXAS7K_OUTDIR (default: this folder). Download the
case from https://electricgrids.engr.tamu.edu.

Format notes for this version of the case:
  - RAW: the header has a variable number of control-parameter lines, so the start of the
    bus data is detected from the first bus record.
  - RAW: an empty SYSTEM SWITCHING DEVICE DATA section between branch and transformer data
    is skipped.
  - RAW: branch records include a quoted NAME field, so RATE1 is at column index 7.
  - AUX: the first "DATA (Bus, ..." table (the one with SubNum) is used; SubNum is at index
    10. In the Substation table, SubNum, latitude and longitude are at indices 0, 3 and 4.
  - The system base is 100 MVA; branch r/x/b are in per unit on that base.
"""
import re
import os
from pathlib import Path
import pandas as pd

HERE = Path(__file__).resolve().parent
RAW_DIR = Path(os.environ.get("TEXAS7K_RAW_DIR", HERE))
RAW = str(RAW_DIR / "Texas7k_20220923.RAW")
AUX = str(RAW_DIR / "Texas7k_20220923.AUX")
OUTDIR = os.environ.get("TEXAS7K_OUTDIR", str(HERE))
os.makedirs(OUTDIR, exist_ok=True)

for _p, _what in ((RAW, "Texas7k_20220923.RAW"), (AUX, "Texas7k_20220923.AUX")):
    if not os.path.exists(_p):
        raise FileNotFoundError(
            f"Missing {_what}: {_p}\n"
            f"Download the TAMU Texas7k 2022-09-23 case (electricgrids.engr.tamu.edu) and "
            f"set $TEXAS7K_RAW_DIR to the folder holding it."
        )

with open(RAW, "r", errors="replace") as f:
    raw_lines = f.readlines()

# confirm system SBASE = 100 (case header, 2nd line, 2nd field)
header_fields = [x.strip() for x in raw_lines[1].split(",")]
sbase = float(header_fields[1])
print(f"Case SBASE from header: {sbase}")
assert sbase == 100.0, "system base is not 100 MVA -- r/x/b conversion assumption invalid"

# dynamically find bus data start: first line matching "<digits>,'<name>...
bus_row_re = re.compile(r"^\d+,\s*'")
bus_start = next(i for i, l in enumerate(raw_lines) if bus_row_re.match(l))
print(f"Detected bus data start at line {bus_start + 1} (1-indexed)")

sect = {}
for i, l in enumerate(raw_lines):
    if "END OF BUS DATA" in l:
        sect["bus_end"] = i
    if "END OF BUS DATA, BEGIN LOAD DATA" in l:
        sect["load_start"] = i + 1
    if "END OF LOAD DATA" in l:
        sect["load_end"] = i
    if "END OF GENERATOR DATA, BEGIN BRANCH DATA" in l:
        sect["branch_start"] = i + 1
    if "END OF BRANCH DATA" in l:
        sect["branch_end"] = i
    if "BEGIN TRANSFORMER DATA" in l:
        sect["xfmr_start"] = i + 1
    if "END OF TRANSFORMER DATA" in l:
        sect["xfmr_end"] = i

# --- buses (voltage) ---
bus_rows = []
for l in raw_lines[bus_start:sect["bus_end"]]:
    p = [x.strip() for x in l.split(",")]
    bus_rows.append({"bus_id": int(p[0]), "baseKV": float(p[2])})
buses_kv = pd.DataFrame(bus_rows).set_index("bus_id")
print(f"Parsed {len(buses_kv)} bus voltage records from RAW")

# --- loads: active demand PL at index 5 ---
load_rows = []
for l in raw_lines[sect["load_start"]:sect["load_end"]]:
    if l.startswith("@!"):
        continue
    p = [x.strip() for x in l.split(",")]
    if len(p) < 6:
        continue
    load_rows.append({"bus_id": int(p[0]), "PL": float(p[5])})
loads_df = pd.DataFrame(load_rows).groupby("bus_id")["PL"].sum()
print(f"Parsed {len(load_rows)} load records from RAW ({loads_df.gt(0).sum()} buses with nonzero load)")

# --- branches: skip '@!' comment lines; rating rateA at index 7 ---
branch_rows = []
for l in raw_lines[sect["branch_start"]:sect["branch_end"]]:
    if l.startswith("@!") or l.startswith("0 /"):
        continue
    p = [x.strip() for x in l.split(",")]
    if len(p) < 9:
        continue
    try:
        fbus, tbus = int(p[0]), int(p[1])
    except ValueError:
        continue
    branch_rows.append({
        "from_bus_id": fbus,
        "to_bus_id": tbus,
        "ckt": p[2].strip("'").strip(),
        "r": float(p[3]),
        "x": float(p[4]),
        "b": float(p[5]),
        "rateA": float(p[7]),  # shifted from index 6 (2021) due to inserted NAME field
        "branch_device_type": "Line",
    })
branches_df = pd.DataFrame(branch_rows)
print(f"Parsed {len(branches_df)} line (branch) records from RAW")

# --- transformers: skip '@!' comment lines; records span 4 or 5 data lines ---
xfmr_rows = []
xfmr_sbases = set()
i = sect["xfmr_start"]
while i < sect["xfmr_end"]:
    l1 = raw_lines[i]
    if l1.startswith("@!"):
        i += 1
        continue
    p1 = [x.strip() for x in l1.split(",")]
    if len(p1) < 3:
        i += 1
        continue
    try:
        fbus, tbus, kbus = int(p1[0]), int(p1[1]), int(p1[2])
    except ValueError:
        i += 1
        continue
    is_3w = kbus != 0
    p2 = [x.strip() for x in raw_lines[i + 1].split(",")]
    r, x_, sb = float(p2[0]), float(p2[1]), float(p2[2])
    xfmr_sbases.add(sb)
    p3 = [x.strip() for x in raw_lines[i + 2].split(",")]
    rateA = float(p3[3])
    xfmr_rows.append({
        "from_bus_id": fbus,
        "to_bus_id": tbus,
        "ckt": p1[3].strip("'").strip(),
        "r": r,
        "x": x_,
        "b": 0.0,
        "rateA": rateA,
        "branch_device_type": "Transformer",
    })
    i += 5 if is_3w else 4
xfmr_df = pd.DataFrame(xfmr_rows)
print(f"Parsed {len(xfmr_df)} transformer records from RAW (winding SBASE values seen: {xfmr_sbases})")
assert xfmr_sbases == {100.0}, "transformer SBASE1-2 is not uniformly 100 MVA -- r/x need rescaling"

# ---------------------------------------------------------------------------
# AUX: Substation coordinates and first Bus table (bus_id -> SubNum at index 10)
# ---------------------------------------------------------------------------
with open(AUX, "r", errors="replace") as f:
    aux_lines = f.read().splitlines()

tok_re = re.compile(r'"[^"]*"|\S+')


def tokenize(line):
    return tok_re.findall(line)


def find_table_bounds(lines, header_marker, occurrence=1):
    seen = 0
    start = None
    for i, l in enumerate(lines):
        if header_marker in l:
            seen += 1
            if seen == occurrence:
                start = i
                break
    if start is None:
        raise ValueError(f"marker not found (occurrence {occurrence}): {header_marker}")
    j = start
    while "{" not in lines[j]:
        j += 1
    data_start = j + 1
    k = data_start
    while lines[k].strip() != "}":
        k += 1
    return data_start, k


sub_start, sub_end = find_table_bounds(aux_lines, "DATA (Substation,")
sub_rows = []
for l in aux_lines[sub_start:sub_end]:
    t = tokenize(l)
    if len(t) < 5:
        continue
    sub_rows.append({"sub_id": int(t[0]), "lat": float(t[3]), "lon": float(t[4])})
sub_df = pd.DataFrame(sub_rows).set_index("sub_id")
print(f"Parsed {len(sub_df)} substation records from AUX")

bus_start_aux, bus_end_aux = find_table_bounds(aux_lines, "DATA (Bus,", occurrence=1)
bus2sub_rows = []
for l in aux_lines[bus_start_aux:bus_end_aux]:
    t = tokenize(l)
    if len(t) < 11:
        continue
    bus2sub_rows.append({"bus_id": int(t[0]), "sub_id": int(t[10])})
bus2sub_df = pd.DataFrame(bus2sub_rows).set_index("bus_id")
print(f"Parsed {len(bus2sub_df)} bus->substation records from AUX")

# ---------------------------------------------------------------------------
# Assemble output tables
# ---------------------------------------------------------------------------
buses_out = buses_kv.join(bus2sub_df, how="left")
buses_out["Pd"] = loads_df.reindex(buses_out.index).fillna(0.0)
buses_out["interconnect"] = "Texas"
buses_out.index.name = "bus_id"
buses_out.to_csv(f"{OUTDIR}/buses.csv")

bus2sub_df[["sub_id"]].to_csv(f"{OUTDIR}/bus2sub.csv")
sub_df.to_csv(f"{OUTDIR}/sub.csv")

branches_all = pd.concat([branches_df, xfmr_df], ignore_index=True)
branches_all["interconnect"] = "Texas"
branches_all.index.name = "branch_id"
branches_all.to_csv(f"{OUTDIR}/branches.csv")

pd.DataFrame(columns=["from_bus_id", "to_bus_id", "Pt"]).to_csv(
    f"{OUTDIR}/links.csv", index_label="link_id",
)

print()
print("=== Output written to", OUTDIR, "===")
for f in ["buses.csv", "bus2sub.csv", "sub.csv", "branches.csv", "links.csv"]:
    df = pd.read_csv(f"{OUTDIR}/{f}")
    print(f"{f}: {len(df)} rows, columns={list(df.columns)}")

# ---------------------------------------------------------------------------
# Sanity checks (same battery as the 2021 conversion + full build_base_network.py sim)
# ---------------------------------------------------------------------------
missing_sub = buses_out["sub_id"].isna().sum()
print(f"\nBuses with no matched substation (no lat/lon): {missing_sub} / {len(buses_out)}")
unmatched_subid = set(buses_out["sub_id"].dropna().astype(int)) - set(sub_df.index)
print(f"sub_ids referenced by buses but missing from Substation table: {len(unmatched_subid)}")

all_bus_ids = set(buses_out.index)
orphan_branch_buses = (set(branches_all.from_bus_id) | set(branches_all.to_bus_id)) - all_bus_ids
print(f"Branch endpoints referencing a bus_id not in buses.csv: {len(orphan_branch_buses)}")

total_load = buses_out["Pd"].sum()
print(f"Total system load (sum of Pd, MW): {total_load:.1f}")

vnom_dist = branches_all["from_bus_id"].map(buses_out["baseKV"]).value_counts().sort_index()
print(f"\nBranch voltage-level distribution:\n{vnom_dist}")

load_by_kv = buses_out.groupby("baseKV")["Pd"].sum().sort_index()
print(f"\nLoad by voltage level (MW):\n{load_by_kv}")
