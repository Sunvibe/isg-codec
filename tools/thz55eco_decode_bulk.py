"""Decode THZ 5.5 Eco response captures using project-owned observed point mappings."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable


ESCAPE = 0x10
HEADER_START = 0x01
END = 0x03
GET = 0x00
SET = 0x80
FOOTER = bytes([ESCAPE, END])
DEFAULT_POINTS_JSON = Path("docs/reference/thz55eco_observed_bulk_points.json")


class DecodeError(Exception):
    """Raised when a response or record cannot be decoded."""


@dataclass(frozen=True)
class RecordDefinition:
    key: str
    request_byte: bytes
    position: int
    length: int
    scale: Decimal
    bit_position: int | None
    unit: str


@dataclass(frozen=True)
class DecodedValue:
    request: str
    key: str
    position: int
    length: int
    bit_position: int | None
    raw: str
    value: object
    unit: str


def parse_hex_bytes(value: str) -> bytes:
    normalized = value.replace("0x", "").replace(",", " ").replace(":", " ")
    parts = normalized.split()
    if len(parts) == 1:
        compact = parts[0]
        if len(compact) % 2 != 0:
            raise ValueError(f"invalid hex byte sequence: {value}")
        parts = [compact[index : index + 2] for index in range(0, len(compact), 2)]
    return bytes(int(part, 16) for part in parts)


def bytes_to_hex(data: bytes) -> str:
    return data.hex(" ").upper()


def load_record_definitions(points_json: Path) -> list[RecordDefinition]:
    data = json.loads(points_json.read_text(encoding="utf-8-sig"))
    records: list[RecordDefinition] = []
    for request in data["requests"]:
        request_byte = parse_hex_bytes(request["request"])
        for point in request["points"]:
            records.append(
                RecordDefinition(
                    key=point["key"],
                    request_byte=request_byte,
                    position=int(point["offset"]),
                    length=int(point["size"]),
                    scale=Decimal(str(point["scale"])),
                    bit_position=point.get("bit"),
                    unit=point.get("unit", ""),
                )
            )
    return records


def group_records_by_request(records: Iterable[RecordDefinition]) -> dict[str, list[RecordDefinition]]:
    grouped: dict[str, list[RecordDefinition]] = {}
    for record in records:
        grouped.setdefault(bytes_to_hex(record.request_byte).replace(" ", ""), []).append(record)
    return grouped


def calculate_checksum(data: bytes) -> int:
    if len(data) < 5:
        raise DecodeError("checksum data must include header, checksum byte, command, and footer")

    checksum = 0
    for index, byte in enumerate(data[:-2]):
        if index == 2:
            continue
        checksum = (checksum + byte) & 0xFF
    return checksum


def fix_duplicated_bytes(data: bytes) -> bytes:
    if len(data) < 4:
        return data

    body = data[:-2]
    fixed = bytearray()
    index = 0
    while index < len(body):
        byte = body[index]
        next_byte = body[index + 1] if index + 1 < len(body) else None
        if byte == ESCAPE and next_byte == ESCAPE:
            fixed.append(ESCAPE)
            index += 2
        elif byte == 0x2B and next_byte == 0x18:
            fixed.append(0x2B)
            index += 2
        else:
            fixed.append(byte)
            index += 1

    fixed.extend(data[-2:])
    return bytes(fixed)


def verify_header(response: bytes) -> None:
    if len(response) < 5:
        raise DecodeError(f"invalid response length: {len(response)}")
    if response[0] != HEADER_START:
        raise DecodeError("response does not start with HEADER_START")
    if response[1] not in (GET, SET):
        raise DecodeError("response is neither GET nor SET")
    if response[-2:] != FOOTER:
        raise DecodeError("response does not end with FOOTER")
    expected = calculate_checksum(response)
    if response[2] != expected:
        raise DecodeError(f"invalid checksum: got {response[2]:02X}, expected {expected:02X}")


def response_request_key(response: bytes) -> str:
    # Single-byte aggregate responses carry the request byte at position 3.
    return f"{response[3]:02X}"


def java_signed_int(raw: bytes) -> int:
    return int.from_bytes(raw, byteorder="big", signed=True)


def get_bit(data: bytes, position: int) -> bool:
    pos_byte = position // 8
    pos_bit = position % 8
    val_byte = data[pos_byte]
    return ((val_byte >> (8 - (pos_bit + 1))) & 0x0001) >= 1


def scale_value(number: int, scale: Decimal) -> Decimal | int:
    if scale == Decimal("1"):
        return number

    value = Decimal(number) * scale
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def decode_record(response: bytes, record: RecordDefinition) -> DecodedValue:
    end = record.position + record.length
    if end > len(response):
        raise DecodeError(
            f"{record.key} needs bytes {record.position}:{end}, response has {len(response)} bytes"
        )

    raw = response[record.position:end]
    if record.length not in (1, 2, 4):
        raise DecodeError(f"{record.key} has unsupported length {record.length}")

    number = java_signed_int(raw)

    if record.bit_position is not None:
        value: object = get_bit(raw, record.bit_position)
    else:
        scaled = scale_value(number, record.scale)
        value = int(scaled) if isinstance(scaled, int) else float(scaled)

    return DecodedValue(
        request=bytes_to_hex(record.request_byte).replace(" ", ""),
        key=record.key,
        position=record.position,
        length=record.length,
        bit_position=record.bit_position,
        raw=bytes_to_hex(raw),
        value=value,
        unit=record.unit,
    )


def input_files(args: argparse.Namespace) -> list[Path]:
    files: list[Path] = []
    for file_path in args.input_file or []:
        files.append(file_path)
    if args.input_dir:
        files.extend(sorted(args.input_dir.glob("*.bin")))
    return files


def decode_file(
    path: Path,
    records_by_request: dict[str, list[RecordDefinition]],
    fix_escaping: bool,
) -> list[DecodedValue]:
    response = path.read_bytes()
    if fix_escaping:
        response = fix_duplicated_bytes(response)
    verify_header(response)
    request_key = response_request_key(response)
    records = records_by_request.get(request_key)
    if not records:
        raise DecodeError(f"no record definitions for response request {request_key}")
    return [decode_record(response, record) for record in records]


def print_table(values: list[DecodedValue]) -> None:
    headers = ("request", "key", "raw", "value", "unit")
    rows = [
        (
            value.request,
            value.key,
            value.raw,
            str(value.value),
            value.unit,
        )
        for value in values
    ]
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    print("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)))


def write_csv(path: Path, values: list[DecodedValue]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(values[0]).keys()))
        writer.writeheader()
        for value in values:
            writer.writerow(asdict(value))


def write_json(path: Path, values: list[DecodedValue]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(value) for value in values], indent=2), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Decode THZ 5.5 Eco response .bin captures using project-owned observed point mappings.",
    )
    parser.add_argument(
        "--points-json",
        type=Path,
        default=DEFAULT_POINTS_JSON,
        help="project-owned observed point mapping JSON file",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        help="directory with de-escaped .bin response captures",
    )
    parser.add_argument(
        "--input-file",
        type=Path,
        action="append",
        help="single de-escaped .bin response capture; can be passed multiple times",
    )
    parser.add_argument(
        "--raw-input",
        action="store_true",
        help="treat input files as raw serial responses and de-escape before decoding",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        help="optional CSV output path",
    )
    parser.add_argument(
        "--json",
        type=Path,
        help="optional JSON output path",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="skip table output",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    files = input_files(args)
    if not files:
        print("no input files specified", file=sys.stderr)
        return 2

    records = load_record_definitions(args.points_json)
    records_by_request = group_records_by_request(records)
    decoded_values: list[DecodedValue] = []
    failures = 0

    for path in files:
        try:
            values = decode_file(path, records_by_request, args.raw_input)
        except (OSError, DecodeError, json.JSONDecodeError, KeyError, ValueError) as exc:
            failures += 1
            print(f"{path}: failed: {exc}", file=sys.stderr)
            continue

        print(f"{path}: decoded {len(values)} value(s)")
        decoded_values.extend(values)

    if not decoded_values:
        return 1

    if not args.quiet:
        print_table(decoded_values)

    if args.csv:
        write_csv(args.csv, decoded_values)
        print(f"wrote {args.csv}")
    if args.json:
        write_json(args.json, decoded_values)
        print(f"wrote {args.json}")

    if failures:
        print(f"completed with {failures} failed file(s)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
