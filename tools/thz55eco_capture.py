"""Capture THZ 5.5 Eco responses through a ser2net TCP endpoint."""

from __future__ import annotations

import argparse
import socket
import sys
import time
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


def receive_available(sock: socket.socket, wait: float, read_timeout: float, buffer_size: int = 4096) -> bytes:
    time.sleep(wait)
    chunks: list[bytes] = []
    idle_since = time.monotonic()

    while True:
        try:
            chunk = sock.recv(buffer_size)
        except TimeoutError:
            if time.monotonic() - idle_since >= read_timeout:
                break
            continue

        if not chunk:
            break

        chunks.append(chunk)
        idle_since = time.monotonic()

    return b"".join(chunks)


def print_phase(name: str, data: bytes) -> None:
    print(f"{name}: {len(data)} bytes")
    if data:
        print(format_hexdump(data))


def recv_once(sock: socket.socket, wait: float, buffer_size: int) -> bytes:
    time.sleep(wait)
    try:
        return sock.recv(buffer_size)
    except TimeoutError:
        return b""


def effective_timeouts(args: argparse.Namespace) -> PhaseTimeouts:
    if args.read_timeout is not None:
        return PhaseTimeouts(args.read_timeout, args.read_timeout, args.read_timeout)
    return PhaseTimeouts(args.init_timeout, args.request_timeout, args.payload_timeout)


def read_phase(sock: socket.socket, args: argparse.Namespace, idle_timeout: float) -> bytes:
    if args.legacy_exact:
        return recv_once(sock, args.delay, args.buffer_size)
    return receive_available(sock, args.delay, idle_timeout)


def send_and_read(
    sock: socket.socket,
    args: argparse.Namespace,
    label: str,
    payload: bytes,
    idle_timeout: float,
) -> bytes:
    sock.sendall(payload)
    print_phase(f"sent {label}", payload)
    data = read_phase(sock, args, idle_timeout)
    print_phase(f"received after {label}", data)
    return data


def run_request_cycle(
    sock: socket.socket,
    args: argparse.Namespace,
    request: bytes,
    timeouts: PhaseTimeouts,
) -> bytes:
    send_and_read(sock, args, "init", b"\x02", timeouts.init)
    send_and_read(sock, args, "request", request, timeouts.request)

    sock.sendall(b"\x10")
    print_phase("sent acknowledge", b"\x10")
    payload = read_phase(sock, args, timeouts.payload)
    print_phase("received payload", payload)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a THZ 5.5 Eco request sequence through a ser2net endpoint.",
    )
    parser.add_argument("--host", required=True, help="ser2net host or IP address")
    parser.add_argument("--port", required=True, type=int, help="ser2net TCP port")
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
        default=0.75,
        help="maximum idle time in seconds while reading the payload after the acknowledge byte",
    )
    parser.add_argument(
        "--initial-read-timeout",
        type=float,
        default=0.75,
        help="initial idle time in seconds for reading the ser2net banner or stale bytes before the first request",
    )
    parser.add_argument(
        "--no-initial-flush",
        action="store_true",
        help="skip reading banner or stale bytes before the first request",
    )
    parser.add_argument(
        "--legacy-exact",
        action="store_true",
        help="after each protocol step, sleep once and read once with --buffer-size, matching the legacy AppDaemon script",
    )
    parser.add_argument(
        "--buffer-size",
        type=int,
        default=200,
        help="socket receive buffer size for legacy-exact mode",
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
        "--connect-timeout",
        type=float,
        default=5.0,
        help="TCP connection timeout in seconds",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="optional file path for writing the final response bytes",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    request = build_request(args.command)
    captured_payloads = bytearray()
    timeouts = effective_timeouts(args)
    socket_timeout = min(timeouts.init, timeouts.request, timeouts.payload, 0.25)

    try:
        with socket.create_connection((args.host, args.port), timeout=args.connect_timeout) as sock:
            print(f"connected to {args.host}:{args.port}")
            sock.settimeout(socket_timeout)

            print_phase("command", args.command)
            print_phase("request frame", request)
            if args.no_initial_flush:
                print("initial data before first request: skipped")
            else:
                initial_data = receive_available(sock, 0.0, args.initial_read_timeout)
                print_phase("initial data before first request", initial_data)

            for cycle in range(1, args.repeat + 1):
                if cycle > 1:
                    time.sleep(args.repeat_delay)

                print(f"cycle {cycle}/{args.repeat}")

                payload = run_request_cycle(sock, args, request, timeouts)
                captured_payloads.extend(payload)

    except OSError as exc:
        print(f"connection failed: {exc}", file=sys.stderr)
        return 1

    print(f"captured {len(captured_payloads)} payload bytes")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(captured_payloads)
        print(f"wrote {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
