#pragma once

// Phase 1 concrete UDP adapter behind the FramePublisher seam. Behavior is
// identical to CsiCollector::sendPending: one UDP datagram per CSI1 frame.
// Not yet wired into the sketch; wiring happens in Phase 2 so the verified
// UDP capture path stays untouched until the MQTT adapter exists.

#include <WiFiUdp.h>

#include "FramePublisher.h"

namespace csi {

class UdpFramePublisher : public FramePublisher {
 public:
  bool begin(const char* hostIp, uint16_t hostPort,
             uint16_t localPort = 5006);
  void end();
  bool publish(const CsiFrame& frame) override;
  void service() override {}
  bool ready() const override;
  uint32_t transportDrops() const override { return transportDrops_; }

 private:
  WiFiUDP udp_;
  IPAddress hostIp_;
  uint16_t hostPort_ = 0;
  bool begun_ = false;
  uint32_t transportDrops_ = 0;
};

}  // namespace csi
