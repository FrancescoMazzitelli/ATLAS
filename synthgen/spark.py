from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from time import perf_counter
from pyspark.sql import SparkSession, Window, functions as F

spark = (
    SparkSession.builder
    .appName("trip_bulk_report_segment_crossings")
    .config("spark.driver.memory", "64g")
    .config("spark.sql.files.maxPartitionBytes", "268435456")
    .config("spark.sql.files.openCostInBytes", "4194304")
    .config("spark.local.dir", "/home/isalvador/spark_tmp")
    .config("spark.memory.fraction", "0.6")
    .getOrCreate()
)

# INPUT_PATH = "/mnt/VMS_FY24_INRIX/trip_paths_chicago_custom/year=2022/month=09/day=01/date=2024-07-18/reportId=201375/v1/data/trajs/"
# TARGET_DATE = "2022-09-29"

# INPUT_PATH = "/mnt/VMS_FY24_INRIX/trip_paths_chicago_custom/year=2025/month=01/day=12/date=2025-01-21/reportId=227756/v1/data/trajs"
# INPUT_PATH = "/mnt/VMS_FY24_INRIX/trip_paths_chicago_custom/year=2025/month=01/day=12/date=2025-01-21/reportId=227756/v1/data/trajs"
INPUT_PATH = "/mnt/VMS_FY24_INRIX/trip_paths_chicago_custom/year=2025/month=05/day=11/date=2025-05-20/reportId=258943/v1/data/trajs"
TARGET_DATE = "2025-05-16"

OUTPUT_PATH = "/home/isalvador/git/ATLAS/synthgen/inrix_final/"
TIMEZONE = "America/Chicago"

BATCH_SIZE = 10000
MAX_FILES: int | None = None

local_tz = ZoneInfo(TIMEZONE)
start_local = datetime.strptime(TARGET_DATE, "%Y-%m-%d").replace(tzinfo=local_tz)
end_local = start_local.replace(hour=23, minute=59, second=59, microsecond=999000)
start_utc_ms = int(start_local.timestamp() * 1000)
end_utc_ms = int(end_local.timestamp() * 1000)


def process_batch(file_paths: list[str], batch_idx: int):
    print(f"--- Batch {batch_idx}: {len(file_paths)} files ---")

    df = spark.read.parquet(*file_paths)

    df = df.filter(
        (F.col("start_utc_ts") >= start_utc_ms) &
        (F.col("start_utc_ts") <= end_utc_ms)
    )

    if df.rdd.isEmpty():
        print(f"Batch {batch_idx}: no rows in target date range, skipping.")
        return

    trip_level_errors = df.filter(F.size(F.coalesce("error_codes", F.array())) > 0).select("trip_id")
    segment_level_errors = (
        df.select("trip_id", F.explode("trajectories").alias("traj"))
          .select("trip_id", F.explode("traj.solution_segments").alias("seg"))
          .filter(F.size(F.coalesce("seg.error_codes", F.array())) > 0)
          .select("trip_id")
    )
    bad_trip_ids = trip_level_errors.union(segment_level_errors).distinct()
    clean_df = df.join(bad_trip_ids, on="trip_id", how="left_anti")

    crossing_level = (
        clean_df
        .select(
            "trip_id",
            F.col("start_utc_ts").alias("trip_start_utc_ts"),
            F.col("end_utc_ts").alias("trip_end_utc_ts"),
            F.posexplode("trajectories").alias("traj_pos", "traj")
        )
        .select(
            "trip_id", "trip_start_utc_ts", "trip_end_utc_ts", "traj.traj_idx",
            F.explode("traj.solution_segments").alias("seg")
        )
        .select(
            "trip_id", "trip_start_utc_ts", "trip_end_utc_ts", "traj_idx",
            F.regexp_replace("seg.segment_id", "^-", "").alias("segment_id"),
            "seg.segment_idx",
            F.col("seg.start_utc_ts").alias("start_utc_ts"),
            F.col("seg.end_utc_ts").alias("end_utc_ts"),
        )
    )

    segment_order_window = Window.partitionBy("trip_id", "traj_idx").orderBy("segment_idx")
    crossing_level = crossing_level.withColumn("way_id", F.split(F.col("segment_id"), "_").getItem(0))
    crossing_level = crossing_level.withColumn("_prev_way_id", F.lag("way_id").over(segment_order_window))
    crossing_level = crossing_level.withColumn(
        "_new_visit",
        F.when(F.col("_prev_way_id").isNull() | (F.col("way_id") != F.col("_prev_way_id")), 1).otherwise(0)
    )
    crossing_level = crossing_level.withColumn(
        "visit_group", F.sum("_new_visit").over(segment_order_window)
    ).drop("_prev_way_id", "_new_visit")

    way_level = (
        crossing_level
        .groupBy("trip_id", "trip_start_utc_ts", "trip_end_utc_ts", "traj_idx", "visit_group", "way_id")
        .agg(
            F.min("start_utc_ts").alias("start_utc_ts"),
            F.max("end_utc_ts").alias("end_utc_ts"),
            F.min("segment_idx").alias("_min_segment_idx"),
        )
    )

    way_order_window = Window.partitionBy("trip_id", "traj_idx").orderBy("_min_segment_idx")
    way_level = (
        way_level
        .withColumn("way_idx", F.row_number().over(way_order_window) - 1)
        .drop("_min_segment_idx", "visit_group")
    )

    (
        way_level
        .coalesce(20)
        .write
        .mode("append")
        .parquet(OUTPUT_PATH)
    )

    print(f"Batch {batch_idx}: done.")


# --- Main driver loop ---
all_files = sorted(str(p) for p in Path(INPUT_PATH).rglob("*.parquet"))
print(f"Found {len(all_files)} total parquet files")

if MAX_FILES is not None:
    all_files = all_files[:MAX_FILES]
    print(f"MAX_FILES set — limiting to first {len(all_files)} files")

batches = [all_files[i:i + BATCH_SIZE] for i in range(0, len(all_files), BATCH_SIZE)]
print(f"Processing {len(batches)} batch(es) of up to {BATCH_SIZE} files each")

run_start = perf_counter()

for idx, batch in enumerate(batches):
    batch_start = perf_counter()
    try:
        process_batch(batch, idx)
    except Exception as e:
        print(f"!!! Batch {idx} failed: {e}")
        raise
    batch_elapsed_min = (perf_counter() - batch_start) / 60
    total_elapsed_min = (perf_counter() - run_start) / 60
    avg_min_per_batch = total_elapsed_min / (idx + 1)
    remaining_batches = len(batches) - (idx + 1)
    est_remaining_min = avg_min_per_batch * remaining_batches
    print(
        f"Batch {idx}: {batch_elapsed_min:.1f} min | "
        f"total elapsed: {total_elapsed_min:.1f} min | "
        f"est. remaining: {est_remaining_min:.1f} min "
        f"({remaining_batches} batch(es) left)"
    )

spark.stop()