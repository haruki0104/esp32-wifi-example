#pragma once

#include <Arduino.h>

class WifiConnection {
 public:
  bool connect(const char* ssid, const char* password, uint32_t timeoutMs);
  bool isConnected() const;
};
