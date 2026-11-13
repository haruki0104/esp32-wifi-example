#include <Arduino.h>

#include <secrets.h>

#ifndef CSI_HOST_IP
#define CSI_HOST_IP "192.168.1.100"
#endif
#ifndef CSI_UDP_PORT
#define CSI_UDP_PORT 5005
#endif

#include "csi/CsiCollector.h"
#include "display/OledDisplay.h"
#include "network/WifiConnection.h"
#include "time/NetworkClock.h"

// Fallback Wi-Fi networks come from gitignored include/secrets.h (see
// include/secrets.example.h). Slots without credentials are skipped, so a
// single-network setup only defines WIFI_SSID/WIFI_PASSWORD.
struct WifiCredential {
  const char* ssid;
  const char* password;
};

WifiCredential kWifiNetworks[] = {
    {WIFI_SSID, WIFI_PASSWORD},
#ifdef WIFI_SSID2
    {WIFI_SSID2, WIFI_PASSWORD2},
#endif
#ifdef WIFI_SSID3
    {WIFI_SSID3, WIFI_PASSWORD3},
#endif
};

namespace {

constexpr uint32_t kWifiTimeoutMs = 15000;
constexpr uint32_t kNtpTimeoutMs = 30000;
constexpr uint32_t kDisplayIntervalMs = 1000;

OledDisplay display;
WifiConnection wifi;
NetworkClock networkClock;
CsiCollector csi;
uint32_t lastDisplayUpdate = 0;
bool streamStarted = false;

void formatCsiStatus(char* buffer, size_t size) {
  if (!csi.isRunning()) {
    snprintf(buffer, size, "CSI: waiting");
    return;
  }

  uint32_t rate = 0;
  uint16_t drops = 0;
  csi.sampleStatus(rate, drops);
  snprintf(buffer, size, "CSI %lu/s d%u", static_cast<unsigned long>(rate),
           drops);
}

void connectAndStartClock() {
  csi.stop();
  streamStarted = false;
  bool wifi_ok = false;
  for (const auto& net : kWifiNetworks) {
    display.showStatus("Connecting WiFi...", net.ssid);
    if (wifi.connect(net.ssid, net.password, kWifiTimeoutMs)) {
      wifi_ok = true;
      break;
    }
    display.showStatus("WiFi connection", "failed", "Retrying...");
  }
  if (!wifi_ok) {
    return;
  }

  display.showStatus("Syncing time...", "NTP");
  networkClock.begin();

  const uint32_t gatewayWaitStarted = millis();
  while (!csi.isRunning() && millis() - gatewayWaitStarted < 2000) {
    if (csi.start(CSI_HOST_IP, CSI_UDP_PORT)) {
      Serial.printf("CSI_UDP_START %s:%u\n", CSI_HOST_IP,
                    static_cast<unsigned>(CSI_UDP_PORT));
      streamStarted = true;
    } else {
      delay(50);
    }
  }

  if (!streamStarted && !networkClock.waitForSync(kNtpTimeoutMs)) {
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
    csi.stop();
    streamStarted = false;
    display.showStatus("WiFi connection", "lost", "Retrying...");
    Serial.println("WiFi disconnected; reconnecting");
    connectAndStartClock();
    return;
  }

  if (!streamStarted && csi.start(CSI_HOST_IP, CSI_UDP_PORT)) {
    Serial.printf("CSI_UDP_START %s:%u\n", CSI_HOST_IP,
                  static_cast<unsigned>(CSI_UDP_PORT));
    streamStarted = true;
  }
  if (streamStarted) {
    csi.sendPending();
  }

  if (millis() - lastDisplayUpdate >= kDisplayIntervalMs) {
    lastDisplayUpdate = millis();
    char status[22];
    formatCsiStatus(status, sizeof(status));
    tm localTime{};
    if (networkClock.getLocalTime(localTime)) {
      display.showClock(&localTime, status);
    } else {
      display.showClock(nullptr, status);
    }
  }
}
