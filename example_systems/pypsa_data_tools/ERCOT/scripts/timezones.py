"""Timezone alignment between ERCOT demand and ERA5 weather.

GenX pairs demand and VRE by row index, so both must describe the same wall-clock hour.
ERA5 (via atlite) is naive UTC; ERCOT native load is local Texas clock time, hour-ending,
and observes DST. The offset is therefore -5 in summer and -6 in winter, so a fixed shift
cannot align them year-round -- these helpers reindex on tz-aware timestamps instead.

Misalignment is silent: both series are 8760 rows and line up without error. Use
check_alignment() to catch it.
"""
import numpy as np
import pandas as pd

ERCOT_TZ = "US/Central"


def ercot_load_index(hour_ending, year):
    """Parse ERCOT 'Hour Ending' labels into a tz-aware US/Central DatetimeIndex.

    Returns hour-BEGINNING instants ('01:00' -> 00:00), matching ERA5/atlite's convention
    of stamping each value at the start of its hour.

    Ambiguity must be resolved on hour-beginning values: under hour-ending labels the
    repeated fall-back hour is two rows both labelled '02:00', but the hour that repeats
    is 01:00->02:00, so localizing the labels as-is resolves the wrong instant and
    collapses both passes onto one.
    """
    raw = pd.Series(hour_ending).reset_index(drop=True)
    s = raw.astype(str).str.strip()

    # On the fall-back day ERCOT writes the repeated hour as plain '02:00' then
    # '02:00 DST', in chronological order -- so despite the name, the 'DST'-suffixed row
    # is the standard-time pass and the plain one is daylight.
    is_dst_flagged = s.str.endswith("DST")
    s = s.str.replace(r"\s*DST$", "", regex=True)

    # Hour-ending 24:00 is 00:00 of the following day.
    is_24 = s.str.contains(r"\s24:00$")
    s = s.str.replace(r"\s24:00$", " 00:00", regex=True)

    # The workbook is not uniformly typed -- a few rows come back from Excel as real
    # datetimes rather than "MM/DD/YYYY HH:MM" strings -- so parse the dominant format
    # strictly and fall back for the stragglers.
    naive = pd.to_datetime(s, format="%m/%d/%Y %H:%M", errors="coerce")
    if naive.isna().any():
        fallback = pd.to_datetime(raw[naive.isna()], errors="coerce")
        if fallback.isna().any():
            bad = raw[naive.isna()][fallback.isna()]
            raise ValueError(f"Unparseable 'Hour Ending' values: {list(bad)[:5]}")
        naive = naive.fillna(fallback)
    naive = naive + pd.to_timedelta(is_24.astype(int), unit="D")

    # Convert hour-ending -> hour-beginning before localizing, so the ambiguity lands on
    # the instant that actually repeats (see docstring).
    naive = naive - pd.Timedelta(hours=1)

    # Resolve the repeated hour from ERCOT's own labelling rather than guessing: the
    # 'DST'-suffixed row is the second (standard-time) pass, everything else is daylight.
    ambiguous = ~is_dst_flagged.to_numpy()
    idx = pd.DatetimeIndex(naive).tz_localize(
        ERCOT_TZ, ambiguous=ambiguous, nonexistent="shift_forward"
    )
    if idx.nunique() != len(idx):
        raise ValueError(
            f"ERCOT timestamps not unique after localization "
            f"({len(idx) - idx.nunique()} duplicate(s)) -- the fall-back hour did not "
            f"resolve to two distinct instants."
        )
    if not idx.tz_convert("UTC").is_monotonic_increasing:
        raise ValueError("ERCOT timestamps are not chronological after localization.")
    return idx


def era5_to_ercot_local(df, utc_index, target_local_index):
    """Reindex a naive-UTC hourly frame onto ERCOT local hours.

    df: DataFrame whose rows correspond 1:1 to utc_index (naive UTC, from atlite).
    target_local_index: tz-aware US/Central index (from ercot_load_index).

    Returns df reindexed so row i is the same wall-clock hour as target_local_index[i].
    DST-repeated hours resolve naturally (both map to distinct UTC instants); the skipped
    spring-forward hour simply never appears. Any hour with no ERA5 match is filled from
    the nearest available hour, which can only occur at the year boundary.
    """
    utc = pd.DatetimeIndex(utc_index)
    if utc.tz is None:
        utc = utc.tz_localize("UTC")
    out = df.copy()
    out.index = utc
    reindexed = out.reindex(target_local_index.tz_convert("UTC"), method="nearest",
                            tolerance=pd.Timedelta("1h"))
    n_missing = reindexed.isna().any(axis=1).sum()
    if n_missing:
        reindexed = reindexed.ffill().bfill()
    reindexed.index = pd.RangeIndex(1, len(reindexed) + 1, name="Time_Index")
    return reindexed, int(n_missing)


def check_alignment(solar_cf, demand_mw, label=""):
    """Report the diurnal peak hour of solar vs demand as an alignment smoke test.

    Both are row-indexed arrays starting at local hour 0. Real ERCOT solar peaks early
    afternoon (~12-14) and demand late afternoon (~16-17). A solar peak in the evening,
    or nonzero solar at local midnight, means the timezone alignment is wrong.
    """
    sol = np.asarray(solar_cf)[:8760]
    dem = np.asarray(demand_mw)[:8760]
    h = np.arange(len(sol)) % 24
    sol_by_h = np.array([sol[h == x].mean() for x in range(24)])
    dem_by_h = np.array([dem[h == x].mean() for x in range(24)])
    sol_peak, dem_peak = int(sol_by_h.argmax()), int(dem_by_h.argmax())
    midnight_cf = sol_by_h[0]
    ok = (10 <= sol_peak <= 15) and (14 <= dem_peak <= 20) and midnight_cf < 0.01
    print(f"[alignment{' ' + label if label else ''}] solar peaks h{sol_peak}, "
          f"demand peaks h{dem_peak}, solar CF at midnight {midnight_cf:.3f} -> "
          f"{'OK' if ok else 'SUSPECT'}")
    return ok
