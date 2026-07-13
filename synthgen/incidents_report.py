"""
incident_report.py

Read incident records from a JSONL file, analyze daily incident counts within
a date range, identify the day closest to the mean and the top-k busiest days,
and write a detailed report for each candidate day to an output folder.

Usage:
    python incident_report.py \
        --input incidents.jsonl \
        --outdir reports/ \
        --start 2020-07-01 \
        --end 2020-09-30 \
        --k 3
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


# ----------------------------------------------------------------------
# 1. Load data
# ----------------------------------------------------------------------
def load_jsonl(path, date_col='date'):
    """Load a JSONL file into a DataFrame, parsing the date column."""
    df = pd.read_json(path, lines=True)
    df[date_col] = pd.to_datetime(df[date_col])
    return df


# ----------------------------------------------------------------------
# 2. Daily counts
# ----------------------------------------------------------------------
def get_daily_counts(df, date_col='date', start=None, end=None):
    """Filter df to [start, end] and return (filtered_df, daily_counts)."""
    df = df.copy()

    if start is not None:
        df = df[df[date_col] >= pd.to_datetime(start)]
    if end is not None:
        df = df[df[date_col] <= pd.to_datetime(end)]

    counts = df.groupby(date_col).size().rename('incident_count')
    return df, counts


# ----------------------------------------------------------------------
# 3. Candidate day selection
# ----------------------------------------------------------------------
def select_candidate_days(counts, k=3, tolerance=None):
    """
    Pick the day(s) closest to the mean incident count, plus the top-k
    busiest days.

    tolerance: if set, returns ALL days within +/- tolerance of the mean
               instead of just the single closest day.
    """
    mean_count = counts.mean()

    if tolerance is None:
        mean_days = [(counts - mean_count).abs().idxmin()]
    else:
        mean_days = counts[(counts - mean_count).abs() <= tolerance].index.tolist()

    top_k_days = counts.sort_values(ascending=False).head(k).index.tolist()

    return {
        'mean_count': mean_count,
        'mean_days': mean_days,
        'top_k_days': top_k_days,
    }


# ----------------------------------------------------------------------
# 4. Per-day report
# ----------------------------------------------------------------------
def build_report(df, day, date_col='date', seconds_col='seconds',
                  duration_col='duration_seconds', weather_col='weather',
                  cause_col='cause', injuries_col='injuries_total'):
    day_df = df[df[date_col] == day].copy()
    day_df['hour'] = (day_df[seconds_col] // 3600).astype(int)

    return {
        'date': day,
        'total_incidents': len(day_df),
        'avg_duration_min': day_df[duration_col].mean() / 60 if len(day_df) else np.nan,
        'total_injuries': int(day_df[injuries_col].sum()),
        'time_distribution': day_df['hour'].value_counts().sort_index(),
        'weather_totals': day_df[weather_col].value_counts(),
        'cause_totals': day_df[cause_col].value_counts(),
    }


def report_to_text(report):
    d = report['date']
    label = d.date() if hasattr(d, 'date') else d

    lines = []
    lines.append(f"=== Report for {label} ===")
    lines.append(f"Total incidents   : {report['total_incidents']}")
    lines.append(f"Avg duration (min): {report['avg_duration_min']:.1f}")
    lines.append(f"Total injuries    : {report['total_injuries']}")

    lines.append("\nTime distribution (by hour of day):")
    lines.append(report['time_distribution'].to_string())

    lines.append("\nWeather totals:")
    lines.append(report['weather_totals'].to_string())

    lines.append("\nCause totals:")
    lines.append(report['cause_totals'].to_string())

    return "\n".join(lines)


def report_to_dict(report):
    """JSON-serializable version of the report."""
    d = report['date']
    label = str(d.date()) if hasattr(d, 'date') else str(d)

    return {
        'date': label,
        'total_incidents': report['total_incidents'],
        'avg_duration_min': None if pd.isna(report['avg_duration_min']) else round(report['avg_duration_min'], 2),
        'total_injuries': report['total_injuries'],
        'time_distribution': report['time_distribution'].to_dict(),
        'weather_totals': report['weather_totals'].to_dict(),
        'cause_totals': report['cause_totals'].to_dict(),
    }


# ----------------------------------------------------------------------
# 5. Writing reports to disk
# ----------------------------------------------------------------------
def write_report(report, outdir, fmt='txt'):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    d = report['date']
    label = d.date().isoformat() if hasattr(d, 'date') else str(d)
    filename = f"report_{label}.{fmt}"
    filepath = outdir / filename

    if fmt == 'txt':
        filepath.write_text(report_to_text(report), encoding='utf-8')
    elif fmt == 'json':
        filepath.write_text(json.dumps(report_to_dict(report), indent=2), encoding='utf-8')
    else:
        raise ValueError(f"Unsupported format: {fmt}")

    return filepath


# ----------------------------------------------------------------------
# 6. Orchestration
# ----------------------------------------------------------------------
def run_analysis(df, outdir, start=None, end=None, k=3, tolerance=None, fmt='txt'):
    """
    Full pipeline: filter by date range, find candidate days, write reports.
    Returns the candidates dict in case you want to inspect it programmatically.
    """
    subset_df, counts = get_daily_counts(df, start=start, end=end)

    if counts.empty:
        print("No incidents found in the given date range.")
        return None

    candidates = select_candidate_days(counts, k=k, tolerance=tolerance)

    print(f"Mean incident count: {candidates['mean_count']:.2f}")
    print(f"Closest-to-mean day(s): {[d.date() for d in candidates['mean_days']]}")
    print(f"Top-{k} busiest day(s): {[d.date() for d in candidates['top_k_days']]}")

    # Also write a summary file
    summary_path = Path(outdir) / "summary.txt"
    Path(outdir).mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        f"Mean incident count: {candidates['mean_count']:.2f}\n"
        f"Closest-to-mean day(s): {[str(d.date()) for d in candidates['mean_days']]}\n"
        f"Top-{k} busiest day(s): {[str(d.date()) for d in candidates['top_k_days']]}\n",
        encoding='utf-8'
    )

    all_days = candidates['mean_days'] + candidates['top_k_days']
    seen = set()
    written_files = []
    for day in all_days:
        if day in seen:
            continue
        seen.add(day)
        report = build_report(subset_df, day)
        filepath = write_report(report, outdir, fmt=fmt)
        written_files.append(filepath)
        print(f"Wrote report: {filepath}")

    return candidates


# ----------------------------------------------------------------------
# CLI entry point
# ----------------------------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser(description="Incident day report generator")
    parser.add_argument('--input', required=True, help='Path to input JSONL file')
    parser.add_argument('--outdir', required=True, help='Folder to write reports to')
    parser.add_argument('--start', default=None, help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', default=None, help='End date (YYYY-MM-DD)')
    parser.add_argument('--k', type=int, default=3, help='Number of top busiest days to report')
    parser.add_argument('--tolerance', type=float, default=None,
                         help='If set, include all days within +/- tolerance of the mean')
    parser.add_argument('--format', choices=['txt', 'json'], default='txt',
                         help='Report file format')
    parser.add_argument('--date-col', default='date', help='Name of the date column')
    return parser.parse_args()


def main():
    args = parse_args()

    df = load_jsonl(args.input, date_col=args.date_col)

    run_analysis(
        df,
        outdir=args.outdir,
        start=args.start,
        end=args.end,
        k=args.k,
        tolerance=args.tolerance,
        fmt=args.format,
    )


if __name__ == "__main__":
    main()
