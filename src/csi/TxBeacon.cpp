#include "TxBeacon.h"

#include <WiFi.h>
#include <esp_timer.h>
#include <esp_wifi.h>

#include "CsiFrameV2.h"

// Measurement traffic must not land on the CSI archive port: the host
// validates every CSI1 datagram there, and beacons carry no header.
constexpr uint16_t kBeaconPort = 5007;
constexpr uint32_t kHeartbeatIntervalMs = 1000;

bool TxBeacon::start(const char* ssid, const char* password, uint8_t channel,
                     const char* hostIp, uint16_t hostPort) {
  if (running_) {
    return true;
  }
  if (ssid == nullptr || hostIp == nullptr || hostPort == 0 ||
      !hostIp_.fromString(hostIp)) {
    return false;
  }
  hostPort_ = hostPort;

  WiFi.mode(WIFI_AP);
  WiFi.setSleep(false);
  if (!WiFi.softAP(ssid, password, channel)) {
    return false;
  }

  // Limited broadcast stays on the softAP L2 segment.
  broadcastIp_.fromString("255.255.255.255");
  intervalMs_ = CSI_TX_INTERVAL_MS;
  if (intervalMs_ == 0) {
    intervalMs_ = 20;
  }

  const uint32_t now = millis();
  lastBeaconMs_ = now;
  lastHeartbeatMs_ = now;
  lastStatusMs_ = now;
  beacons_ = 0;
  lastBeacons_ = 0;
  cachedRate_ = 0;
  running_ = true;
  return true;
}

void TxBeacon::stop() {
  if (!running_) {
    return;
  }
  running_ = false;
  udp_.stop();
  WiFi.softAPdisconnect(true);
  WiFi.mode(WIFI_OFF);
}

void TxBeacon::service() {
  if (!running_) {
    return;
  }

  const uint32_t now = millis();
  while (now - lastBeaconMs_ >= intervalMs_) {
    lastBeaconMs_ += intervalMs_;
    const uint8_t probe = 0xAA;
    if (udp_.beginPacket(broadcastIp_, kBeaconPort) == 1) {
      if (udp_.write(&probe, sizeof(probe)) == sizeof(probe)) {
        if (udp_.endPacket() == 1) {
          beacons_++;
        }
      }
    }
  }

  if (now - lastHeartbeatMs_ >= kHeartbeatIntervalMs) {
    lastHeartbeatMs_ = now;
    csi::FrameV2Header header = {};
    header.magic = csi::kMagic;
    header.version = csi::kVersion;
    header.chip = csi::kChipId;
    header.len = 0;
    header.rxTsUs = static_cast<uint64_t>(esp_timer_get_time());
    header.nodeId = csi::kNodeTx;
    header.flags = csi::kFlagRoleTx;
    const uint8_t liveChannel = static_cast<uint8_t>(WiFi.channel());
    header.channel = liveChannel != 0 ? liveChannel : CSI_TX_CHANNEL;
    uint8_t self[6] = {};
    if (esp_wifi_get_mac(WIFI_IF_AP, self) == ESP_OK) {
      memcpy(header.mac, self, sizeof(header.mac));
    }
    if (udp_.beginPacket(hostIp_, hostPort_) == 1) {
      if (udp_.write(reinterpret_cast<const uint8_t*>(&header),
                     sizeof(header)) == sizeof(header)) {
        udp_.endPacket();
      }
    }
  }
}

void TxBeacon::sampleStatus(uint32_t& broadcastsPerSec,
                            uint8_t& stationCount) {
  if (!running_) {
    broadcastsPerSec = 0;
    stationCount = 0;
    return;
  }
  const uint32_t now = millis();
  if (now - lastStatusMs_ >= 1000) {
    const uint32_t elapsed = now - lastStatusMs_;
    cachedRate_ = elapsed == 0 ? 0 : (beacons_ - lastBeacons_) * 1000 / elapsed;
    lastBeacons_ = beacons_;
    lastStatusMs_ = now;
  }
  broadcastsPerSec = cachedRate_;
  stationCount = static_cast<uint8_t>(WiFi.softAPgetStationNum());
}
