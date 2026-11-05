#include "CsiCollector.h"

#include <WiFi.h>
#include <esp_wifi.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <lwip/ip_addr.h>
#include <ping/ping_sock.h>
#include <string.h>

namespace {

constexpr uint32_t kMagic = 0x31495343;  // little-endian bytes 'C','S','I','1'
constexpr uint8_t kVersion = 1;
constexpr size_t kSlotCount = 4;
constexpr size_t kMaxLen = 512;
constexpr uint32_t kPingIntervalMs = 100;
constexpr int kFramesPerLoop = 4;

#if CONFIG_IDF_TARGET_ESP32
constexpr uint8_t kChipId = 1;
#elif CONFIG_IDF_TARGET_ESP32S3
constexpr uint8_t kChipId = 2;
#else
#error CSI collector supports only ESP32 and ESP32-S3
#endif

struct __attribute__((packed)) FrameHeader {
  uint32_t magic;
  uint8_t version;
  uint8_t chip;
  uint16_t len;
  uint32_t seq;
  int8_t rssi;
  uint8_t channel;
  uint8_t firstWordInvalid;
  uint8_t reserved;
  uint8_t mac[6];
  uint16_t drops;
};

static_assert(sizeof(FrameHeader) == 24, "CSI frame header must be 24 bytes");

struct Slot {
  int8_t rssi;
  uint8_t channel;
  uint8_t firstWordInvalid;
  uint8_t mac[6];
  uint16_t len;
  uint32_t seq;
  int8_t data[kMaxLen];
};

Slot slots[kSlotCount];
QueueHandle_t freeQueue = nullptr;
QueueHandle_t readyQueue = nullptr;
esp_ping_handle_t ping = nullptr;
portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
volatile bool accepting = false;
uint8_t apBssid[6] = {};
uint32_t queuedCount = 0;
uint32_t dropCount = 0;
uint32_t nextSeq = 0;
uint32_t emittedDrops = 0;

void noteDrop() {
  portENTER_CRITICAL(&mux);
  dropCount++;
  portEXIT_CRITICAL(&mux);
}

void csiRxCallback(void* ctx, wifi_csi_info_t* info) {
  if (!accepting || ctx == nullptr || info == nullptr || info->buf == nullptr) {
    return;
  }
  if (memcmp(info->mac, ctx, 6) != 0) {
    return;
  }
  if (info->len > kMaxLen) {
    noteDrop();
    return;
  }

  uint8_t index = 0;
  if (xQueueReceive(freeQueue, &index, 0) != pdTRUE) {
    noteDrop();
    return;
  }

  Slot& slot = slots[index];
  slot.rssi = info->rx_ctrl.rssi;
  slot.channel = static_cast<uint8_t>(info->rx_ctrl.channel);
  slot.firstWordInvalid = info->first_word_invalid ? 1 : 0;
  memcpy(slot.mac, info->mac, sizeof(slot.mac));
  slot.len = info->len;
  if (info->len > 0) {
    memcpy(slot.data, info->buf, info->len);
  }

  portENTER_CRITICAL(&mux);
  slot.seq = nextSeq++;
  queuedCount++;
  portEXIT_CRITICAL(&mux);

  if (xQueueSend(readyQueue, &index, 0) != pdTRUE) {
    xQueueSend(freeQueue, &index, 0);
    noteDrop();
  }
}

bool ensureQueues() {
  if (freeQueue != nullptr && readyQueue != nullptr) {
    return true;
  }

  freeQueue = xQueueCreate(kSlotCount, sizeof(uint8_t));
  readyQueue = xQueueCreate(kSlotCount, sizeof(uint8_t));
  if (freeQueue == nullptr || readyQueue == nullptr) {
    return false;
  }

  for (uint8_t index = 0; index < kSlotCount; ++index) {
    if (xQueueSend(freeQueue, &index, 0) != pdTRUE) {
      return false;
    }
  }
  return true;
}

void returnReadySlots() {
  if (readyQueue == nullptr || freeQueue == nullptr) {
    return;
  }

  uint8_t index = 0;
  while (xQueueReceive(readyQueue, &index, 0) == pdTRUE) {
    xQueueSend(freeQueue, &index, 0);
  }
}

bool startPing(const IPAddress& gateway) {
  esp_ping_config_t config = {};
  config.count = ESP_PING_COUNT_INFINITE;
  config.interval_ms = kPingIntervalMs;
  config.timeout_ms = 80;
  config.data_size = 1;
  config.tos = 0;
  config.ttl = 64;
  config.task_stack_size = 3072;
  config.task_prio = 2;
  config.interface = 0;
  IP_ADDR4(&config.target_addr, gateway[0], gateway[1], gateway[2], gateway[3]);

  esp_ping_callbacks_t callbacks = {};
  if (esp_ping_new_session(&config, &callbacks, &ping) != ESP_OK) {
    ping = nullptr;
    return false;
  }
  if (esp_ping_start(ping) != ESP_OK) {
    esp_ping_delete_session(ping);
    ping = nullptr;
    return false;
  }
  return true;
}

void stopPing() {
  if (ping == nullptr) {
    return;
  }
  esp_ping_stop(ping);
  esp_ping_delete_session(ping);
  ping = nullptr;
}

}  // namespace

bool CsiCollector::start() {
  if (running_) {
    return true;
  }

  const IPAddress gateway = WiFi.gatewayIP();
  if (gateway == IPAddress(0, 0, 0, 0)) {
    return false;
  }
  if (!ensureQueues()) {
    return false;
  }

  WiFi.setSleep(false);

  wifi_ap_record_t ap = {};
  if (esp_wifi_sta_get_ap_info(&ap) != ESP_OK) {
    return false;
  }
  memcpy(apBssid, ap.bssid, sizeof(apBssid));

  wifi_csi_config_t csiConfig = {};
  csiConfig.lltf_en = true;
  csiConfig.htltf_en = false;
  csiConfig.stbc_htltf2_en = false;
  csiConfig.ltf_merge_en = true;
  csiConfig.channel_filter_en = true;
  csiConfig.manu_scale = true;
  csiConfig.shift = 1;
  if (esp_wifi_set_csi_config(&csiConfig) != ESP_OK) {
    return false;
  }
  if (esp_wifi_set_csi_rx_cb(csiRxCallback, apBssid) != ESP_OK) {
    return false;
  }
  if (!startPing(gateway)) {
    esp_wifi_set_csi_rx_cb(nullptr, nullptr);
    return false;
  }

  accepting = true;
  if (esp_wifi_set_csi(true) != ESP_OK) {
    accepting = false;
    stopPing();
    esp_wifi_set_csi_rx_cb(nullptr, nullptr);
    return false;
  }

  const uint32_t now = millis();
  portENTER_CRITICAL(&mux);
  lastQueued_ = queuedCount;
  lastDrops_ = dropCount;
  portEXIT_CRITICAL(&mux);
  lastStatusMs_ = now;
  cachedRate_ = 0;
  cachedDrops_ = 0;
  running_ = true;
  return true;
}

void CsiCollector::stop() {
  accepting = false;
  esp_wifi_set_csi(false);
  esp_wifi_set_csi_rx_cb(nullptr, nullptr);
  vTaskDelay(1);
  stopPing();
  returnReadySlots();
  running_ = false;
}

bool CsiCollector::isRunning() const { return running_; }

void CsiCollector::sampleStatus(uint32_t& ratePerSec, uint16_t& recentDrops) {
  if (!running_) {
    ratePerSec = 0;
    recentDrops = 0;
    return;
  }

  const uint32_t now = millis();
  if (now - lastStatusMs_ < 1000) {
    ratePerSec = cachedRate_;
    recentDrops = cachedDrops_;
    return;
  }

  uint32_t queued = 0;
  uint32_t drops = 0;
  portENTER_CRITICAL(&mux);
  queued = queuedCount;
  drops = dropCount;
  portEXIT_CRITICAL(&mux);

  const uint32_t elapsed = now - lastStatusMs_;
  const uint32_t queuedDelta = queued - lastQueued_;
  const uint32_t dropDelta = drops - lastDrops_;
  cachedRate_ = elapsed == 0 ? 0 : queuedDelta * 1000 / elapsed;
  cachedDrops_ = dropDelta > 0xFFFF ? 0xFFFF : static_cast<uint16_t>(dropDelta);
  lastQueued_ = queued;
  lastDrops_ = drops;
  lastStatusMs_ = now;
  ratePerSec = cachedRate_;
  recentDrops = cachedDrops_;
}

void CsiCollector::writePending() {
  if (!running_ || readyQueue == nullptr || freeQueue == nullptr) {
    return;
  }

  uint8_t index = 0;
  for (int written = 0; written < kFramesPerLoop; ++written) {
    if (xQueueReceive(readyQueue, &index, 0) != pdTRUE) {
      return;
    }

    const Slot& slot = slots[index];
    uint32_t dropsNow = 0;
    portENTER_CRITICAL(&mux);
    dropsNow = dropCount;
    portEXIT_CRITICAL(&mux);
    const uint32_t dropsSince = dropsNow - emittedDrops;
    emittedDrops = dropsNow;

    FrameHeader header = {};
    header.magic = kMagic;
    header.version = kVersion;
    header.chip = kChipId;
    header.len = slot.len;
    header.seq = slot.seq;
    header.rssi = slot.rssi;
    header.channel = slot.channel;
    header.firstWordInvalid = slot.firstWordInvalid;
    memcpy(header.mac, slot.mac, sizeof(header.mac));
    header.drops = dropsSince > 0xFFFF ? 0xFFFF
                                       : static_cast<uint16_t>(dropsSince);

    Serial.write(reinterpret_cast<const uint8_t*>(&header), sizeof(header));
    if (slot.len > 0) {
      Serial.write(reinterpret_cast<const uint8_t*>(slot.data), slot.len);
    }
    xQueueSend(freeQueue, &index, 0);
  }
}
