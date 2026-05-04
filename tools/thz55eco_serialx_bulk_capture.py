"""Capture large THZ 5.5 Eco aggregate requests through serialx."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from dataclasses import dataclass
from pathlib import Path


ESCAPE = 0x10
HEADER_START = 0x01
END = 0x03
GET = 0x00
SET = 0x80
START_COMMUNICATION = 0x02
FOOTER = bytes([ESCAPE, END])
DATA_AVAILABLE = bytes([ESCAPE, START_COMMUNICATION])


@dataclass(frozen=True)
class BulkRequest:
    key: str
    request_bytes: bytes
    record_count: int
    description: str


BULK_REQUESTS: tuple[BulkRequest, ...] = (
    BulkRequest("FB", bytes.fromhex("FB"), 42, "global values"),
    BulkRequest("F2", bytes.fromhex("F2"), 22, "status values"),
    BulkRequest("F4", bytes.fromhex("F4"), 19, "heating circuit 1"),
    BulkRequest("F3", bytes.fromhex("F3"), 10, "domestic hot water"),
    BulkRequest("F5", bytes.fromhex("F5"), 8, "heating circuit 2"),
    BulkRequest("FC", bytes.fromhex("FC"), 7, "time/date"),
    BulkRequest("16", bytes.fromhex("16"), 6, "solar"),
    BulkRequest("E8", bytes.fromhex("E8"), 6, "aggregate values"),
    BulkRequest("09", bytes.fromhex("09"), 5, "history"),
    BulkRequest("D1", bytes.fromhex("D1"), 5, "last errors"),
)


class ProtocolError(Exception):
    """Raised when the heat pump protocol exchange does not match expectations."""


def format_hexdump(data: bytes, offset: int = 0) -> str:
    lines: list[str] = []
    for index in range(0, len(data), 16):
        chunk = data[index : index + 16]
        hex_part = " ".join(f"{byte:02X}" for byte in chunk)
        ascii_part = "".join(chr(byte) if 32 <= byte <= 126 else "." for byte in chunk)
        lines.append(f"{offset + index:08X}  {hex_part:<47}  {ascii_part}")
    return "\n".join(lines)


def print_phase(name: str, data: bytes) -> None:
    print(f"{name}: {len(data)} bytes")
    if data:
        print(format_hexdump(data))


def calculate_checksum(data: bytes) -> int:
    if len(data) < 5:
        raise ProtocolError("checksum data must include header, checksum byte, command, and footer")

    checksum = 0
    for index, byte in enumerate(data[:-2]):
        if index == 2:
            continue
        checksum = (checksum + byte) & 0xFF
    return checksum


def add_duplicated_bytes(data: bytes) -> bytes:
    if len(data) < 4:
        return data

    result = bytearray(data[:2])
    for byte in data[2:-2]:
        result.append(byte)
        if byte == ESCAPE:
            result.append(byte)
        elif byte == 0x2B:
            result.append(0x18)
    result.extend(data[-2:])
    return bytes(result)


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


def create_request_message(request_bytes: bytes) -> bytes:
    message = bytearray([HEADER_START, GET, 0x00])
    message.extend(request_bytes)
    message.extend(FOOTER)
    message[2] = calculate_checksum(message)
    return add_duplicated_bytes(bytes(message))


def verify_header(response: bytes) -> None:
    if len(response) < 5:
        raise ProtocolError(f"invalid response length: {len(response)}")
    if response[0] != HEADER_START:
        raise ProtocolError("response does not start with HEADER_START")
    if response[1] not in (GET, SET):
        raise ProtocolError("response is neither GET nor SET")
    expected = calculate_checksum(response)
    if response[2] != expected:
        raise ProtocolError(f"invalid checksum: got {response[2]:02X}, expected {expected:02X}")


async def recv_exactly_one(reader: asyncio.StreamReader, timeout: float) -> int:
    data = await asyncio.wait_for(reader.read(1), timeout=timeout)
    if not data:
        raise ProtocolError("connection closed while waiting for one byte")
    return data[0]


async def read_stale_bytes(reader: asyncio.StreamReader, timeout: float, buffer_size: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        try:
            chunk = await asyncio.wait_for(reader.read(buffer_size), timeout=timeout)
        except TimeoutError:
            break

        if not chunk:
            break

        chunks.append(chunk)

    return b"".join(chunks)


async def start_communication(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    timeout: float,
) -> None:
    writer.write(bytes([START_COMMUNICATION]))
    await writer.drain()

    response = await recv_exactly_one(reader, timeout)
    if response != ESCAPE:
        raise ProtocolError("heat pump did not return ESCAPE after start communication")


async def establish_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    request_message: bytes,
    timeout: float,
    max_retry: int,
) -> bytes:
    for _ in range(max_retry):
        writer.write(request_message)
        await writer.drain()

        response = bytearray()
        for _ in range(max_retry):
            try:
                response.append(await recv_exactly_one(reader, timeout))
            except (ProtocolError, TimeoutError):
                continue

            if bytes(response[-2:]) == DATA_AVAILABLE:
                return bytes(response)

        await start_communication(reader, writer, timeout)

    raise ProtocolError("heat pump did not report DATA_AVAILABLE for request")


async def receive_data(
    reader: asyncio.StreamReader,
    timeout: float,
    max_retry: int,
) -> tuple[bytes, bytes]:
    response = bytearray()
    retries = 0

    while retries < max_retry:
        try:
            chunk = await asyncio.wait_for(reader.read(1), timeout=timeout)
        except TimeoutError:
            retries += 1
            continue

        if not chunk:
            break

        response.extend(chunk)
        if len(response) > 4 and bytes(response[-2:]) == FOOTER:
            raw = bytes(response)
            return raw, fix_duplicated_bytes(raw)

    raise ProtocolError("response footer was not received")


async def capture_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    request: BulkRequest,
    args: argparse.Namespace,
) -> tuple[bytes, bytes, bytes]:
    request_message = create_request_message(request.request_bytes)
    await start_communication(reader, writer, args.byte_timeout)
    data_available = await establish_request(reader, writer, request_message, args.byte_timeout, args.max_retry)

    writer.write(bytes([ESCAPE]))
    await writer.drain()

    raw_response, response = await receive_data(reader, args.byte_timeout, args.max_retry)
    verify_header(response)
    return data_available, raw_response, response


def selected_requests(only: str | None) -> list[BulkRequest]:
    if not only:
        return list(BULK_REQUESTS)

    requested_keys = {part.strip().upper() for part in only.split(",") if part.strip()}
    known = {request.key: request for request in BULK_REQUESTS}
    unknown = sorted(requested_keys - known.keys())
    if unknown:
        raise ValueError(f"unknown bulk request key(s): {', '.join(unknown)}")
    return [request for request in BULK_REQUESTS if request.key in requested_keys]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture large THZ 5.5 Eco aggregate requests through serialx.",
    )
    parser.add_argument(
        "--url",
        required=True,
        help='serialx URL, for example "esphome://192.168.64.120:6053/?port_name=THZ"',
    )
    parser.add_argument(
        "--baudrate",
        type=int,
        default=115200,
        help="serial baud rate",
    )
    parser.add_argument(
        "--only",
        help='comma-separated request keys to capture, for example "FB,F4,F3"',
    )
    parser.add_argument(
        "--byte-timeout",
        type=float,
        default=1.2,
        help="timeout in seconds for each single-byte protocol read",
    )
    parser.add_argument(
        "--initial-read-timeout",
        type=float,
        default=0.2,
        help="initial timeout in seconds for reading stale bytes before the first request",
    )
    parser.add_argument(
        "--no-initial-flush",
        action="store_true",
        help="skip reading stale bytes before the first request",
    )
    parser.add_argument(
        "--max-retry",
        type=int,
        default=5,
        help="maximum retries for openHAB-style request and byte reads",
    )
    parser.add_argument(
        "--repeat-delay",
        type=float,
        default=1.2,
        help="delay in seconds between requests on the same connection",
    )
    parser.add_argument(
        "--buffer-size",
        type=int,
        default=1024,
        help="maximum bytes to read while flushing stale data",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="optional directory for writing one de-escaped .bin response per request",
    )
    parser.add_argument(
        "--raw-output-dir",
        type=Path,
        help="optional directory for writing one raw .bin response per request",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print one summary line per request instead of response hexdumps",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="list built-in bulk requests and exit",
    )
    return parser


async def async_main() -> int:
    args = build_parser().parse_args()

    try:
        requests = selected_requests(args.only)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.list:
        for request in BULK_REQUESTS:
            print(f"{request.key:>2}  records={request.record_count:<2}  {request.description}")
        return 0

    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.raw_output_dir:
        args.raw_output_dir.mkdir(parents=True, exist_ok=True)

    try:
        import serialx
    except ImportError:
        print(
            "serialx is required for this tool. Install it with: pip install serialx",
            file=sys.stderr,
        )
        return 1

    try:
        reader, writer = await serialx.open_serial_connection(args.url, baudrate=args.baudrate)
    except Exception as exc:
        if args.url.startswith("esphome://") and "No handler registered" in str(exc):
            print(
                'connection failed: esphome:// URLs require the ESPHome extra. Install it with: pip install "serialx[esphome]"',
                file=sys.stderr,
            )
            return 1
        print(f"connection failed: {exc}", file=sys.stderr)
        return 1

    failures = 0

    try:
        with contextlib.closing(writer):
            print(f"connected to {args.url}")

            if args.no_initial_flush:
                print("initial data before first request: skipped")
            else:
                initial_data = await read_stale_bytes(reader, args.initial_read_timeout, args.buffer_size)
                print_phase("initial data before first request", initial_data)

            for index, request in enumerate(requests, start=1):
                if index > 1:
                    await asyncio.sleep(args.repeat_delay)

                print(
                    f"request {index}/{len(requests)}: {request.key} "
                    f"({request.description}, {request.record_count} records)"
                )

                try:
                    data_available, raw_response, response = await capture_request(reader, writer, request, args)
                except (OSError, ProtocolError, TimeoutError) as exc:
                    failures += 1
                    print(f"  failed: {exc}", file=sys.stderr)
                    continue

                escaped_delta = len(raw_response) - len(response)
                print(
                    f"  ok: data_available={data_available.hex(' ').upper()} "
                    f"raw={len(raw_response)} bytes de_escaped={len(response)} bytes "
                    f"escaped_delta={escaped_delta}"
                )

                if args.output_dir:
                    output_path = args.output_dir / f"thz55eco-{request.key.lower()}.bin"
                    output_path.write_bytes(response)
                    print(f"  wrote {output_path}")
                if args.raw_output_dir:
                    raw_output_path = args.raw_output_dir / f"thz55eco-{request.key.lower()}-raw.bin"
                    raw_output_path.write_bytes(raw_response)
                    print(f"  wrote {raw_output_path}")
                if not args.quiet:
                    print_phase("  response", response)

    except (OSError, TimeoutError) as exc:
        print(f"communication failed: {exc}", file=sys.stderr)
        return 1

    if failures:
        print(f"completed with {failures} failed request(s)", file=sys.stderr)
        return 1

    print(f"completed {len(requests)} request(s)")
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
