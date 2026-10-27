"""Minimal protobuf wire-format encoder/decoder for the Samsung Food gRPC-Web API.

Samsung Food doesn't publish .proto files. Field numbers come from the schemas
embedded in the web app's JavaScript bundle, so a tiny hand-rolled codec is
simpler than generating protobuf classes.
"""

from __future__ import annotations

import struct
from typing import Iterable

WIRE_VARINT = 0
WIRE_FIXED64 = 1
WIRE_LEN = 2
WIRE_FIXED32 = 5


# ---------------------------------------------------------------- encoding


def _varint(value: int) -> bytes:
    if value < 0:
        value += 1 << 64
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _key(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def enc_int(field: int, value: int | None) -> bytes:
    """Encode an int32/int64/enum/bool field (omitted when zero/None, like proto3)."""
    if not value:
        return b""
    return _key(field, WIRE_VARINT) + _varint(int(value))


def enc_bool(field: int, value: bool | None) -> bytes:
    return enc_int(field, 1 if value else 0)


def enc_float(field: int, value: float | None) -> bytes:
    if not value:
        return b""
    return _key(field, WIRE_FIXED32) + struct.pack("<f", value)


def enc_bytes(field: int, value: bytes) -> bytes:
    return _key(field, WIRE_LEN) + _varint(len(value)) + value


def enc_str(field: int, value: str | None) -> bytes:
    if not value:
        return b""
    return enc_bytes(field, value.encode("utf-8"))


def enc_msg(field: int, value: bytes) -> bytes:
    """Encode a nested message. Unlike scalars, empty messages are still sent."""
    return enc_bytes(field, value)


def enc_strs(field: int, values: Iterable[str]) -> bytes:
    return b"".join(enc_str(field, v) for v in values)


# ---------------------------------------------------------------- decoding


class Msg:
    """A decoded protobuf message: field number -> list of raw values.

    Varints are ints, fixed32/fixed64 are raw bytes, length-delimited fields
    are bytes (interpret them with .str()/.msg() since the wire format doesn't
    say whether they're strings or nested messages).
    """

    __slots__ = ("fields",)

    def __init__(self, data: bytes = b"") -> None:
        self.fields: dict[int, list] = {}
        i = 0
        n = len(data)
        while i < n:
            key, i = _read_varint(data, i)
            field, wire = key >> 3, key & 7
            if wire == WIRE_VARINT:
                val, i = _read_varint(data, i)
            elif wire == WIRE_LEN:
                length, i = _read_varint(data, i)
                val = data[i : i + length]
                if len(val) != length:
                    raise ValueError("truncated length-delimited field")
                i += length
            elif wire == WIRE_FIXED32:
                val = data[i : i + 4]
                i += 4
            elif wire == WIRE_FIXED64:
                val = data[i : i + 8]
                i += 8
            else:
                raise ValueError(f"unsupported wire type {wire}")
            self.fields.setdefault(field, []).append(val)

    def has(self, field: int) -> bool:
        return field in self.fields

    def int(self, field: int, default: int = 0) -> int:
        vals = self.fields.get(field)
        return vals[-1] if vals and isinstance(vals[-1], int) else default

    def bool(self, field: int) -> bool:
        return bool(self.int(field))

    def float(self, field: int) -> float | None:
        vals = self.fields.get(field)
        if not vals or not isinstance(vals[-1], bytes) or len(vals[-1]) != 4:
            return None
        return struct.unpack("<f", vals[-1])[0]

    def str(self, field: int, default: str = "") -> str:
        vals = self.fields.get(field)
        return vals[-1].decode("utf-8", "replace") if vals else default

    def msg(self, field: int) -> Msg:
        """Nested message (an empty Msg if absent, so chains don't need None checks)."""
        vals = self.fields.get(field)
        return Msg(vals[-1]) if vals else Msg()

    def msgs(self, field: int) -> list[Msg]:
        return [Msg(v) for v in self.fields.get(field, [])]

    def strs(self, field: int) -> list[str]:
        return [v.decode("utf-8", "replace") for v in self.fields.get(field, [])]


def _read_varint(data: bytes, i: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if i >= len(data):
            raise ValueError("truncated varint")
        b = data[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


# ---------------------------------------------------------------- gRPC-Web framing


def grpc_web_frame(message: bytes) -> bytes:
    return b"\x00" + len(message).to_bytes(4, "big") + message


def parse_grpc_web(body: bytes) -> tuple[bytes | None, dict[str, str]]:
    """Split a gRPC-Web response body into (message bytes, trailers)."""
    message = None
    trailers: dict[str, str] = {}
    i = 0
    while i + 5 <= len(body):
        flag = body[i]
        length = int.from_bytes(body[i + 1 : i + 5], "big")
        chunk = body[i + 5 : i + 5 + length]
        i += 5 + length
        if flag & 0x80:
            for line in chunk.decode("utf-8", "replace").split("\r\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    trailers[k.strip().lower()] = v.strip()
        elif message is None:
            message = chunk
    return message, trailers
