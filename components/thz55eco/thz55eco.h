#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "esphome/components/binary_sensor/binary_sensor.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/components/uart/uart.h"
#include "esphome/core/component.h"

namespace esphome {
namespace thz55eco {

struct PointDefinition {
  const char *key;
  uint8_t request;
  uint8_t offset;
  uint8_t size;
  float scale;
  int8_t bit;
};

struct BulkRequestDefinition {
  uint8_t request;
  const char *label;
};

struct NumericRegistration {
  std::string key;
  sensor::Sensor *sensor;
};

struct BinaryRegistration {
  std::string key;
  binary_sensor::BinarySensor *sensor;
};

class Thz55EcoComponent : public PollingComponent, public uart::UARTDevice {
 public:
  void dump_config() override;
  void setup() override;
  void loop() override;
  void update() override;

  void set_byte_timeout(uint32_t timeout_ms) { this->byte_timeout_ms_ = timeout_ms; }
  void set_initial_flush_timeout(uint32_t timeout_ms) { this->initial_flush_timeout_ms_ = timeout_ms; }
  void set_startup_delay(uint32_t delay_ms) { this->startup_delay_ms_ = delay_ms; }
  void set_max_retry(uint8_t max_retry) { this->max_retry_ = max_retry; }

  void register_sensor(const std::string &key, sensor::Sensor *sensor);
  void register_binary_sensor(const std::string &key, binary_sensor::BinarySensor *sensor);

 protected:
  uint32_t byte_timeout_ms_{1200};
  uint32_t initial_flush_timeout_ms_{200};
  uint32_t startup_delay_ms_{10000};
  uint32_t setup_time_ms_{0};
  uint8_t max_retry_{5};
  bool startup_delay_logged_{false};
  bool cycle_started_{false};
  bool rx_pending_{false};
  bool using_rx_callback_{false};

  enum class ProtocolState : uint8_t {
    IDLE,
    WAIT_START_ACK,
    WAIT_DATA_AVAILABLE,
    WAIT_RESPONSE,
  };

  ProtocolState state_{ProtocolState::IDLE};
  uint8_t request_index_{0};
  uint8_t attempt_{0};
  uint8_t current_request_{0};
  const char *current_label_{nullptr};
  uint8_t previous_byte_{0};
  uint32_t state_started_ms_{0};
  std::vector<uint8_t> current_request_message_;
  std::vector<uint8_t> response_buffer_;

  std::vector<NumericRegistration> numeric_sensors_;
  std::vector<BinaryRegistration> binary_sensors_;

  void begin_cycle_();
  void start_next_request_();
  void send_start_communication_();
  void process_uart_();
  void process_byte_(uint8_t byte);
  void handle_timeout_();
  void fail_current_request_(const char *reason);
  void finish_current_request_(const std::vector<uint8_t> &response);
  void end_cycle_();
  const char *state_name_() const;

  std::vector<uint8_t> create_request_message_(uint8_t request) const;
  uint8_t calculate_checksum_(const std::vector<uint8_t> &data) const;
  std::vector<uint8_t> add_duplicated_bytes_(const std::vector<uint8_t> &data) const;
  std::vector<uint8_t> fix_duplicated_bytes_(const std::vector<uint8_t> &data) const;
  bool verify_header_(const std::vector<uint8_t> &response) const;
  bool request_has_registered_point_(uint8_t request) const;
  void decode_and_publish_(uint8_t request, const std::vector<uint8_t> &response);
  int32_t read_signed_big_endian_(const std::vector<uint8_t> &response, const PointDefinition &point) const;
  bool read_bit_(uint8_t value, uint8_t bit) const;
};

}  // namespace thz55eco
}  // namespace esphome
