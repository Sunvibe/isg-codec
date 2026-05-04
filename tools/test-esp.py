import asyncio
import serialx

async def main():
    reader, writer = await serialx.open_serial_connection(
        "esphome://fmnet-heatpump-serial-bridge:6053/?port_name=THZ&key=6oK7OUuDZJfxYFL5uEdgcqNbTxLyxgYXkm54g05OSOc=",
        baudrate=115200,
    )
    await asyncio.sleep(2)
    writer.write(b"\x02")
    await writer.drain()
    await asyncio.sleep(1)

    data = await reader.read(1024)
    print(data.hex(" "))

    writer.close()

asyncio.run(main())
