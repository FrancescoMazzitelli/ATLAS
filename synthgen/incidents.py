import json
import pandas as pd
import numpy as np


def incident_duration_seconds(injuries_total, injuries_fatal):
    """Simple severity-based rule for how long the incident lasts (seconds).

    fatal        -> 120 min
    3+ injuries  ->  90 min
    1-2 injuries ->  45 min
    none         ->  30 min
    """
    minutes = np.where(
        injuries_fatal > 0, 120,
        np.where(injuries_total >= 3, 90,
        np.where(injuries_total >= 1, 45, 30)))
    return (minutes * 60).astype(int)


def crashes_to_jsonl(df, path, drop_null_island=True):
    """Convert the crashes df to JSONL with one record per crash.

    Each record has:
      - date:              crash date as YYYY-MM-DD
      - seconds:           seconds elapsed from midnight to the crash time
      - duration_seconds:  modeled incident length (severity-based rule)
      - end_seconds:       seconds + duration (may exceed 86400 if it spills past midnight)
      - x, y:              longitude, latitude
      - weather:           WEATHER_CONDITION
      - injuries_total:    INJURIES_TOTAL
    """
    ts = pd.to_datetime(df["CRASH_DATE"], format="%m/%d/%Y %I:%M:%S %p")
    seconds = ts.dt.hour * 3600 + ts.dt.minute * 60 + ts.dt.second

    injuries_total = pd.to_numeric(df["INJURIES_TOTAL"], errors="coerce").fillna(0).astype(int)
    injuries_fatal = pd.to_numeric(df["INJURIES_FATAL"], errors="coerce").fillna(0).astype(int)
    duration = incident_duration_seconds(injuries_total.values, injuries_fatal.values)

    causes = []
    for _, row in df.iterrows():
        primary = row["PRIM_CONTRIBUTORY_CAUSE"]
        secondary = row["SEC_CONTRIBUTORY_CAUSE"]

        if secondary == "NOT APPLICABLE" or secondary == primary:
            cause = primary
        else:
            cause = f"{primary}, {secondary}"

        cause = cause.lower().capitalize()
        causes.append(cause)

    out = pd.DataFrame({
        "date": ts.dt.strftime("%Y-%m-%d"),
        "seconds": seconds.astype(int),
        "duration_seconds": duration,
        "end_seconds": seconds.astype(int) + duration,
        "weather": df["WEATHER_CONDITION"],
        "cause": causes,
        "injuries_total": injuries_total,
        "x": pd.to_numeric(df["LONGITUDE"], errors="coerce"),
        "y": pd.to_numeric(df["LATITUDE"], errors="coerce"),
    })

    out = out.dropna(subset=["x", "y"])
    if drop_null_island:
        out = out[(out["x"] != 0) & (out["y"] != 0)]

    with open(path, "w") as f:
        for rec in out.to_dict(orient="records"):
            f.write(json.dumps(rec) + "\n")

    return out
