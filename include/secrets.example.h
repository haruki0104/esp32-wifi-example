#pragma once

#define WIFI_SSID "your-wifi-ssid"
#define WIFI_PASSWORD "your-wifi-password"
#define CSI_HOST_IP "192.168.1.100"
#define CSI_UDP_PORT 5005

// MQTT distribution (Phase 0 defaults; trusted LAN only).
// Broker runs on the host computer; keep real values in the ignored
// local include/secrets.h, never in this example file.
#define MQTT_BROKER_IP "192.168.1.100"
#define MQTT_BROKER_PORT 1883
#define MQTT_USERNAME ""
#define MQTT_PASSWORD ""
#define CSI_DEVICE_ID "esp32-csi-01"
