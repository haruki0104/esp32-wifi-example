#pragma once

// Packet layout shared by the RX collector and the TX beacon.
// Mirrors docs/csi-multi-node-design.md "Frame v2 (frozen 2026-11-19)" and
// the host parser in tools/csi_read.py: 32-byte packed little-endian header,
// magic CSI1, version 2, then the raw CSI payload (LLTF, 128 bytes here).
// node_id identifies the RECEIVER (TX node = 255, 0 = unset and rejected by
// the host); mac[6] is the transmitter (peer) MAC.

#include <stdint.h>

// Per-board identity. Set with -D CSI_NODE_ID=<n> in that board's
// platformio.ini env (1..254 RX, 255 TX, 0 unset).
#ifndef CSI_NODE_ID
#define CSI_NODE_ID 0
#endif

// TX beacon knobs (TX role only).
#ifndef CSI_TX_INTERVAL_MS
#define CSI_TX_INTERVAL_MS 20
#endif
#ifndef CSI_TX_CHANNEL
#define CSI_TX_CHANNEL 6
#endif

namespace csi {

constexpr uint32_t kMagic = 0x31495343;  // little-endian bytes 'C','S','I','1'
constexpr uint8_t kVersion = 2;
constexpr uint8_t kNodeTx = 255;
constexpr uint8_t kFlagFirstWordInvalid = 0x01;
constexpr uint8_t kFlagBandwidthMask = 0x06;  // 0 = 20 MHz, 1 = 40 MHz
constexpr uint8_t kFlagRoleTx = 0x08;

#if CONFIG_IDF_TARGET_ESP32
constexpr uint8_t kChipId = 1;
#elif CONFIG_IDF_TARGET_ESP32S3
constexpr uint8_t kChipId = 2;
#else
#error CSI firmware supports only ESP32 and ESP32-S3 (no C5 board definitions)
#endif

struct __attribute__((packed)) FrameV2Header {
  uint32_t magic;    // 0
  uint8_t version;   // 4
  uint8_t chip;      // 5
  uint16_t len;      // 6 payload bytes
  uint32_t seq;      // 8 per node, wraps
  uint64_t rxTsUs;   // 12 esp_timer_get_time() on the RX node at capture
  uint8_t nodeId;    // 20 receiver identity
  uint8_t flags;     // 21 bit0 first-word-invalid, bits1-2 bw, bit3 role
  int8_t rssi;       // 22 dBm
  uint8_t channel;   // 23 primary channel
  uint16_t drops;    // 24 since previous emitted frame
  uint8_t mac[6];    // 26 transmitter (peer) MAC
};

static_assert(sizeof(FrameV2Header) == 32, "CSI v2 header must be 32 bytes");

}  // namespace csi
