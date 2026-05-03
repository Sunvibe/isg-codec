"""Capture raw bytes from a ser2net TCP endpoint."""

from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Connect to a ser2net endpoint, optionally send a command, and capture the response bytes.",
    )
    parser.add_argument("--host", required=True, help="ser2net host or IP address")
    parser.add_argument("--port", required=True, type=int, help="ser2net TCP port")
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="capture duration in seconds after connecting or sending a command",
    )
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=5.0,
        help="TCP connection timeout in seconds",
    )
    parser.add_argument(
        "--send-hex",
        type=parse_hex_bytes,
        help='optional binary command to send first, for example "01 02 0A FF"',
    )
    parser.add_argument(
        "--send-text",
        help="optional text command to send first",
    )
    parser.add_argument(
        "--newline",
        choices=("none", "lf", "crlf"),
        default="none",
        help="line ending appended to --send-text",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="optional file path for writing captured raw bytes",
    )
    return parser


def text_payload(value: str, newline: str) -> bytes:
    suffix = {"none": "", "lf": "\n", "crlf": "\r\n"}[newline]
    return f"{value}{suffix}".encode("utf-8")


def main() -> int:
    args = build_parser().parse_args()

    if args.send_hex and args.send_text:
        print("error: use either --send-hex or --send-text, not both", file=sys.stderr)
        return 2

    payload = args.send_hex
    if args.send_text is not None:
        payload = text_payload(args.send_text, args.newline)

    captured = bytearray()
    start_time = time.monotonic()

    try:
        with socket.create_connection((args.host, args.port), timeout=args.connect_timeout) as sock:
            print(f"connected to {args.host}:{args.port}")
            sock.settimeout(0.25)

            if payload:
                sock.sendall(payload)
                print(f"sent {len(payload)} bytes")
                print(format_hexdump(payload))

            while time.monotonic() - start_time < args.timeout:
                try:
                    chunk = sock.recv(4096)
                except TimeoutError:
                    continue

                if not chunk:
                    print("connection closed by remote endpoint")
                    break

                offset = len(captured)
                captured.extend(chunk)
                print(format_hexdump(chunk, offset=offset))

    except OSError as exc:
        print(f"connection failed: {exc}", file=sys.stderr)
        return 1

    print(f"captured {len(captured)} bytes")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(captured)
        print(f"wrote {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
