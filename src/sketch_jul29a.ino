#include <Arduino.h>

#include <secrets.h>

#include "display/OledDisplay.h"
#include "network/WifiConnection.h"
#include "time/NetworkClock.h"

namespace {

constexpr uint32_t kWifiTimeoutMs = 15000;
constexpr uint32_t kNtpTimeoutMs = 30000;
constexpr uint32_t kDisplayIntervalMs = 1000;

OledDisplay display;
WifiConnection wifi;
NetworkClock networkClock;
uint32_t lastDisplayUpdate = 0;

void connectAndStartClock() {
  display.showStatus("Connecting WiFi...", WIFI_SSID);
  if (!wifi.connect(WIFI_SSID, WIFI_PASSWORD, kWifiTimeoutMs)) {
    display.showStatus("WiFi connection", "failed", "Retrying...");
    return;
  }

  display.showStatus("Syncing time...", "NTP");
  networkClock.begin();
  if (!networkClock.waitForSync(kNtpTimeoutMs)) {
    Serial.println("NTP sync is pending; SNTP will continue in the background");
  }
}

}  // namespace

void setup() {
  Serial.begin(115200);

  if (!display.begin()) {
    Serial.println("OLED initialization failed");
    for (;;) {
      delay(1000);
    }
  }
  Serial.println("OLED initialized");

  connectAndStartClock();
}

void loop() {
  if (!wifi.isConnected()) {
    Serial.println("WiFi disconnected; reconnecting");
    connectAndStartClock();
    return;
  }

  if (millis() - lastDisplayUpdate >= kDisplayIntervalMs) {
    lastDisplayUpdate = millis();
    tm localTime{};
    if (networkClock.getLocalTime(localTime)) {
      display.showClock(&localTime);
    } else {
      display.showClock(nullptr);
    }
  }
}
