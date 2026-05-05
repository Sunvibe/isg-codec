# isg-codec

Python library for encoding and decoding the proprietary ISG protocol used by Stiebel Eltron and Tecalor heat pumps.

This repository is intended for VibeCoding with Codex GPT-5.5 Middle.

## Purpose

The project aims to understand as many device protocols as possible that can be connected to a Stiebel Eltron ISG web.

## Long-Term Goal

The long-term goal is to publish this project as a PyPI package that can be installed with pip.

The core library should stay focused and avoid dependencies that are only needed for protocol discovery, decoding, and device support work.

## Protocol Discovery Tools

Supporting new devices may require separate test and decoding tools. These tools can have their own dependencies, such as connectivity through ser2net or optional ESPHome-based access through `serialx`.

The current THZ 5.5 Eco tooling has two equivalent access paths:

- `tools/thz55eco_ser2net_capture.py` uses a ser2net TCP endpoint.
- `tools/thz55eco_serialx_capture.py` uses `serialx`, including ESPHome serial proxy URLs.

Both tools implement the same optimized openHAB-style protocol sequence:

- send `02` and expect `10`
- send an escaped and checksummed request frame
- wait for `10 02` (`DATA_AVAILABLE`)
- acknowledge with `10`
- read until `10 03`
- de-escape and validate the response checksum

This sequence avoids fixed phase sleeps on the happy path and uses `--byte-timeout` only for stalled or invalid communication.

Example for capturing global data through ser2net:

```powershell
py tools\thz55eco_ser2net_capture.py --host 192.168.64.101 --port 3334 --command "FB" --byte-timeout 1.2 --output tests\fixtures\thz55eco-global-ser2net.bin
```

Example for capturing global data through an ESPHome serial proxy with `serialx`:

```powershell
py tools\thz55eco_serialx_capture.py --url "esphome://fmnet-heatpump-serial-bridge:6053/?port_name=THZ&key=..." --command "FB" --byte-timeout 1.2 --output tests\fixtures\thz55eco-global-esphome.bin
```

The ESPHome serial proxy transport is confirmed working with an ESP32-S3 N16R8 dual USB-C board after closing the board's `USB-OTG` solder bridge so the THZ diagnostic interface receives VBUS/5 V. See [THZ 5.5 Eco ESPHome Serial Proxy Notes](docs/thz55eco-esphome-serial-proxy-notes.md).

## Bulk Reads And Decoding

The bulk capture tools run a built-in list of aggregate requests based on the project's observed THZ 5.5 Eco data point descriptions:

- `tools/thz55eco_ser2net_bulk_capture.py` captures through ser2net.
- `tools/thz55eco_serialx_bulk_capture.py` captures through `serialx` and ESPHome serial proxy URLs.

Both tools support `--list`, `--only`, `--output-dir`, `--raw-output-dir`, and `--quiet`.

Example bulk capture through ser2net:

```powershell
py tools\thz55eco_ser2net_bulk_capture.py --host 192.168.64.101 --port 3334 --quiet --output-dir tests\fixtures\bulk
```

Example bulk capture through ESPHome serial proxy:

```powershell
py tools\thz55eco_serialx_bulk_capture.py --url "esphome://fmnet-heatpump-serial-bridge:6053/?port_name=THZ&key=..." --quiet --output-dir tests\fixtures\bulk
```

Captured aggregate responses can be decoded with the small data point decoder:

```powershell
py tools\thz55eco_decode_bulk.py --input-dir tests\fixtures\bulk --csv tests\fixtures\bulk-decoded.csv
```

The decoder uses `docs/reference/thz55eco_observed_bulk_points.json`. It currently implements the value parsing needed for the observed captures: signed big-endian 1/2/4-byte values, optional bit extraction, and record scaling.

Important THZ 5.5 Eco aggregate requests include:

- `FB` reads global data.
- `F2` reads status values.
- `F4` reads heating circuit 1 data.
- `F3` reads domestic hot water data.
- `F5` reads heating circuit 2 data.
- `FC` reads time/date data.
- `16`, `E8`, `09`, and `D1` read additional aggregate groups.

See [THZ 5.5 Eco Protocol Notes](docs/thz55eco-protocol-notes.md) for the current request sequence, timing observations, and tuning ranges.

## ESPHome Native THZ 5.5 Eco Component

The repository also contains an ESPHome external component in `components/thz55eco` for reading the THZ 5.5 Eco directly on an ESP32-S3 and exposing selected values to Home Assistant through the ESPHome native API. The native component has been verified with the `FB` global aggregate request through the ESP32-S3 USB-OTG and CP2102 diagnostic interface path.

Start from [THZ 5.5 Eco ESPHome Native Component Notes](docs/thz55eco-esphome-native-component-notes.md) and [the example ESPHome YAML](docs/thz55eco-esphome-native-component-example.yaml).

The generated C++ point table is built from `docs/reference/thz55eco_observed_bulk_points.json`:

```powershell
py tools\generate_thz55eco_esphome_points.py
```

## Repository Structure

- `src/isg_codec/` contains the core library code that should remain suitable for PyPI distribution.
- `tools/` contains development tools for capturing, inspecting, and understanding recorded frames before they become supported decoder logic.
- `tests/` contains automated tests for the library.
- `tests/fixtures/` contains captured frame samples used to make decoder behavior reproducible.

## Inspiration

Inspired by [Sunvibe/tecalor-thz5-5-eco-homeassistant-bridge](https://github.com/Sunvibe/tecalor-thz5-5-eco-homeassistant-bridge).
