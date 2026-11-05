#pragma once

#include <Arduino.h>
#include <stddef.h>
#include <stdint.h>

class CsiCollector {
 public:
  bool start();
  void stop();
  bool isRunning() const;
  void sampleStatus(uint32_t& ratePerSec, uint16_t& recentDrops);
  void writePending();

 private:
  bool running_ = false;
  uint32_t lastStatusMs_ = 0;
  uint32_t lastQueued_ = 0;
  uint32_t lastDrops_ = 0;
  uint32_t cachedRate_ = 0;
  uint16_t cachedDrops_ = 0;
};
