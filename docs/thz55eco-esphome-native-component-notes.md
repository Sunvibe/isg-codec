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

The component waits for `startup_delay` before the first request. This gives `usb_host` and `usb_uart` time to enumerate the CP2102 bridge before the component starts writing protocol bytes.

Protocol handling is implemented as a non-blocking state machine. `update_interval` starts a complete read cycle, while `loop()` advances the individual protocol phases as bytes arrive from `usb_uart`.

## Point Mapping

The point definitions are generated from `docs/reference/thz55eco_observed_bulk_points.json` into `components/thz55eco/thz55eco_points.h`.

Regenerate the header after changing the JSON mapping:

```powershell
py tools\generate_thz55eco_esphome_points.py
```

The ESP knows all generated points, but Home Assistant entities are only created for points listed in YAML under `sensor:` or `binary_sensor:`.

## Example

Use `docs/thz55eco-esphome-native-component-example.yaml` as the starting point for an ESPHome device configuration.

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

After bring-up, disable `usb_uart.debug` unless byte-level protocol logging is needed for diagnostics.
