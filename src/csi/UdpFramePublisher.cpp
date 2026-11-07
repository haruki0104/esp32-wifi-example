#include "UdpFramePublisher.h"

namespace csi {

bool UdpFramePublisher::begin(const char* hostIp, uint16_t hostPort,
                              uint16_t localPort) {
  if (hostIp == nullptr || hostPort == 0 || !hostIp_.fromString(hostIp)) {
    return false;
  }
  hostPort_ = hostPort;
  begun_ = udp_.begin(localPort) == 1;
  return begun_;
}

void UdpFramePublisher::end() {
  udp_.stop();
  begun_ = false;
}

bool UdpFramePublisher::publish(const CsiFrame& frame) {
  if (!begun_) {
    ++transportDrops_;
    return false;
  }
  uint8_t wire[kFrameMaxWireSize];
  const size_t total = encodeCsiFrame(frame, wire, sizeof(wire));
  if (total == 0) {
    ++transportDrops_;
    return false;
  }
  bool sent = udp_.beginPacket(hostIp_, hostPort_) == 1;
  if (sent) {
    sent = udp_.write(wire, total) == total && udp_.endPacket() == 1;
  }
  if (!sent) {
    ++transportDrops_;
  }
  return sent;
}

bool UdpFramePublisher::ready() const { return begun_; }

}  // namespace csi
