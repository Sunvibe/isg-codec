# THZ 5.5 Eco ESPHome Serial Proxy Notes

These notes document the current ESPHome and serialx approach for accessing a Tecalor THZ 5.5 Eco through an ESP32-S3 USB-OTG host port and the THZ diagnostic interface's CP2102 USB-to-UART bridge.

The ESPHome serial proxy transport is confirmed working with the hardware and configuration notes below.

## Intended Setup

The intended hardware path is:

```text
THZ diagnostic USB interface
  -> internal CP2102 USB-to-UART bridge
  -> ESP32-S3 USB-OTG host
  -> ESPHome usb_uart
  -> ESPHome serial_proxy
  -> serialx esphome:// client
```

## ESP32-S3 Board Hardware Requirement

The ESP32-S3 N16R8 dual USB-C board must have its `USB-OTG` solder bridge closed for this setup. Without this bridge, the left USB-C OTG connector does not supply VBUS/5 V to the THZ 5.5 Eco diagnostic USB interface.

Observed behavior without the `USB-OTG` bridge:

- A USB power meter connected to the ESP32-S3 OTG port does not turn on.
- Plugging and unplugging the THZ diagnostic interface produces no USB host events in ESPHome, even with `logger.level: VERBOSE`.
- Writes through `serial_proxy` fail with `usb_uart: Channel not initialised - write ignored`.

Observed behavior after closing the `USB-OTG` bridge:

- The USB power meter turns on when connected to the ESP32-S3 OTG port.
- ESPHome logs USB activity when the THZ diagnostic interface is connected.

A reference ESPHome log from a working serial proxy connection is stored in `docs/reference/thz55eco-esphome-working-serial-proxy-log.txt`.

The intended client URL shape is:

```text
esphome://fmnet-heatpump-serial-bridge:6053/?port_name=THZ&key=...
```

The `port_name` value comes from the ESPHome `serial_proxy` name.

## ESPHome Configuration

Current ESPHome configuration shape:

```yaml
logger:
  level: DEBUG
  hardware_uart: UART0

usb_host:
  enable_hubs: false
  max_transfer_requests: 32

usb_uart:
  - type: cp210x
    vid: 0x10C4
    pid: 0xEA60
    channels:
      - id: uch_1
        baud_rate: 115200
        data_bits: 8
        parity: NONE
        stop_bits: 1
        buffer_size: 1024
        dummy_receiver: false

serial_proxy:
  - id: thz_serial_proxy
    uart_id: uch_1
    name: THZ
    port_type: TTL
```

Important details:

- `type: cp210x` matches the observed USB device.
- `vid: 0x10C4` and `pid: 0xEA60` match the THZ diagnostic interface's CP2102 bridge.
- `baud_rate: 115200`, `data_bits: 8`, `parity: NONE`, and `stop_bits: 1` match the confirmed ser2net setup (`115200n81`).
- `name: THZ` is used as `port_name=THZ` in the serialx URL.
- `hardware_uart: UART0` keeps ESPHome logging off the ESP32-S3 USB_SERIAL_JTAG peripheral while USB-OTG host is in use.
- `dummy_receiver: false` keeps received bytes available for `serial_proxy`.
- `debug: true` and `logger.level: VERBOSE` are useful for short-term diagnostics, but should be disabled for normal operation.

## serialx Dependency

The ESPHome URL scheme requires the ESPHome extra for serialx:

```powershell
pip install "serialx[esphome]"
```

Without this extra, serialx can fail with:

```text
No handler registered for URI scheme 'esphome://'
```

## Current Capture Command

```powershell
py tools\thz55eco_serialx_capture.py --url "esphome://fmnet-heatpump-serial-bridge:6053/?port_name=THZ&key=..." --command "FB" --byte-timeout 1.2 --output tests\fixtures\thz55eco-global-esphome.bin
```

Minimal init-only transport check:

```powershell
py tools\thz55eco_serialx_capture.py --url "esphome://fmnet-heatpump-serial-bridge:6053/?port_name=THZ&key=..." --command "FB" --init-only --byte-timeout 1.2
```

For a working transport, the init-only check should produce:

```text
sent start communication: 1 bytes
00000000  02                                               .
received start response: 1 bytes
00000000  10                                               .
```

## Observed ESPHome State

ESPHome detects the CP210x USB-to-UART adapter:

```text
Vendor id 10C4
Product id EA60
Device connected: Manuf: Silicon Labs; Prod: CP2102 USB to UART Bridge Controller
```

ESPHome reports the USB UART channel as:

```text
Baud Rate: 115200 baud
Data Bits: 8
Parity: NONE
Stop bits: 1
Debug: NO
Dummy receiver: NO
```

The serial proxy is exposed as:

```text
Serial Proxy [0]:
  Name: THZ
  Port Type: TTL
```

serialx can connect to the ESPHome Native API and trigger the serial proxy configuration:

```text
api.connection: aioesphomeapi: connected
serial_proxy: Configuring serial proxy [0]: baud=115200, flow_ctrl=NO, parity=0, stop=1, data=8
```

## Resolved Blocker

Writes through the ESPHome serial proxy were initially ignored by ESPHome:

```text
usb_uart: Channel not initialised - write ignored
```

This happened when the Python tool sent the initial THZ byte:

```text
sent start communication: 1 bytes
00000000  02                                               .
```

Root causes and fixes:

- The ESP32-S3 board's `USB-OTG` solder bridge was initially open, so the THZ diagnostic interface was not powered from the OTG port.
- The THZ diagnostic interface must be unplugged and replugged after ESPHome changes or reboot if no fresh USB host events are logged.
- The working setup uses `type: cp210x`, `vid: 0x10C4`, `pid: 0xEA60`, `dummy_receiver: false`, and logger output on `UART0`.

With the USB-OTG bridge closed and the CP2102 re-enumerated, serialx receives data through ESPHome `serial_proxy`.

## Tested Changes

- Changed `stop_bits` from the initially observed `1.5` to `1`.
- Tested `debug: true` for diagnostics, then disabled it for normal operation.
- Tested `dummy_receiver: true`, then disabled it so `serial_proxy` receives incoming bytes.
- Set `dummy_receiver: false` for normal serial proxy operation.
- Moved ESPHome logging to `hardware_uart: UART0`.
- Closed the ESP32-S3 board's `USB-OTG` solder bridge to provide VBUS/5 V to the THZ diagnostic interface.
- Confirmed that serialx connects to ESPHome and configures the serial proxy.
- Confirmed that serialx receives data through the ESPHome serial proxy.

## Next Diagnostics

If the issue returns, first verify that ESPHome logs a fresh USB device connection after the THZ diagnostic interface is plugged into the OTG port.

Expected outcomes:

- If there are no USB host events, check the `USB-OTG` solder bridge, VBUS/5 V, the OTG adapter, and the cable.
- If USB enumeration succeeds but writes log `Channel not initialised - write ignored`, replug the THZ diagnostic interface after boot and confirm `type: cp210x`, `vid`, and `pid`.
- If USB writes succeed but serialx receives no payload, compare the exchange between `tools/thz55eco_ser2net_capture.py` and `tools/thz55eco_serialx_capture.py`.

## Remaining Notes

- The THZ diagnostic interface may need to be replugged after ESPHome changes or reboot if no fresh USB host event appears.
- Keep `logger.level` at `DEBUG` or lower during normal operation; `VERBOSE` is only for short-term USB diagnostics.
- Keep `usb_uart.debug` disabled during normal operation unless byte-level USB UART diagnostics are needed.
