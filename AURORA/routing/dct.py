import base64
import struct
from typing import List

import numpy as np

BUCKETS_PER_WEEK = 2016
COEFFICIENT_COUNT = 200

K_1_OVER_SQRT2 = 0.707106781
K_SPEED_NORMALIZATION = 0.031497039


def compress_speed_buckets(speeds: np.ndarray) -> np.ndarray:
    n = BUCKETS_PER_WEEK
    k = COEFFICIENT_COUNT
    indices = np.arange(k)[:, None]
    n_range = np.arange(n)[None, :]
    cos_term = np.cos(np.pi / n * (n_range + 0.5) * indices)
    raw = cos_term @ speeds
    raw[0] *= K_1_OVER_SQRT2
    coefficients = np.round(raw * K_SPEED_NORMALIZATION).astype(np.int16)
    return coefficients


def encode_compressed_speeds(coefficients: np.ndarray) -> str:
    raw = bytearray()
    for c in coefficients.astype(np.int16):
        raw.extend(struct.pack(">h", int(c)))
    return base64.b64encode(bytes(raw)).decode("ascii")


def decode_compressed_speeds(encoded: str) -> np.ndarray:
    raw = base64.b64decode(encoded)
    coefficients = []
    for i in range(0, len(raw), 2):
        coefficients.append(struct.unpack(">h", raw[i:i + 2])[0])
    return np.array(coefficients, dtype=np.int16)


def decompress_speed_bucket(coefficients: np.ndarray, bucket_index: int) -> float:
    n = BUCKETS_PER_WEEK
    k = len(coefficients)
    idx = np.arange(k)
    cos_val = np.cos(np.pi / n * (bucket_index + 0.5) * idx)
    speed = float(cos_val @ coefficients)
    speed = speed + coefficients[0] * (K_1_OVER_SQRT2 - 1.0)
    return speed * K_SPEED_NORMALIZATION


def decompress_all_speeds(coefficients: np.ndarray) -> np.ndarray:
    n = BUCKETS_PER_WEEK
    k = len(coefficients)
    n_range = np.arange(n)[:, None]
    idx = np.arange(k)[None, :]
    cos_term = np.cos(np.pi / n * (n_range + 0.5) * idx)
    result = cos_term @ coefficients
    result += coefficients[0] * (K_1_OVER_SQRT2 - 1.0)
    return result * K_SPEED_NORMALIZATION


def build_historical_speeds(speeds: List[float]) -> str:
    arr = np.array(speeds, dtype=np.float32)
    coeffs = compress_speed_buckets(arr)
    return encode_compressed_speeds(coeffs)
