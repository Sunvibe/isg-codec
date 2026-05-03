# THZ 5.5 Eco ESPHome Serial Proxy Notes

These notes document the current ESPHome and serialx approach for accessing a Tecalor THZ 5.5 Eco through an ESP32-S3 with a CP210x USB-to-UART adapter attached to the ESP32-S3 USB-OTG host port.

This is not yet a confirmed working transport. The ser2net transport is currently confirmed working; the ESPHome serial proxy transport is still under investigation.

## Intended Setup

The intended hardware path is:

```text
THZ diagnostic serial port
  -> CP210x USB-to-UART adapter
  -> ESP32-S3 USB-OTG host
  -> ESPHome usb_uart
  -> ESPHome serial_proxy
  -> serialx esphome:// client
```

The intended client URL shape is:

```text
esphome://fmnet-heatpump-serial-bridge:6053/?port_name=THZ&key=...
```

The `port_name` value comes from the ESPHome `serial_proxy` name.

## ESPHome Configuration

Current ESPHome configuration shape:

```yaml
usb_uart:
  - type: cp210x
    channels:
      - id: uch_1
        baud_rate: 115200
        data_bits: 8
        parity: NONE
        stop_bits: 1
        buffer_size: 2048
        debug: true
        dummy_receiver: true

serial_proxy:
  - id: thz_serial_proxy
    uart_id: uch_1
    name: THZ
    port_type: TTL
```

Important details:

- `type: cp210x` matches the observed USB device.
- `baud_rate: 115200`, `data_bits: 8`, `parity: NONE`, and `stop_bits: 1` match the confirmed ser2net setup (`115200n81`).
- `name: THZ` is used as `port_name=THZ` in the serialx URL.
- `debug: true` was enabled for diagnostics.
- `dummy_receiver: true` was tested, but did not resolve the current blocker.

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
py tools\thz55eco_serialx_capture.py --url "esphome://fmnet-heatpump-serial-bridge:6053/?port_name=THZ&key=..." --command "FB" --initial-read-timeout 0.1 --delay 0.25 --init-timeout 0.05 --request-timeout 0.05 --payload-timeout 0.25 --output tests\fixtures\thz55eco-global-esphome-fast.bin
```

Minimal init-only transport check:

```powershell
py tools\thz55eco_serialx_capture.py --url "esphome://fmnet-heatpump-serial-bridge:6053/?port_name=THZ&key=..." --command "FB" --init-only --initial-read-timeout 0.1 --delay 0.25 --init-timeout 1.0
```

For a working transport, the init-only check should produce:

```text
sent init: 1 bytes
00000000  02                                               .
received after init: 1 bytes
00000000  10                                               .
```

## Observed ESPHome State

ESPHome detects the CP210x USB-to-UART adapter:

```text
Vendor id 10C4
Product id EA60
```

ESPHome reports the USB UART channel as:

```text
Baud Rate: 115200 baud
Data Bits: 8
Parity: NONE
Stop bits: 1
Debug: YES
Dummy receiver: YES
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

## Current Blocker

Writes through the ESPHome serial proxy are currently ignored by ESPHome:

```text
usb_uart: Channel not initialised - write ignored
```

This happens when the Python tool sends the initial THZ byte:

```text
sent init: 1 bytes
00000000  02                                               .
received after init: 0 bytes
```

Interpretation:

- The ESPHome API connection works.
- The serial proxy is found and configured.
- The CP210x adapter is detected.
- The UART settings appear correct.
- The write does not reach the CP210x channel because ESPHome considers the USB UART channel not initialized.

This means the current failure is below the THZ request protocol. The THZ command, checksum, and acknowledge sequence are not yet being tested over this transport because the init byte is ignored before it reaches the device.

## Tested Changes

- Changed `stop_bits` from the initially observed `1.5` to `1`.
- Enabled `debug: true`.
- Enabled `dummy_receiver: true`.
- Confirmed that serialx connects to ESPHome and configures the serial proxy.
- Confirmed that writes still produce `Channel not initialised - write ignored`.

## Next Diagnostics

Recommended next step: test whether ESPHome can write to the `usb_uart` channel internally, without `serial_proxy` or serialx. The exact diagnostic mechanism should be chosen in the ESPHome configuration or component code that fits the test setup.

Expected outcomes:

- If this also logs `Channel not initialised - write ignored`, the issue is in ESPHome `usb_uart`, USB host setup, CP210x lifecycle, or hardware.
- If this writes successfully, but `serial_proxy` still fails, the issue is likely the interaction between `serial_proxy` and `usb_uart`.

## Open Questions

- Does ESPHome `usb_uart` require a different lifecycle trigger before the channel becomes initialized?
- Does `serial_proxy` currently support `usb_uart` channels reliably, or only hardware UARTs?
- Is there a known ESPHome issue for `serial_proxy` combined with `usb_uart` and CP210x?
- Does the CP210x need to be replugged after boot, or does it initialize only after a USB host event?
- Would an ESPHome-native UART write/read test succeed without `serial_proxy`?
