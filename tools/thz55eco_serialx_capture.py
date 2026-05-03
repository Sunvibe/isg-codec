"""Capture THZ 5.5 Eco responses through serialx, including ESPHome serial proxy URLs."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from pathlib import Path
from typing import NamedTuple


class PhaseTimeouts(NamedTuple):
    init: float
    request: float
    payload: float


def parse_hex_bytes(value: str) -> bytes:
    normalized = value.replace("0x", "").replace(",", " ").replace(":", " ")
    parts = normalized.split()
    try:
        return bytes(int(part, 16) for part in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid hex byte sequence: {value}") from exc


def format_hexdump(data: bytes, offset: int = 0) -> str:
    lines: list[str] = []
    for index in range(0, len(data), 16):
        chunk = data[index : index + 16]
        hex_part = " ".join(f"{byte:02X}" for byte in chunk)
        ascii_part = "".join(chr(byte) if 32 <= byte <= 126 else "." for byte in chunk)
        lines.append(f"{offset + index:08X}  {hex_part:<47}  {ascii_part}")
    return "\n".join(lines)


def calc_checksum(command: bytes) -> bytes:
    checksum = 1
    for byte in command:
        checksum = (checksum + byte) & 0xFF
    return bytes([checksum])


def build_request(command: bytes) -> bytes:
    if not command:
        raise ValueError("command must contain at least one byte")
    return b"\x01\x00" + calc_checksum(command) + command + b"\x10\x03"


def print_phase(name: str, data: bytes) -> None:
    print(f"{name}: {len(data)} bytes")
    if data:
        print(format_hexdump(data))


def effective_timeouts(args: argparse.Namespace) -> PhaseTimeouts:
    if args.read_timeout is not None:
        return PhaseTimeouts(args.read_timeout, args.read_timeout, args.read_timeout)
    return PhaseTimeouts(args.init_timeout, args.request_timeout, args.payload_timeout)


async def read_until_idle(reader: asyncio.StreamReader, wait: float, idle_timeout: float, buffer_size: int) -> bytes:
    await asyncio.sleep(wait)
    chunks: list[bytes] = []

    while True:
        try:
            chunk = await asyncio.wait_for(reader.read(buffer_size), timeout=idle_timeout)
        except TimeoutError:
            break

        if not chunk:
            break

        chunks.append(chunk)

    return b"".join(chunks)


async def send_and_read(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    label: str,
    payload: bytes,
    delay: float,
    idle_timeout: float,
    buffer_size: int,
) -> bytes:
    writer.write(payload)
    await writer.drain()
    print_phase(f"sent {label}", payload)
    data = await read_until_idle(reader, delay, idle_timeout, buffer_size)
    print_phase(f"received after {label}", data)
    return data


async def run_request_cycle(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    request: bytes,
    args: argparse.Namespace,
    timeouts: PhaseTimeouts,
) -> bytes:
    await send_and_read(reader, writer, "init", b"\x02", args.delay, timeouts.init, args.buffer_size)
    await send_and_read(reader, writer, "request", request, args.delay, timeouts.request, args.buffer_size)

    writer.write(b"\x10")
    await writer.drain()
    print_phase("sent acknowledge", b"\x10")
    payload = await read_until_idle(reader, args.delay, timeouts.payload, args.buffer_size)
    print_phase("received payload", payload)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a THZ 5.5 Eco request sequence through serialx.",
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
        "--command",
        required=True,
        type=parse_hex_bytes,
        help='THZ command bytes, for example "FB", "F4", "F3", or "0A 09 1C"',
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.25,
        help="delay in seconds between protocol steps",
    )
    parser.add_argument(
        "--read-timeout",
        type=float,
        help="deprecated global idle timeout; overrides init, request, and payload timeouts when set",
    )
    parser.add_argument(
        "--init-timeout",
        type=float,
        default=0.05,
        help="short idle time in seconds while reading after the init byte before sending the request frame",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=0.05,
        help="short idle time in seconds while reading after the request frame before sending the acknowledge byte",
    )
    parser.add_argument(
        "--payload-timeout",
        type=float,
        default=0.25,
        help="maximum idle time in seconds while reading the payload after the acknowledge byte",
    )
    parser.add_argument(
        "--initial-read-timeout",
        type=float,
        default=0.1,
        help="initial idle time in seconds for reading stale bytes before the first request",
    )
    parser.add_argument(
        "--no-initial-flush",
        action="store_true",
        help="skip reading stale bytes before the first request",
    )
    parser.add_argument(
        "--init-only",
        action="store_true",
        help="only send the THZ init byte 02 and read the init response",
    )
    parser.add_argument(
        "--buffer-size",
        type=int,
        default=1024,
        help="maximum number of bytes to read per stream read",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="number of request cycles to run on the same connection",
    )
    parser.add_argument(
        "--repeat-delay",
        type=float,
        default=0.5,
        help="delay in seconds between repeated request cycles",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="optional file path for writing the final response bytes",
    )
    return parser


async def async_main() -> int:
    args = build_parser().parse_args()
    request = build_request(args.command)
    timeouts = effective_timeouts(args)
    captured_payloads = bytearray()

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

    print(f"connected to {args.url}")
    print_phase("command", args.command)
    print_phase("request frame", request)

    with contextlib.closing(writer):
        if args.no_initial_flush:
            print("initial data before first request: skipped")
        else:
            initial_data = await read_until_idle(reader, 0.0, args.initial_read_timeout, args.buffer_size)
            print_phase("initial data before first request", initial_data)

        if args.init_only:
            payload = await send_and_read(
                reader,
                writer,
                "init",
                b"\x02",
                args.delay,
                timeouts.init,
                args.buffer_size,
            )
            captured_payloads.extend(payload)
            print("init-only mode: skipped request and acknowledge phases")
        else:
            for cycle in range(1, args.repeat + 1):
                if cycle > 1:
                    await asyncio.sleep(args.repeat_delay)

                print(f"cycle {cycle}/{args.repeat}")
                payload = await run_request_cycle(reader, writer, request, args, timeouts)
                captured_payloads.extend(payload)

    print(f"captured {len(captured_payloads)} payload bytes")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(captured_payloads)
        print(f"wrote {args.output}")

    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
