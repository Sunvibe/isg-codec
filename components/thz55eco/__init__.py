import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import uart
from esphome.const import CONF_ID

DEPENDENCIES = ["uart", "usb_uart"]
AUTO_LOAD = ["sensor", "binary_sensor"]

CONF_BYTE_TIMEOUT = "byte_timeout"
CONF_MAX_RETRY = "max_retry"
CONF_INITIAL_FLUSH_TIMEOUT = "initial_flush_timeout"
CONF_STARTUP_DELAY = "startup_delay"
CONF_REQUEST_DELAY = "request_delay"

thz55eco_ns = cg.esphome_ns.namespace("thz55eco")
Thz55EcoComponent = thz55eco_ns.class_(
    "Thz55EcoComponent",
    cg.PollingComponent,
    uart.UARTDevice,
)

CONFIG_SCHEMA = (
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(Thz55EcoComponent),
            cv.Optional(CONF_BYTE_TIMEOUT, default="1200ms"): cv.positive_time_period_milliseconds,
            cv.Optional(CONF_INITIAL_FLUSH_TIMEOUT, default="200ms"): cv.positive_time_period_milliseconds,
            cv.Optional(CONF_STARTUP_DELAY, default="10s"): cv.positive_time_period_milliseconds,
            cv.Optional(CONF_REQUEST_DELAY, default="1200ms"): cv.positive_time_period_milliseconds,
            cv.Optional(CONF_MAX_RETRY, default=5): cv.int_range(min=1, max=20),
        }
    )
    .extend(cv.polling_component_schema("60s"))
    .extend(uart.UART_DEVICE_SCHEMA)
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await uart.register_uart_device(var, config)

    cg.add(var.set_byte_timeout(config[CONF_BYTE_TIMEOUT].total_milliseconds))
    cg.add(var.set_initial_flush_timeout(config[CONF_INITIAL_FLUSH_TIMEOUT].total_milliseconds))
    cg.add(var.set_startup_delay(config[CONF_STARTUP_DELAY].total_milliseconds))
    cg.add(var.set_request_delay(config[CONF_REQUEST_DELAY].total_milliseconds))
    cg.add(var.set_max_retry(config[CONF_MAX_RETRY]))
