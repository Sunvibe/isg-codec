# THZ 5.5 Eco Protocol Notes

These notes document current observations while capturing data from a Tecalor THZ 5.5 Eco through a ser2net TCP endpoint. They are working notes, not a final protocol specification.

## Transport

The current test setup uses ser2net to expose the diagnostic serial connection as a TCP socket.

Observed ser2net banner on connect:

```text
ser2net port tcp,3334 device serialdev, /dev/ttyUSB0, 115200n81,local [,115200N81,CLOCAL]
```

The banner should be read and ignored before starting the device request sequence.

An ESPHome serial proxy can also expose the diagnostic serial connection. The `tools/thz55eco_serialx_capture.py` tool uses `serialx` for this transport. With a serial proxy named `THZ`, the URL shape is:

```text
esphome://192.168.64.120:6053/?port_name=THZ
```

If ESPHome API encryption is enabled, include the API key in the URL according to serialx's ESPHome support:

```text
esphome://192.168.64.120:6053/?port_name=THZ&key=...
```

See [THZ 5.5 Eco ESPHome Serial Proxy Notes](thz55eco-esphome-serial-proxy-notes.md) for the current ESP32-S3, CP210x, ESPHome, and serialx transport status.

## Request Sequence

The observed request sequence is:

```text
send 02
receive 10

send 01 00 <checksum> <command...> 10 03
receive 10 02

send 10
receive payload until 10 03
```

This is an event-driven sequence. The next phase can start as soon as the expected protocol response has been received:

- `02` starts communication and should return `10`.
- `10 02` means data is available for the request.
- the final `10` acknowledges that the device should send the response.
- `10 03` ends the response frame.

The `tools/thz55eco_openhab_capture.py` tool follows this flow and no longer needs fixed sleeps between the phases on the happy path. Its `--byte-timeout` is only a read timeout for stalled or invalid communication, not a normal delay.

The naming in the capture tools follows the OpenHAB `DataParser.java` constants where possible:

- `ESCAPE`: `10`
- `HEADER_START`: `01`
- `END`: `03`
- `GET`: `00`
- `START_COMMUNICATION`: `02`
- `FOOTER`: `10 03`
- `DATA_AVAILABLE`: `10 02`

## Request Frame

The request frame has this shape:

```text
01 00 <checksum> <command...> 10 03
```

The checksum is calculated as:

```text
checksum = 0x01 + sum(command bytes), modulo 256
```

This is equivalent to OpenHAB's read-response checksum calculation for request frames: sum all bytes before the footer, skip the checksum byte at position 2, and keep the low byte.

Example for global data:

```text
command: FB
checksum: FC
request: 01 00 FC FB 10 03
```

Example for a consumption-related value:

```text
command: 0A 09 1C
checksum: 30
request: 01 00 30 0A 09 1C 10 03
```

## Known Commands

- `FB` reads global data.
- `F4` reads heating circuit 1 data.
- `F3` reads domestic hot water data.
- `0A 09 1C` reads a consumption-related value.

The OpenHAB Stiebel heat pump configuration is a useful external reference for additional request bytes, channel names, positions, lengths, scales, and units:

- [Tecalor_THZ55_7_62.xml](https://github.com/rhuitl/openhab-addons/blob/cd3c9cd223e9d4922cf7732f10210ef8e7d208c7/bundles/org.openhab.binding.stiebelheatpump/src/main/resources/HeatpumpConfig/Tecalor_THZ55_7_62.xml)

The OpenHAB parser implementation is a useful reference for packet constants, checksum validation, duplicated-byte escaping, and response value parsing:

- [DataParser.java](https://github.com/rhuitl/openhab-addons/blob/cd3c9cd223e9d4922cf7732f10210ef8e7d208c7/bundles/org.openhab.binding.stiebelheatpump/src/main/java/org/openhab/binding/stiebelheatpump/protocol/DataParser.java)

## OpenHAB-Style Capture Tool

The `tools/thz55eco_openhab_capture.py` tool keeps the ser2net transport but follows the OpenHAB communication flow more directly:

- start communication with `02` and expect `10`
- send a checksummed and duplicated-byte-escaped request message
- wait for `10 02` (`DATA_AVAILABLE`)
- acknowledge with `10`
- read until `10 03`, then de-escape the response and validate the header/checksum

Example:

```powershell
py tools\thz55eco_openhab_capture.py --host 192.168.64.101 --port 3334 --request "FB" --output tests\fixtures\thz55eco-global-openhab.bin
```

Confirmed global-data capture:

```text
02      -> 10
request -> 10 02
10      -> 83-byte response ending in 10 03
checksum validation: ok
```

Repeat captures on the same connection should keep the default `--repeat-delay 1.2`. This matches OpenHAB's `waitingTime` default for polling multiple requests. Local THZ 5.5 Eco tests showed:

```text
repeat-delay 1.2s: stable, matches OpenHAB default
repeat-delay 0.6s: stable in a short test
repeat-delay 0.3s: no longer stable
```

For testing the largest aggregate requests, use `tools/thz55eco_bulk_requests.py`. It captures the built-in aggregate request list, validates each response, and can write one response file per request:

```powershell
py tools\thz55eco_bulk_requests.py --host 192.168.64.101 --port 3334 --quiet --output-dir tests\fixtures\bulk
```

Use `--list` to show the built-in request list, or `--only FB,F4,F3` to limit a run.

Captured aggregate responses can be decoded with `tools/thz55eco_decode_bulk.py` and this project's observed point mapping:

```powershell
py tools\thz55eco_decode_bulk.py --input-dir tests\fixtures\bulk --csv tests\fixtures\bulk-decoded.csv
```

The decoder does not vendor the OpenHAB XML configuration. Its mapping lives in `docs/reference/thz55eco_observed_bulk_points.json` and is maintained by this project from local captures and protocol interpretation. OpenHAB remains a comparison reference for protocol behavior and value parsing.

The decoder follows the same value parsing rules that were validated against `DataParser.java`: signed big-endian 1/2/4-byte values, optional bit extraction, and record scaling.

The tool is intentionally small and is based on these OpenHAB classes:

- [CommunicationService.java](https://github.com/rhuitl/openhab-addons/blob/cd3c9cd223e9d4922cf7732f10210ef8e7d208c7/bundles/org.openhab.binding.stiebelheatpump/src/main/java/org/openhab/binding/stiebelheatpump/internal/CommunicationService.java)
- [DataParser.java](https://github.com/rhuitl/openhab-addons/blob/cd3c9cd223e9d4922cf7732f10210ef8e7d208c7/bundles/org.openhab.binding.stiebelheatpump/src/main/java/org/openhab/binding/stiebelheatpump/protocol/DataParser.java)
- [ProtocolConnector.java](https://github.com/rhuitl/openhab-addons/blob/cd3c9cd223e9d4922cf7732f10210ef8e7d208c7/bundles/org.openhab.binding.stiebelheatpump/src/main/java/org/openhab/binding/stiebelheatpump/protocol/ProtocolConnector.java)

## ESPHome Serial Proxy Capture

Equivalent ESPHome serial proxy capture still uses the serialx tool:

```powershell
py tools\thz55eco_serialx_capture.py --url "esphome://192.168.64.120:6053/?port_name=THZ" --command "FB" --initial-read-timeout 0.1 --delay 0.25 --init-timeout 0.05 --request-timeout 0.05 --payload-timeout 0.25 --repeat 5 --output tests\fixtures\thz55eco-global-esphome-repeat5.bin
```

The ESPHome path has separate transport-level status and blockers. See [THZ 5.5 Eco ESPHome Serial Proxy Notes](thz55eco-esphome-serial-proxy-notes.md).

## Open Questions

- Are OpenHAB's duplicated-byte escaping rules complete for all THZ 5.5 Eco payloads (`10 10` -> `10`, `2B 18` -> `2B`)?
