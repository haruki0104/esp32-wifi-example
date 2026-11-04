#include "WifiConnection.h"

#include <WiFi.h>

bool WifiConnection::connect(const char* ssid, const char* password,
                             uint32_t timeoutMs) {
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  Serial.printf("Connecting to %s", ssid);

  const uint32_t startedAt = millis();
  while (!isConnected() && millis() - startedAt < timeoutMs) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();

  if (!isConnected()) {
    Serial.println("WiFi connection failed");
    return false;
  }

  Serial.print("Connected; IP: ");
  Serial.println(WiFi.localIP());
  return true;
}

bool WifiConnection::isConnected() const {
  return WiFi.status() == WL_CONNECTED;
}
