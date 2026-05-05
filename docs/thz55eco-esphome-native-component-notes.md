# THZ 5.5 Eco ESPHome Native Component Notes

This document describes the project-owned ESPHome external component that reads a Tecalor THZ 5.5 Eco directly on the ESP32-S3 and exposes selected values to Home Assistant through the ESPHome native API.

## Shape

The component lives in `components/thz55eco`.

It replaces the Python-side `serialx` client for normal operation:

```text
THZ diagnostic USB interface
  -> internal CP2102 USB-to-UART bridge
  -> ESP32-S3 USB-OTG host
  -> ESPHome usb_uart
  -> thz55eco external component
  -> ESPHome native API
  -> Home Assistant entities
```

The existing `serial_proxy` setup remains useful for diagnostics and capture work, but it should not be enabled at the same time as the native component on the same UART channel.

The current native component is intentionally built for the ESPHome `usb_uart` CP210x path used by the THZ diagnostic USB interface.

## Protocol

The C++ component ports the same openHAB-style request sequence used by `tools/thz55eco_serialx_bulk_capture.py`:

- send `02` and expect `10`
- send an escaped and checksummed aggregate request frame
- wait for `10 02`
- acknowledge with `10`
- read until `10 03`
- de-escape and validate the response checksum
- decode signed big-endian 1-, 2-, and 4-byte values
- decode mapped bit values as binary sensors
- read both one-byte aggregate requests such as `FB` and XML-derived multi-byte requests such as `0A092A`

The component waits for `startup_delay` before the first request. This gives `usb_host` and `usb_uart` time to enumerate the CP2102 bridge before the component starts writing protocol bytes.

Protocol handling is implemented as a non-blocking state machine. `update_interval` starts a complete read cycle, while `loop()` advances the individual protocol phases as bytes arrive from `usb_uart`.

When multiple mapped requests are active in YAML, `request_delay` spaces the individual request transactions. The default `1200ms` mirrors the stable delay used by the Python capture tools.

## Point Mapping

The point definitions are generated from `docs/reference/thz55eco_observed_bulk_points.json` into `components/thz55eco/thz55eco_points.h`.

Regenerate the header after changing the JSON mapping:

```powershell
py tools\generate_thz55eco_esphome_points.py
```

The ESP knows all generated points, but Home Assistant entities are only created for points listed in YAML under `sensor:` or `binary_sensor:`.

The mapping includes XML-derived electrical consumption, heat, and energy counters from OpenHAB's `Tecalor_THZ55_7_62.xml`, including domestic hot water electrical energy, heating circuit electrical energy, domestic hot water heat, heating circuit heat, recovered heat, and relative heating power.

The electrical and heat energy counters are published as composite values because the THZ exposes them as two adjacent 16-bit reads. The low part contains the current 0..999 portion, and the adjacent high part contains the thousands portion.

Composite day counters are converted to kWh:

```text
day_kwh = low_wh * 0.001 + high_kwh
```

Composite total counters are kept in kWh:

```text
total_kwh = low_kwh + high_kwh * 1000
```

The component currently uses these composite request pairs:

| Published key | Low request | High request | Formula |
| --- | --- | --- | --- |
| `electrical_domestic_hot_water_day` | `0A091A` | `0A091B` | `low * 0.001 + high` |
| `electrical_domestic_hot_water_total` | `0A091C` | `0A091D` | `low + high * 1000` |
| `electrical_heating_circuit_day` | `0A091E` | `0A091F` | `low * 0.001 + high` |
| `electrical_heating_circuit_total` | `0A0920` | `0A0921` | `low + high * 1000` |
| `heat_domestic_hot_water_day` | `0A092A` | `0A092B` | `low * 0.001 + high` |
| `heat_domestic_hot_water_total` | `0A092C` | `0A092D` | `low + high * 1000` |
| `heat_heating_circuit_day` | `0A092E` | `0A092F` | `low * 0.001 + high` |
| `heat_heating_circuit_total` | `0A0930` | `0A0931` | `low + high * 1000` |
| `heat_recovered_day` | `0A03AE` | `0A03AF` | `low * 0.001 + high` |
| `heat_recovered_total` | `0A03B0` | `0A03B1` | `low + high * 1000` |

The low and high part keys are internal implementation details. YAML should register only the published keys listed above.

## Example

Use `docs/thz55eco-esphome-native-component-example.yaml` as the starting point for an ESPHome device configuration. It registers all known mapped points from `docs/reference/thz55eco_observed_bulk_points.json`, so the component reads every mapped request group.

Keep these hardware details from the serial proxy setup:

- The ESP32-S3 board's `USB-OTG` solder bridge must be closed so the THZ diagnostic interface receives VBUS/5 V.
- `usb_uart` should use `type: cp210x`, `vid: 0x10C4`, and `pid: 0xEA60`.
- `logger.hardware_uart: UART0` keeps ESPHome logging away from the USB-OTG host path.
- `dummy_receiver: false` keeps received bytes available to the component.

## Verified Bring-Up

The native component has been verified with the `FB` global aggregate request through the ESP32-S3 USB-OTG host path.

Observed successful exchange:

```text
02 -> 10
01 00 FC FB 10 03 -> 10 02
10 -> 01 00 ... 10 03
```

The ESPHome component decoded and published the configured Home Assistant-facing entities, including outside temperature, flow temperature, return temperature, domestic hot water temperature, compressor state, pump states, and high-side pressure.

The composite electrical and heat counters were also verified on the ESP. A successful run produced these Home Assistant-facing values:

```text
Electrical Domestic Hot Water Day   -> 2.905 kWh
Electrical Domestic Hot Water Total -> 1574.000 kWh
Electrical Heating Circuit Day      -> 0.578 kWh
Electrical Heating Circuit Total    -> 4026.000 kWh
Heat Domestic Hot Water Day         -> 8.496 kWh
Heat Domestic Hot Water Total       -> 4073 kWh
Heat Heating Circuit Day            -> 0.262 kWh
Heat Heating Circuit Total          -> 17055 kWh
Heat Recovered Day                  -> 0.307 kWh
Heat Recovered Total                -> 0 kWh
Heating Relative Power              -> 18 %
```

The heat totals are plausible relative to the electrical totals: `17055 kWh / 4026 kWh` gives a heating circuit ratio of about `4.2`, and `4073 kWh / 1574 kWh` gives a domestic hot water ratio of about `2.6`. `Heat Recovered Total` remained `0 kWh` on the verified system, which may mean that this counter is unused or not meaningful for that installation.

After bring-up, disable `usb_uart.debug` unless byte-level protocol logging is needed for diagnostics.
