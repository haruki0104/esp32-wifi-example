#pragma once

// Phase 1 publisher seam: narrow transport interface shared by the UDP and
// future MQTT adapters. Keeps broker/UDP client types out of the CSI and
// application interfaces. All methods run outside the CSI RX callback.

#include <stdint.h>

#include "CsiFrame.h"

namespace csi {

class FramePublisher {
 public:
  virtual ~FramePublisher() = default;
  // Returns true when the frame was handed to the transport.
  virtual bool publish(const CsiFrame& frame) = 0;
  // Runs reconnect/backoff and other periodic work. Must return quickly and
  // never block waiting for a broker.
  virtual void service() = 0;
  // True when publish() may succeed (e.g. destination configured).
  virtual bool ready() const = 0;
  // Transport-side publish failures since reset. Capture-queue drops are
  // counted separately by the acquisition module.
  virtual uint32_t transportDrops() const = 0;
};

}  // namespace csi
