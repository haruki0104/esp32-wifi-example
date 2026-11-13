#pragma once

#include <Arduino.h>
#include <WiFiUdp.h>
#include <stddef.h>
#include <stdint.h>

class CsiCollector {
 public:
  bool start(const char* hostIp, uint16_t hostPort);
  void stop();
  bool isRunning() const;
  void sampleStatus(uint32_t& ratePerSec, uint16_t& recentDrops);
  void sendPending();

 private:
  WiFiUDP udp_;
  IPAddress hostIp_;
  uint16_t hostPort_ = 0;
  bool running_ = false;
  uint32_t lastStatusMs_ = 0;
  uint32_t lastQueued_ = 0;
  uint32_t lastDrops_ = 0;
  uint32_t cachedRate_ = 0;
  uint16_t cachedDrops_ = 0;
};
