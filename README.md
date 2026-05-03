# isg-codec

Python library for encoding and decoding the proprietary ISG protocol used by Stiebel Eltron and Tecalor heat pumps.

This repository is intended for VibeCoding with Codex GPT-5.5 Middle.

## Purpose

The project aims to understand as many device protocols as possible that can be connected to a Stiebel Eltron ISG web.

## Long-Term Goal

The long-term goal is to publish this project as a PyPI package that can be installed with pip.

The core library should stay focused and avoid dependencies that are only needed for protocol discovery, decoding, and device support work.

## Protocol Discovery Tools

Supporting new devices may require separate test and decoding tools. These tools can have their own dependencies, such as connectivity through the currently available ser2net setup and, in the future, optional ESPHome-based access.

The generic `tools/ser2net_capture.py` tool can be used to test TCP connectivity and capture raw bytes from any ser2net endpoint.

The device-specific `tools/thz55eco_capture.py` tool captures Tecalor THZ 5.5 Eco responses through ser2net. It intentionally keeps the THZ 5.5 Eco request sequence separate from the generic ser2net transport tool.

The device-specific `tools/thz55eco_serialx_capture.py` tool uses `serialx` for serial transports, including ESPHome serial proxy URLs. This keeps ESPHome-specific dependencies out of the core library.

The ESPHome serial proxy transport is still under investigation. See [THZ 5.5 Eco ESPHome Serial Proxy Notes](docs/thz55eco-esphome-serial-proxy-notes.md) for the current status.

Example for capturing global data:

```powershell
py tools\thz55eco_capture.py --host 192.168.64.101 --port 3334 --command "FB" --initial-read-timeout 1.5 --delay 0.25 --init-timeout 0.05 --request-timeout 0.05 --payload-timeout 0.75 --output tests\fixtures\thz55eco-global.bin
```

Example for capturing global data through an ESPHome serial proxy with `serialx`:

```powershell
py tools\thz55eco_serialx_capture.py --url "esphome://192.168.64.120:6053/?port_name=THZ" --command "FB" --delay 0.25 --init-timeout 0.05 --request-timeout 0.05 --payload-timeout 0.25 --repeat 5 --output tests\fixtures\thz55eco-global-esphome.bin
```

Known THZ 5.5 Eco commands:

- `FB` reads global data.
- `F4` reads heating circuit 1 data.
- `F3` reads domestic hot water data.
- `0A 09 1C` reads a consumption-related value.

The early protocol phases are time-sensitive. The tool therefore uses separate phase timeouts: short init and request timeouts before sending the next protocol byte, and a longer payload timeout after the acknowledge byte.

The `--legacy-exact` mode is still available for comparison with the AppDaemon bridge this project was initially inspired by: after each protocol step, the tool sleeps once and then performs exactly one socket read with the configured buffer size.

See [THZ 5.5 Eco Protocol Notes](docs/thz55eco-protocol-notes.md) for the current request sequence, timing observations, and tuning ranges.

## Repository Structure

- `src/isg_codec/` contains the core library code that should remain suitable for PyPI distribution.
- `tools/` contains development tools for capturing, inspecting, and understanding recorded frames before they become supported decoder logic.
- `tests/` contains automated tests for the library.
- `tests/fixtures/` contains captured frame samples used to make decoder behavior reproducible.

## Inspiration

Inspired by [Sunvibe/tecalor-thz5-5-eco-homeassistant-bridge](https://github.com/Sunvibe/tecalor-thz5-5-eco-homeassistant-bridge).
