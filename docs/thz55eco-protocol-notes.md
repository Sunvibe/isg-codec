# THZ 5.5 Eco Protocol Notes

These notes document current observations while capturing data from a Tecalor THZ 5.5 Eco through a ser2net TCP endpoint. They are working notes, not a final protocol specification.

## Transport

The current test setup uses ser2net to expose the diagnostic serial connection as a TCP socket.

Observed ser2net banner on connect:

```text
ser2net port tcp,3334 device serialdev, /dev/ttyUSB0, 115200n81,local [,115200N81,CLOCAL]
```

The banner should be read and ignored before starting the device request sequence.

## Request Sequence

The observed request sequence is:

```text
send 02
receive 10

send 01 00 <checksum> <command...> 10 03
receive optional request-phase bytes

send 10
receive payload
```

The final `10` byte is an acknowledge/continue byte. It is time-sensitive and should be sent promptly after the request phase.

## Request Frame

The request frame has this shape:

```text
01 00 <checksum> <command...> 10 03
```

The checksum is calculated as:

```text
checksum = 0x01 + sum(command bytes), modulo 256
```

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

## Timing Observations

The early protocol phases are sensitive to long idle reads. Reading too long after the init byte or request frame can delay the next protocol byte and prevent the device from returning payload data.

Observed working timing:

```text
initial banner flush: 1.5s
step delay after send: 0.25s
init idle timeout: 0.05s
request idle timeout: 0.05s
payload idle timeout: 0.75s
```

Observed problematic timing:

```text
init or request idle timeout: 0.75s to 2.0s
```

Legacy behavior from the AppDaemon bridge also works:

```text
send protocol byte/frame
sleep 0.25s
recv(200)
```

This suggests that the issue is not the receive buffer size itself, but the timing between protocol phases.

## Suggested Tuning Ranges

These are current working ranges for further experiments, not hard protocol limits.

```text
initial banner flush:
  observed working: 1.5s
  suggested range: 0.5s to 2.0s

step delay after send:
  observed working: 0.25s
  suggested range: 0.20s to 0.50s

init idle timeout:
  observed working: 0.05s
  observed problematic: 0.75s and above
  suggested range: 0.02s to 0.10s

request idle timeout:
  observed working: 0.05s
  observed problematic: 0.75s and above
  suggested range: 0.02s to 0.10s

payload idle timeout:
  observed working: 0.75s
  suggested range: 0.50s to 2.0s
```

## Current Capture Command

```powershell
py tools\thz55eco_capture.py --host 192.168.64.101 --port 3334 --command "FB" --initial-read-timeout 1.5 --delay 0.25 --init-timeout 0.05 --request-timeout 0.05 --payload-timeout 0.75 --output tests\fixtures\thz55eco-global.bin
```

## Confirmed Faster Repeat Capture

This command was confirmed to work and can capture repeated global data responses faster:

```powershell
py tools\thz55eco_capture.py --host 192.168.64.101 --port 3334 --command "FB" --initial-read-timeout 0.1 --delay 0.25 --init-timeout 0.05 --request-timeout 0.05 --payload-timeout 0.25 --repeat 5 --output tests\fixtures\thz55eco-global-repeat5.bin
```

Observed working faster timing:

```text
initial banner flush: 0.1s
step delay after send: 0.25s
init idle timeout: 0.05s
request idle timeout: 0.05s
payload idle timeout: 0.25s
repeat count: 5
```

## Open Questions

- Are the timing requirements caused by the heat pump, the diagnostic serial adapter, ser2net, or a combination of all three?
- Is the request-phase response always empty for known read commands?
- Does the payload include an internal checksum or length field that can be validated?
- Which byte escaping rules are required before parsing payload data?
- Are the same timings valid for all supported THZ 5.5 Eco commands?
