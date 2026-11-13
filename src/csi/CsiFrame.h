#pragma once

// Phase 1 publisher seam: transport-agnostic CSI frame value and wire codec.
// The encoded bytes must stay identical to CsiCollector::sendPending output:
// packed 24-byte little-endian CSI1 header followed by the raw payload.

#include <stddef.h>
#include <stdint.h>

namespace csi {

constexpr uint32_t kFrameMagic = 0x31495343;  // 'C','S','I','1' little-endian
constexpr uint8_t kFrameVersion = 1;
constexpr size_t kFrameHeaderSize = 24;
constexpr size_t kFrameMaxPayload = 512;
constexpr size_t kFrameMaxWireSize = kFrameHeaderSize + kFrameMaxPayload;

struct CsiFrame {
  uint8_t chip = 0;
  uint32_t seq = 0;
  int8_t rssi = 0;
  uint8_t channel = 0;
  bool firstWordInvalid = false;
  uint8_t mac[6] = {};
  uint16_t drops = 0;
  uint16_t len = 0;
  uint8_t payload[kFrameMaxPayload] = {};
};

// Encodes header + payload into out. Returns bytes written, or 0 when
// outSize is too small or len exceeds the maximum.
inline size_t encodeCsiFrame(const CsiFrame& frame, uint8_t* out,
                             size_t outSize) {
  if (frame.len > kFrameMaxPayload) {
    return 0;
  }
  const size_t total = kFrameHeaderSize + frame.len;
  if (out == nullptr || outSize < total) {
    return 0;
  }
  out[0] = static_cast<uint8_t>(kFrameMagic);
  out[1] = static_cast<uint8_t>(kFrameMagic >> 8);
  out[2] = static_cast<uint8_t>(kFrameMagic >> 16);
  out[3] = static_cast<uint8_t>(kFrameMagic >> 24);
  out[4] = kFrameVersion;
  out[5] = frame.chip;
  out[6] = static_cast<uint8_t>(frame.len);
  out[7] = static_cast<uint8_t>(frame.len >> 8);
  out[8] = static_cast<uint8_t>(frame.seq);
  out[9] = static_cast<uint8_t>(frame.seq >> 8);
  out[10] = static_cast<uint8_t>(frame.seq >> 16);
  out[11] = static_cast<uint8_t>(frame.seq >> 24);
  out[12] = static_cast<uint8_t>(frame.rssi);
  out[13] = frame.channel;
  out[14] = frame.firstWordInvalid ? 1 : 0;
  out[15] = 0;  // reserved
  for (int i = 0; i < 6; ++i) {
    out[16 + i] = frame.mac[i];
  }
  out[22] = static_cast<uint8_t>(frame.drops);
  out[23] = static_cast<uint8_t>(frame.drops >> 8);
  for (uint16_t i = 0; i < frame.len; ++i) {
    out[kFrameHeaderSize + i] = frame.payload[i];
  }
  return total;
}

}  // namespace csi
