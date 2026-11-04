#pragma once

#include <Arduino.h>
#include <time.h>

class NetworkClock {
 public:
  void begin();
  bool waitForSync(uint32_t timeoutMs) const;
  bool getLocalTime(tm& result) const;

 private:
  bool isSynchronized() const;
};
