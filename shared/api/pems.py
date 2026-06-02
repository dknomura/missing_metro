"""
PeMS typical-weekday hourly preprocessing — designed to run after steps in https://github.com/BayAreaMetro/pems-typical-weekday/

Usage:
    python pems_yearly_variance.py input.csv output.csv
    python pems_yearly_variance.py input.csv output.csv --lane-type ML
"""

import argparse

import numpy as np
import pandas as pd

LOC_KEYS = ["station", "route", "direction", "hour"]


def add_within_year_se(df: pd.DataFrame) -> pd.DataFrame:
    """SE = sd / sqrt(days_observed) -- precision of this one year's estimate."""
    n = df["days_observed"].clip(lower=1)
    for sd_col, prefix in [("sd_speed", "speed"), ("sd_flow", "flow")]:
        if sd_col in df.columns:
            df[f"se_{prefix}"] = df[sd_col] / np.sqrt(n)
    return df


def add_segment_length(df: pd.DataFrame) -> pd.DataFrame:
    """
    Distance to next downstream station, computed within each route-direction-
    hour-year so the neighbor is consistent with the network that reported
    that year. Direction-aware: N/E increase postmile, S/W decrease.
    """
    if "abs_pm" not in df.columns:
        print("  abs_pm not found -- skipping segment_length")
        return df

    df["abs_pm"] = pd.to_numeric(df["abs_pm"], errors="coerce")
    df["travel_order_pm"] = np.where(df["direction"].isin(["S", "W"]), -df["abs_pm"], df["abs_pm"])

    grp = ["route", "direction", "hour", "year"]
    df = df.sort_values(grp + ["travel_order_pm"])
    df["next_pm"] = df.groupby(grp, dropna=False)["abs_pm"].shift(-1)
    df["segment_length"] = (df["next_pm"] - df["abs_pm"]).abs()
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_csv")
    ap.add_argument("output_csv")
    ap.add_argument("--lane-type", default="ML", help="filter on 'type' column (default ML; '' to keep all)")
    args = ap.parse_args()

    df = pd.read_csv(args.input_csv)
    df.columns = [c.strip() for c in df.columns]
    print(f"Loaded {len(df):,} rows")

    if args.lane_type and "type" in df.columns:
        df = df[df["type"] == args.lane_type].copy()
        print(f"  {len(df):,} rows after type == {args.lane_type!r}")

    df = add_within_year_se(df)
    df = add_segment_length(df)

    df.to_csv(args.output_csv, index=False)
    print(f"Wrote {len(df):,} rows -> {args.output_csv}")
    new_cols = [c for c in df.columns if c in ("se_speed", "se_flow", "segment_length", "next_pm", "travel_order_pm")]
    print("New columns added:", new_cols)


if __name__ == "__main__":
    main()
