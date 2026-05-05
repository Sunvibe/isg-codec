import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import binary_sensor

from . import Thz55EcoComponent

CONF_KEY = "key"
CONF_THZ55ECO_ID = "thz55eco_id"

CONFIG_SCHEMA = binary_sensor.binary_sensor_schema().extend(
    {
        cv.GenerateID(CONF_THZ55ECO_ID): cv.use_id(Thz55EcoComponent),
        cv.Required(CONF_KEY): cv.string_strict,
    }
)


async def to_code(config):
    var = await binary_sensor.new_binary_sensor(config)
    hub = await cg.get_variable(config[CONF_THZ55ECO_ID])
    cg.add(hub.register_binary_sensor(config[CONF_KEY], var))
