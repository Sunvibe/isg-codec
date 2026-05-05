#include "thz55eco.h"

#include <algorithm>
#include <cinttypes>

#include "esphome/core/hal.h"
#include "esphome/core/log.h"
#include "esphome/components/usb_uart/usb_uart.h"

#include "thz55eco_points.h"

namespace esphome {
namespace thz55eco {

static const char *const TAG = "thz55eco";

static constexpr uint8_t ESCAPE = 0x10;
static constexpr uint8_t HEADER_START = 0x01;
static constexpr uint8_t END = 0x03;
static constexpr uint8_t GET = 0x00;
static constexpr uint8_t SET = 0x80;
static constexpr uint8_t START_COMMUNICATION = 0x02;

void Thz55EcoComponent::setup() {
  ESP_LOGCONFIG(TAG, "Setting up THZ 5.5 Eco component");
  this->setup_time_ms_ = millis();

  if (this->parent_ != nullptr) {
    auto *channel = static_cast<usb_uart::USBUartChannel *>(this->parent_);
    channel->set_rx_callback([this]() {
      this->rx_pending_ = true;
      this->enable_loop_soon_any_context();
    });
    this->using_rx_callback_ = true;
  }
}

void Thz55EcoComponent::dump_config() {
  ESP_LOGCONFIG(TAG, "THZ 5.5 Eco:");
  ESP_LOGCONFIG(TAG, "  Byte timeout: %" PRIu32 " ms", this->byte_timeout_ms_);
  ESP_LOGCONFIG(TAG, "  Initial flush timeout: %" PRIu32 " ms", this->initial_flush_timeout_ms_);
  ESP_LOGCONFIG(TAG, "  Startup delay: %" PRIu32 " ms", this->startup_delay_ms_);
  ESP_LOGCONFIG(TAG, "  Max retry: %u", this->max_retry_);
  ESP_LOGCONFIG(TAG, "  USB UART RX callback: %s", YESNO(this->using_rx_callback_));
  ESP_LOGCONFIG(TAG, "  Numeric sensors: %u", static_cast<unsigned>(this->numeric_sensors_.size()));
  ESP_LOGCONFIG(TAG, "  Binary sensors: %u", static_cast<unsigned>(this->binary_sensors_.size()));
}

void Thz55EcoComponent::register_sensor(const std::string &key, sensor::Sensor *sensor) {
  this->numeric_sensors_.push_back({key, sensor});
}

void Thz55EcoComponent::register_binary_sensor(const std::string &key, binary_sensor::BinarySensor *sensor) {
  this->binary_sensors_.push_back({key, sensor});
}

void Thz55EcoComponent::update() {
  if (millis() - this->setup_time_ms_ < this->startup_delay_ms_) {
    if (!this->startup_delay_logged_) {
      ESP_LOGI(TAG, "Waiting %" PRIu32 " ms before first THZ request", this->startup_delay_ms_);
      this->startup_delay_logged_ = true;
    }
    return;
  }

  if (this->state_ != ProtocolState::IDLE) {
    ESP_LOGD(TAG, "Skipping poll because protocol state is %s", this->state_name_());
    return;
  }

  this->begin_cycle_();
}

void Thz55EcoComponent::loop() {
  this->process_uart_();
  this->handle_timeout_();

  if (this->state_ == ProtocolState::IDLE && !this->cycle_started_)
    this->disable_loop();
}

void Thz55EcoComponent::begin_cycle_() {
  this->cycle_started_ = true;
  this->request_index_ = 0;
  this->start_next_request_();
}

void Thz55EcoComponent::start_next_request_() {
  while (this->request_index_ < sizeof(THZ55ECO_BULK_REQUESTS) / sizeof(THZ55ECO_BULK_REQUESTS[0])) {
    const auto &request = THZ55ECO_BULK_REQUESTS[this->request_index_++];
    if (!this->request_has_registered_point_(request.request))
      continue;

    this->current_request_ = request.request;
    this->current_label_ = request.label;
    this->current_request_message_ = this->create_request_message_(request.request);
    this->attempt_ = 0;
    this->previous_byte_ = 0;
    this->response_buffer_.clear();
    this->send_start_communication_();
    return;
  }

  this->cycle_started_ = false;
  this->state_ = ProtocolState::IDLE;
}

void Thz55EcoComponent::send_start_communication_() {
  this->attempt_++;
  this->previous_byte_ = 0;
  this->response_buffer_.clear();
  this->write_byte(START_COMMUNICATION);
  this->state_ = ProtocolState::WAIT_START_ACK;
  this->state_started_ms_ = millis();
  this->enable_loop();
}

void Thz55EcoComponent::process_uart_() {
  if (this->using_rx_callback_ && !this->rx_pending_)
    return;

  this->rx_pending_ = false;

  uint8_t byte = 0;
  size_t total_read = 0;
  while (total_read < 256) {
    const size_t available = this->available();
    if (available == 0)
      break;

    const size_t read_limit = std::min<size_t>(available, 256 - total_read);
    for (size_t read_count = 0; read_count < read_limit; read_count++) {
      if (!this->read_byte(&byte))
        return;
      total_read++;
      this->process_byte_(byte);
    }
  }
}

void Thz55EcoComponent::process_byte_(uint8_t byte) {
  ESP_LOGVV(TAG, "RX %02X in %s", byte, this->state_name_());

  switch (this->state_) {
    case ProtocolState::IDLE:
      ESP_LOGV(TAG, "Dropping stale byte 0x%02X", byte);
      return;

    case ProtocolState::WAIT_START_ACK:
      if (byte != ESCAPE) {
        ESP_LOGW(TAG, "Start communication returned 0x%02X instead of 0x10", byte);
        this->fail_current_request_("unexpected start response");
        return;
      }
      this->write_array(this->current_request_message_.data(), this->current_request_message_.size());
      this->state_ = ProtocolState::WAIT_DATA_AVAILABLE;
      this->state_started_ms_ = millis();
      this->previous_byte_ = 0;
      return;

    case ProtocolState::WAIT_DATA_AVAILABLE:
      if (this->previous_byte_ == ESCAPE && byte == START_COMMUNICATION) {
        this->write_byte(ESCAPE);
        this->state_ = ProtocolState::WAIT_RESPONSE;
        this->state_started_ms_ = millis();
        this->previous_byte_ = 0;
        this->response_buffer_.clear();
        return;
      }
      this->previous_byte_ = byte;
      return;

    case ProtocolState::WAIT_RESPONSE:
      this->response_buffer_.push_back(byte);
      if (this->response_buffer_.size() > 4 && this->response_buffer_[this->response_buffer_.size() - 2] == ESCAPE &&
          this->response_buffer_[this->response_buffer_.size() - 1] == END) {
        const auto response = this->fix_duplicated_bytes_(this->response_buffer_);
        this->finish_current_request_(response);
      }
      return;
  }
}

void Thz55EcoComponent::handle_timeout_() {
  if (this->state_ == ProtocolState::IDLE)
    return;
  if (millis() - this->state_started_ms_ < this->byte_timeout_ms_)
    return;

  if (this->state_ == ProtocolState::WAIT_START_ACK && this->attempt_ < this->max_retry_) {
    ESP_LOGD(TAG, "Start communication timed out waiting for 0x10, retrying");
    this->send_start_communication_();
    return;
  }

  this->fail_current_request_("timeout");
}

void Thz55EcoComponent::fail_current_request_(const char *reason) {
  ESP_LOGW(TAG, "Request %02X (%s) failed in %s: %s", this->current_request_,
           this->current_label_ == nullptr ? "unknown" : this->current_label_, this->state_name_(), reason);
  this->end_cycle_();
}

void Thz55EcoComponent::finish_current_request_(const std::vector<uint8_t> &response) {
  if (!this->verify_header_(response)) {
    this->fail_current_request_("invalid response");
    return;
  }
  if (response.size() < 4 || response[3] != this->current_request_) {
    ESP_LOGW(TAG, "Request %02X (%s) returned response key %02X", this->current_request_,
             this->current_label_ == nullptr ? "unknown" : this->current_label_, response.size() >= 4 ? response[3] : 0);
    this->state_ = ProtocolState::IDLE;
    this->start_next_request_();
    return;
  }

  this->decode_and_publish_(this->current_request_, response);
  this->state_ = ProtocolState::IDLE;
  this->response_buffer_.clear();
  this->previous_byte_ = 0;
  this->start_next_request_();
}

void Thz55EcoComponent::end_cycle_() {
  this->cycle_started_ = false;
  this->state_ = ProtocolState::IDLE;
  this->response_buffer_.clear();
  this->previous_byte_ = 0;
  this->current_request_message_.clear();
}

const char *Thz55EcoComponent::state_name_() const {
  switch (this->state_) {
    case ProtocolState::IDLE:
      return "IDLE";
    case ProtocolState::WAIT_START_ACK:
      return "WAIT_START_ACK";
    case ProtocolState::WAIT_DATA_AVAILABLE:
      return "WAIT_DATA_AVAILABLE";
    case ProtocolState::WAIT_RESPONSE:
      return "WAIT_RESPONSE";
  }
  return "UNKNOWN";
}

std::vector<uint8_t> Thz55EcoComponent::create_request_message_(uint8_t request) const {
  std::vector<uint8_t> message{HEADER_START, GET, 0x00, request, ESCAPE, END};
  message[2] = this->calculate_checksum_(message);
  return this->add_duplicated_bytes_(message);
}

uint8_t Thz55EcoComponent::calculate_checksum_(const std::vector<uint8_t> &data) const {
  uint8_t checksum = 0;
  if (data.size() < 5)
    return checksum;

  for (size_t index = 0; index + 2 < data.size(); index++) {
    if (index == 2)
      continue;
    checksum = static_cast<uint8_t>(checksum + data[index]);
  }
  return checksum;
}

std::vector<uint8_t> Thz55EcoComponent::add_duplicated_bytes_(const std::vector<uint8_t> &data) const {
  if (data.size() < 4)
    return data;

  std::vector<uint8_t> result;
  result.reserve(data.size() + 4);
  result.push_back(data[0]);
  result.push_back(data[1]);
  for (size_t index = 2; index + 2 < data.size(); index++) {
    result.push_back(data[index]);
    if (data[index] == ESCAPE) {
      result.push_back(ESCAPE);
    } else if (data[index] == 0x2B) {
      result.push_back(0x18);
    }
  }
  result.push_back(data[data.size() - 2]);
  result.push_back(data[data.size() - 1]);
  return result;
}

std::vector<uint8_t> Thz55EcoComponent::fix_duplicated_bytes_(const std::vector<uint8_t> &data) const {
  if (data.size() < 4)
    return data;

  std::vector<uint8_t> fixed;
  fixed.reserve(data.size());
  size_t index = 0;
  while (index + 2 < data.size()) {
    const uint8_t byte = data[index];
    const uint8_t next = data[index + 1];
    if (byte == ESCAPE && next == ESCAPE) {
      fixed.push_back(ESCAPE);
      index += 2;
    } else if (byte == 0x2B && next == 0x18) {
      fixed.push_back(0x2B);
      index += 2;
    } else {
      fixed.push_back(byte);
      index++;
    }
  }
  fixed.push_back(data[data.size() - 2]);
  fixed.push_back(data[data.size() - 1]);
  return fixed;
}

bool Thz55EcoComponent::verify_header_(const std::vector<uint8_t> &response) const {
  if (response.size() < 5)
    return false;
  if (response[0] != HEADER_START)
    return false;
  if (response[1] != GET && response[1] != SET)
    return false;
  if (response[response.size() - 2] != ESCAPE || response[response.size() - 1] != END)
    return false;

  const uint8_t expected = this->calculate_checksum_(response);
  if (response[2] != expected) {
    ESP_LOGW(TAG, "Invalid checksum: got %02X, expected %02X", response[2], expected);
    return false;
  }
  return true;
}

bool Thz55EcoComponent::request_has_registered_point_(uint8_t request) const {
  for (const auto &point : THZ55ECO_POINTS) {
    if (point.request != request)
      continue;

    for (const auto &registration : this->numeric_sensors_) {
      if (registration.key == point.key)
        return true;
    }
    for (const auto &registration : this->binary_sensors_) {
      if (registration.key == point.key)
        return true;
    }
  }
  return false;
}

void Thz55EcoComponent::decode_and_publish_(uint8_t request, const std::vector<uint8_t> &response) {
  for (const auto &point : THZ55ECO_POINTS) {
    if (point.request != request)
      continue;
    if (static_cast<size_t>(point.offset) + point.size > response.size())
      continue;

    if (point.bit >= 0) {
      const bool value = this->read_bit_(response[point.offset], static_cast<uint8_t>(point.bit));
      for (const auto &registration : this->binary_sensors_) {
        if (registration.key == point.key) {
          registration.sensor->publish_state(value);
          break;
        }
      }
    } else {
      const int32_t raw = this->read_signed_big_endian_(response, point);
      const float value = static_cast<float>(raw) * point.scale;
      for (const auto &registration : this->numeric_sensors_) {
        if (registration.key == point.key) {
          registration.sensor->publish_state(value);
          break;
        }
      }
    }
  }
}

int32_t Thz55EcoComponent::read_signed_big_endian_(const std::vector<uint8_t> &response,
                                                   const PointDefinition &point) const {
  int32_t value = 0;
  for (uint8_t index = 0; index < point.size; index++) {
    value = (value << 8) | response[point.offset + index];
  }

  const uint8_t bits = point.size * 8;
  if (bits < 32 && (value & (1 << (bits - 1))) != 0) {
    value -= (1 << bits);
  }
  return value;
}

bool Thz55EcoComponent::read_bit_(uint8_t value, uint8_t bit) const {
  return ((value >> (8 - (bit + 1))) & 0x01) != 0;
}

}  // namespace thz55eco
}  // namespace esphome
