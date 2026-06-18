import base64
from typing import Dict, Tuple


def _varint(n: int) -> bytes:
    buf = bytearray()
    while n > 0x7F:
        buf.append((n & 0x7F) | 0x80)
        n >>= 7
    buf.append(n & 0x7F)
    return bytes(buf)


def _tag(field: int, wire_type: int) -> bytes:
    return _varint((field << 3) | wire_type)


def _encode_edge(edge_id: int, speed_kph: int,
                 speed_unrestricted: int = 0,
                 congestion: int = 2,
                 edge_length_m: int = 0) -> bytes:
    buf = bytearray()
    if edge_id:
        buf.extend(_tag(1, 0))
        buf.extend(_varint(edge_id))
    if speed_kph:
        buf.extend(_tag(2, 0))
        buf.extend(_varint(speed_kph))
    if speed_unrestricted:
        buf.extend(_tag(3, 0))
        buf.extend(_varint(speed_unrestricted))
    if congestion:
        buf.extend(_tag(4, 0))
        buf.extend(_varint(congestion))
    if edge_length_m:
        buf.extend(_tag(6, 0))
        buf.extend(_varint(edge_length_m))
    return bytes(buf)


def build_traffic_stream(edge_speeds: Dict[int, Tuple[int, int, int]]) -> str:
    tile = bytearray()
    tile.extend(_tag(1, 0))
    tile.extend(_varint(1))

    for edge_id, (speed, congestion, length_m) in edge_speeds.items():
        edge_bytes = _encode_edge(
            edge_id, speed,
            speed_unrestricted=speed,
            congestion=congestion,
            edge_length_m=length_m,
        )
        tile.extend(_tag(2, 2))
        tile.extend(_varint(len(edge_bytes)))
        tile.extend(edge_bytes)

    return base64.b64encode(bytes(tile)).decode("ascii")
