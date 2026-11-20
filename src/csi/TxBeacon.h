#pragma once

#include <Arduino.h>
#include <WiFiUdp.h>
#include <stddef.h>
#include <stdint.h>

// TX node role (M0): runs a softAP and broadcasts a fixed-rate stream so RX
// nodes receive packets at a known cadence. Also unicasts v2 heartbeat
// frames (role=TX, node_id=255, empty payload) to the host so the aggregator
// sees TX liveness. The AP uplink is not used for CSI.
class TxBeacon {
 public:
  // ssid/password/channel select the softAP network RX nodes join.
  // hostIp/hostPort is the unicast heartbeat destination.
  bool start(const char* ssid, const char* password, uint8_t channel,
             const char* hostIp, uint16_t hostPort);
  void stop();
  bool isRunning() const { return running_; }
  // Call from loop(): emits due broadcasts and the 1 Hz heartbeat.
  void service();
  void sampleStatus(uint32_t& broadcastsPerSec, uint8_t& stationCount);

 private:
  WiFiUDP udp_;
  IPAddress broadcastIp_;
  IPAddress hostIp_;
  uint16_t hostPort_ = 0;
  uint32_t intervalMs_ = 20;
  uint32_t lastBeaconMs_ = 0;
  uint32_t lastHeartbeatMs_ = 0;
  uint32_t lastStatusMs_ = 0;
  uint32_t beacons_ = 0;
  uint32_t lastBeacons_ = 0;
  uint32_t cachedRate_ = 0;
  bool running_ = false;
};
