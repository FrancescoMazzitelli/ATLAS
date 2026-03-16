import argparse
import json
import os
import struct
import sys
import tarfile


# ──────────────────────────────────────────────────────────────
# Costanti Valhalla
# ──────────────────────────────────────────────────────────────
LEVEL_BITS   = 3
TILE_ID_BITS = 22
ID_BITS      = 21
LEVEL_MASK   = (1 << LEVEL_BITS)   - 1
TILE_ID_MASK = (1 << TILE_ID_BITS) - 1
ID_MASK      = (1 << ID_BITS)      - 1

TRAFFIC_TILE_VERSION  = 3
UNKNOWN_SPEED_RAW     = 127        # breakpoint1=0 → speed ignorata
MAX_SPEED_RAW         = 126        # 126*2 = 252 kph

TRAFFIC_HEADER_SIZE   = 32         # sizeof(TrafficTileHeader)
TRAFFIC_SPEED_SIZE    = 8          # sizeof(TrafficSpeed)

INDEX_ENTRY_SIZE      = 16         # uint64 offset + uint32 tile_id + uint32 size
INDEX_FILENAME        = "index.bin"


def decode_graph_id(graph_id: int) -> tuple[int, int, int]:
    level    =  graph_id & LEVEL_MASK
    tile_id  = (graph_id >> LEVEL_BITS) & TILE_ID_MASK
    edge_idx = (graph_id >> (LEVEL_BITS + TILE_ID_BITS)) & ID_MASK
    return level, tile_id, edge_idx


def tile_id_32bit(level: int, tile_id: int) -> int:
    return (tile_id << LEVEL_BITS) | level


def make_speed(speed_kph: int) -> int:
    """
    Costruisce un TrafficSpeed valido.
    breakpoint1=255 → speed valida sull'intero edge.
    """
    if speed_kph <= 0:
        # edge chiuso: congestion1=MAX(63), overall/speed1=0
        overall, s1, cong1 = 0, 0, 63
    else:
        raw = min(max(speed_kph // 2, 1), MAX_SPEED_RAW)
        overall, s1, cong1 = raw, raw, 0

    return (
        (overall            & 0x7F)        |
        ((s1                & 0x7F) <<  7) |
        ((UNKNOWN_SPEED_RAW & 0x7F) << 14) |
        ((UNKNOWN_SPEED_RAW & 0x7F) << 21) |
        ((255               & 0xFF) << 28) |   # breakpoint1=255 → VALIDA
        ((cong1             & 0x3F) << 44)
    )


def make_invalid() -> int:
    """Speed con breakpoint1=0 → Valhalla ignora, usa OSM."""
    return (
        (UNKNOWN_SPEED_RAW & 0x7F)         |
        ((UNKNOWN_SPEED_RAW & 0x7F) <<  7) |
        ((UNKNOWN_SPEED_RAW & 0x7F) << 14) |
        ((UNKNOWN_SPEED_RAW & 0x7F) << 21)
        # breakpoint1 = 0 implicito
    )


def decode_speed(val: int) -> dict:
    """Decodifica un TrafficSpeed uint64 in campi leggibili."""
    overall = (val       ) & 0x7F
    s1      = (val >>  7 ) & 0x7F
    s2      = (val >> 14 ) & 0x7F
    s3      = (val >> 21 ) & 0x7F
    bp1     = (val >> 28 ) & 0xFF
    bp2     = (val >> 36 ) & 0xFF
    cong1   = (val >> 44 ) & 0x3F
    return {
        "overall_kph" : overall * 2,
        "speed1_kph"  : s1 * 2,
        "breakpoint1" : bp1,
        "congestion1" : cong1,
        "valid"       : bp1 != 0 and overall != UNKNOWN_SPEED_RAW,
    }


# ──────────────────────────────────────────────────────────────
# Lettura indice dal tar
# ──────────────────────────────────────────────────────────────
def read_index(tar_path: str) -> dict[int, tuple[int, int]]:
    """
    Legge index.bin dal tar.
    Ritorna { tile_id_32: (offset_in_file, size) }
    """
    with tarfile.open(tar_path, "r:") as tf:
        for m in tf.getmembers():
            if m.name == INDEX_FILENAME:
                blob = tf.extractfile(m).read()
                break
        else:
            raise RuntimeError(f"index.bin non trovato in {tar_path}")

    index = {}
    for i in range(0, len(blob), INDEX_ENTRY_SIZE):
        entry = blob[i:i + INDEX_ENTRY_SIZE]
        if len(entry) < INDEX_ENTRY_SIZE:
            break
        offset, tid32, size = struct.unpack("<QII", entry)
        index[tid32] = (offset, size)
    return index


# ──────────────────────────────────────────────────────────────
# Patch in-place sul file tar
# ──────────────────────────────────────────────────────────────
def patch_tar(tar_path: str, edge_ids: list[int],
              speed_kph: int | None, verbose: bool) -> None:
    """
    Modifica in-place le struct TrafficSpeed nel tar.
    speed_kph=None → reset a INVALID (usa velocità OSM).
    """
    index = read_index(tar_path)
    reset_mode = speed_kph is None

    # Raggruppa edge per tile
    by_tile: dict[tuple[int,int], list[int]] = {}
    for gid in edge_ids:
        l, t, i = decode_graph_id(gid)
        by_tile.setdefault((l, t), []).append(i)

    patched_tiles  = 0
    patched_edges  = 0
    skipped_tiles  = 0

    with open(tar_path, "r+b") as f:
        for (level, tile_id), edge_idxs in by_tile.items():
            tid32 = tile_id_32bit(level, tile_id)

            if tid32 not in index:
                print(f"  [SKIP] tile ({level},{tile_id}) "
                      f"non trovato nell'indice", file=sys.stderr)
                skipped_tiles += 1
                continue

            tile_offset, tile_size = index[tid32]

            # Leggi l'header del tile per verificare edge_count
            f.seek(tile_offset)
            header_raw = f.read(TRAFFIC_HEADER_SIZE)
            if len(header_raw) < TRAFFIC_HEADER_SIZE:
                print(f"  [ERR] tile ({level},{tile_id}): header troppo corto",
                      file=sys.stderr)
                continue

            # TrafficTileHeader: tile_id(u64), last_update(u64),
            #                    edge_count(u32), version(u32), s2(u32), s3(u32)
            _, last_update, edge_count, version, _, _ = \
                struct.unpack("<QQIIII", header_raw)

            if version != TRAFFIC_TILE_VERSION:
                print(f"  [WARN] tile ({level},{tile_id}): "
                      f"version={version} (atteso {TRAFFIC_TILE_VERSION})")

            if verbose:
                print(f"  tile ({level},{tile_id})  "
                      f"edge_count={edge_count}  offset={tile_offset}")

            for edge_idx in edge_idxs:
                if edge_idx >= edge_count:
                    print(f"  [SKIP] edge_idx={edge_idx} >= edge_count={edge_count} "
                          f"in tile ({level},{tile_id})", file=sys.stderr)
                    continue

                # Offset della struct TrafficSpeed dentro il file
                speed_offset = (tile_offset
                                + TRAFFIC_HEADER_SIZE
                                + edge_idx * TRAFFIC_SPEED_SIZE)

                new_val = make_invalid() if reset_mode else make_speed(speed_kph)

                f.seek(speed_offset)
                f.write(struct.pack("<Q", new_val))

                if verbose:
                    action = "RESET" if reset_mode else f"{speed_kph}kph"
                    print(f"    edge={edge_idx:6d}  offset={speed_offset}  → {action}")

                patched_edges += 1
            patched_tiles += 1

    action = "resettati" if reset_mode else f"impostati a {speed_kph} kph"
    print(f"\n[OK] {patched_edges} edge {action} "
          f"in {patched_tiles} tile"
          + (f" ({skipped_tiles} tile saltati)" if skipped_tiles else ""))
    if skipped_tiles:
        print("[WARN] Tile mancanti: il traffic.tar potrebbe coprire un'area diversa.")
    print("[>>] Il tar è già aggiornato. Basta fare docker restart <id>.")


# ──────────────────────────────────────────────────────────────
# Inspect: mostra velocità attuali degli edge
# ──────────────────────────────────────────────────────────────
def inspect_edges(tar_path: str, edge_ids: list[int]):

    index = read_index(tar_path)

    results = []

    with open(tar_path, "rb") as f:

        for gid in edge_ids:

            level, tile_id, edge_idx = decode_graph_id(gid)
            tid32 = tile_id_32bit(level, tile_id)

            if tid32 not in index:
                results.append({
                    "graph_id": gid,
                    "error": "tile non presente"
                })
                continue

            tile_offset, _ = index[tid32]

            speed_offset = tile_offset + TRAFFIC_HEADER_SIZE + edge_idx * TRAFFIC_SPEED_SIZE

            f.seek(speed_offset)
            raw = struct.unpack("<Q", f.read(8))[0]

            decoded = decode_speed(raw)

            results.append({
                "graph_id": gid,
                "level": level,
                "tile_id": tile_id,
                "edge_idx": edge_idx,
                **decoded
            })

    return results


# ──────────────────────────────────────────────────────────────
# Caricamento edge
# ──────────────────────────────────────────────────────────────
def load_edge_ids(args) -> list[int]:
    if hasattr(args, "edges_json") and args.edges_json:
        with open(args.edges_json) as f:
            data = json.load(f)
        ids = [int(e["id"]) for e in data.get("edges", []) if "id" in e]
        print(f"[INFO] {len(ids)} edge da {args.edges_json}")
        return ids
    return list(args.edge_ids)