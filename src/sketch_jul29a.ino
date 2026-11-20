#include <Arduino.h>

#include <secrets.h>

#ifndef CSI_HOST_IP
#define CSI_HOST_IP "192.168.1.100"
#endif
#ifndef CSI_UDP_PORT
#define CSI_UDP_PORT 5005
#endif
// TX softAP network that RX nodes join. Placeholders keep CI building;
// real values live in gitignored include/secrets.h.
#ifndef CSI_TX_SSID
#define CSI_TX_SSID "csi-tx"
#endif
#ifndef CSI_TX_PASSWORD
#define CSI_TX_PASSWORD "csi-link-key"
#endif

#include "csi/CsiCollector.h"
#include "csi/CsiFrameV2.h"
#include "csi/TxBeacon.h"
#include "display/OledDisplay.h"
#include "network/WifiConnection.h"
#include "time/NetworkClock.h"

// RX nodes join the TX softAP first, then fall back to plain station
// networks. The TX softAP is the measurement network; the fallbacks only
// give the board uplink for the clock when no TX node is around.
struct WifiCredential {
  const char* ssid;
  const char* password;
};

WifiCredential kWifiNetworks[] = {
    {CSI_TX_SSID, CSI_TX_PASSWORD},
    {WIFI_SSID, WIFI_PASSWORD},
#ifdef WIFI_SSID2
    {WIFI_SSID2, WIFI_PASSWORD2},
#endif
#ifdef WIFI_SSID3
    {WIFI_SSID3, WIFI_PASSWORD3},
#endif
};

#if CSI_NODE_ID == 0
#error CSI_NODE_ID is unset: set -D CSI_NODE_ID per board (255 TX, 1..254 RX)
#endif
#if CSI_NODE_ID == 255
constexpr bool kIsTxNode = true;
#else
constexpr bool kIsTxNode = false;
#endif

namespace {

// RX nodes only measure the TX softAP link. Fallback networks below exist
// for uplink/clock when no TX node is around; frames captured there must
// never be forwarded as measurement data (wrong-link corruption).
const char* activeSsid = nullptr;
bool onMeasurementNetwork() {
  return !kIsTxNode && activeSsid != nullptr &&
         strcmp(activeSsid, CSI_TX_SSID) == 0;
}

constexpr uint32_t kWifiTimeoutMs = 15000;
constexpr uint32_t kNtpTimeoutMs = 30000;
constexpr uint32_t kDisplayIntervalMs = 1000;

OledDisplay display;
WifiConnection wifi;
NetworkClock networkClock;
CsiCollector csiCollector;
TxBeacon txBeacon;
uint32_t lastDisplayUpdate = 0;
bool streamStarted = false;

void formatCsiStatus(char* buffer, size_t size) {
  if (kIsTxNode) {
    if (!txBeacon.isRunning()) {
      snprintf(buffer, size, "TX: waiting");
      return;
    }
    uint32_t rate = 0;
    uint8_t clients = 0;
    txBeacon.sampleStatus(rate, clients);
    snprintf(buffer, size, "TX %lu/s c%u", static_cast<unsigned long>(rate),
             clients);
    return;
  }
  if (!csiCollector.isRunning()) {
    snprintf(buffer, size, "CSI: waiting");
    return;
  }

  uint32_t rate = 0;
  uint16_t drops = 0;
  csiCollector.sampleStatus(rate, drops);
  snprintf(buffer, size, "N%u %lu/s d%u", CSI_NODE_ID,
           static_cast<unsigned long>(rate), drops);
}

void connectAndStartClock() {
  csiCollector.stop();
  streamStarted = false;
  activeSsid = nullptr;
  bool wifi_ok = false;
  for (const auto& net : kWifiNetworks) {
    display.showStatus("Connecting WiFi...", net.ssid);
    if (wifi.connect(net.ssid, net.password, kWifiTimeoutMs)) {
      wifi_ok = true;
      activeSsid = net.ssid;
      break;
    }
    display.showStatus("WiFi connection", "failed", "Retrying...");
  }
  if (!wifi_ok) {
    return;
  }

  display.showStatus("Syncing time...", "NTP");
  networkClock.begin();

  if (!onMeasurementNetwork()) {
    Serial.print("Joined uplink only (");
    Serial.print(activeSsid);
    Serial.println("): CSI capture disabled until TX network is up");
    return;
  }

  const uint32_t gatewayWaitStarted = millis();
  while (!csiCollector.isRunning() && millis() - gatewayWaitStarted < 2000) {
    if (csiCollector.start(CSI_HOST_IP, CSI_UDP_PORT)) {
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

  if (kIsTxNode) {
    Serial.printf("TX node: softAP starting\n");
    if (txBeacon.start(CSI_TX_SSID, CSI_TX_PASSWORD, CSI_TX_CHANNEL,
                       CSI_HOST_IP, CSI_UDP_PORT)) {
      Serial.print("TX_BEACON_START ");
      Serial.println(CSI_TX_SSID);
      streamStarted = true;
    } else {
      display.showStatus("TX softAP", "failed", "Retrying...");
    }
    return;
  }

  connectAndStartClock();
}

void loop() {
  if (kIsTxNode) {
    txBeacon.service();
    if (millis() - lastDisplayUpdate >= kDisplayIntervalMs) {
      lastDisplayUpdate = millis();
      char status[22];
      formatCsiStatus(status, sizeof(status));
      display.showClock(nullptr, status);
    }
    return;
  }

  if (!wifi.isConnected()) {
    csiCollector.stop();
    streamStarted = false;
    display.showStatus("WiFi connection", "lost", "Retrying...");
    Serial.println("WiFi disconnected; reconnecting");
    connectAndStartClock();
    return;
  }

  if (!streamStarted && onMeasurementNetwork() &&
      csiCollector.start(CSI_HOST_IP, CSI_UDP_PORT)) {
    Serial.printf("CSI_UDP_START %s:%u\n", CSI_HOST_IP,
                  static_cast<unsigned>(CSI_UDP_PORT));
    streamStarted = true;
  }
  if (streamStarted) {
    csiCollector.sendPending();
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
