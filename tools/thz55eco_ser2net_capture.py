"""Capture THZ 5.5 Eco responses through ser2net using openHAB-style protocol flow."""

from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path


ESCAPE = 0x10
HEADER_START = 0x01
END = 0x03
GET = 0x00
SET = 0x80
START_COMMUNICATION = 0x02
FOOTER = bytes([ESCAPE, END])
DATA_AVAILABLE = bytes([ESCAPE, START_COMMUNICATION])


class ProtocolError(Exception):
    """Raised when the heat pump protocol exchange does not match expectations."""


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
    if not request_bytes:
        raise ValueError("request bytes must contain at least one byte")

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


def recv_exactly_one(sock: socket.socket, timeout: float) -> int:
    sock.settimeout(timeout)
    data = sock.recv(1)
    if not data:
        raise ProtocolError("connection closed while waiting for one byte")
    return data[0]


def read_stale_bytes(sock: socket.socket, timeout: float, buffer_size: int) -> bytes:
    sock.settimeout(timeout)
    chunks: list[bytes] = []
    while True:
        try:
            chunk = sock.recv(buffer_size)
        except TimeoutError:
            break
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def start_communication(sock: socket.socket, timeout: float) -> None:
    sock.sendall(bytes([START_COMMUNICATION]))
    print_phase("sent start communication", bytes([START_COMMUNICATION]))

    response = recv_exactly_one(sock, timeout)
    print_phase("received start response", bytes([response]))
    if response != ESCAPE:
        raise ProtocolError("heat pump did not return ESCAPE after start communication")


def establish_request(sock: socket.socket, request_message: bytes, timeout: float, max_retry: int) -> None:
    for request_try in range(1, max_retry + 1):
        sock.sendall(request_message)
        print_phase(f"sent request try {request_try}", request_message)

        response = bytearray()
        for _ in range(max_retry):
            try:
                response.append(recv_exactly_one(sock, timeout))
            except ProtocolError:
                continue

            if bytes(response[-2:]) == DATA_AVAILABLE:
                print_phase("received data available", bytes(response))
                return

        print_phase("received while waiting for data available", bytes(response))
        start_communication(sock, timeout)

    raise ProtocolError("heat pump did not report DATA_AVAILABLE for request")


def receive_data(sock: socket.socket, timeout: float, max_retry: int) -> bytes:
    response = bytearray()
    retries = 0
    sock.settimeout(timeout)

    while retries < max_retry:
        try:
            chunk = sock.recv(1)
        except TimeoutError:
            retries += 1
            continue

        if not chunk:
            break

        response.extend(chunk)
        if len(response) > 4 and bytes(response[-2:]) == FOOTER:
            print_phase("received raw response", bytes(response))
            fixed = fix_duplicated_bytes(bytes(response))
            print_phase("de-escaped response", fixed)
            return fixed

    raise ProtocolError("response footer was not received")


def get_data(sock: socket.socket, request_message: bytes, args: argparse.Namespace) -> bytes:
    establish_request(sock, request_message, args.byte_timeout, args.max_retry)
    sock.sendall(bytes([ESCAPE]))
    print_phase("sent acknowledge", bytes([ESCAPE]))
    return receive_data(sock, args.byte_timeout, args.max_retry)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run an openHAB-style Stiebel heat pump request through a ser2net endpoint.",
    )
    parser.add_argument("--host", required=True, help="ser2net host or IP address")
    parser.add_argument("--port", required=True, type=int, help="ser2net TCP port")
    parser.add_argument(
        "--request",
        "--command",
        dest="request_bytes",
        required=True,
        type=parse_hex_bytes,
        help='request bytes, for example "FB", "F4", "F3", or "0A 09 1C"',
    )
    parser.add_argument(
        "--byte-timeout",
        type=float,
        default=1.2,
        help="timeout in seconds for each single-byte protocol read",
    )
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=5.0,
        help="TCP connection timeout in seconds",
    )
    parser.add_argument(
        "--initial-read-timeout",
        type=float,
        default=0.2,
        help="initial timeout in seconds for reading ser2net banner or stale bytes",
    )
    parser.add_argument(
        "--no-initial-flush",
        action="store_true",
        help="skip reading banner or stale bytes before the first request",
    )
    parser.add_argument(
        "--max-retry",
        type=int,
        default=5,
        help="maximum retries for openHAB-style request and byte reads",
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
        default=1.2,
        help="delay in seconds between repeated request cycles",
    )
    parser.add_argument(
        "--buffer-size",
        type=int,
        default=1024,
        help="maximum bytes to read while flushing stale data",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="optional file path for writing the final de-escaped response bytes",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="skip response header and checksum validation",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    request_message = create_request_message(args.request_bytes)
    captured_responses = bytearray()

    print_phase("request bytes", args.request_bytes)
    print_phase("request message", request_message)

    try:
        with socket.create_connection((args.host, args.port), timeout=args.connect_timeout) as sock:
            print(f"connected to {args.host}:{args.port}")

            if args.no_initial_flush:
                print("initial data before first request: skipped")
            else:
                initial_data = read_stale_bytes(sock, args.initial_read_timeout, args.buffer_size)
                print_phase("initial data before first request", initial_data)

            for cycle in range(1, args.repeat + 1):
                if cycle > 1:
                    time.sleep(args.repeat_delay)

                print(f"cycle {cycle}/{args.repeat}")
                start_communication(sock, args.byte_timeout)
                response = get_data(sock, request_message, args)
                if not args.no_verify:
                    verify_header(response)
                    print("response verification: ok")
                captured_responses.extend(response)

    except (OSError, ProtocolError) as exc:
        print(f"communication failed: {exc}", file=sys.stderr)
        return 1

    print(f"captured {len(captured_responses)} de-escaped response bytes")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(captured_responses)
        print(f"wrote {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
