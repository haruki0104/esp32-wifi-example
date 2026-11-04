#include "NetworkClock.h"

namespace {

constexpr char kTimezone[] = "CST-8";
constexpr char kNtpServer1[] = "pool.ntp.org";
constexpr char kNtpServer2[] = "time.nist.gov";
constexpr char kNtpServer3[] = "ntp1.tw";
constexpr time_t kMinimumValidEpoch = 1704067200;

}  // namespace

void NetworkClock::begin() {
  configTzTime(kTimezone, kNtpServer1, kNtpServer2, kNtpServer3);
  Serial.println("SNTP started (Asia/Taipei)");
}

bool NetworkClock::waitForSync(uint32_t timeoutMs) const {
  Serial.print("Waiting for NTP");
  const uint32_t startedAt = millis();
  while (!isSynchronized() && millis() - startedAt < timeoutMs) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();

  if (!isSynchronized()) {
    Serial.println("NTP sync not available yet");
    return false;
  }

  tm localTime{};
  if (getLocalTime(localTime)) {
    Serial.printf("NTP synchronized: %04d-%02d-%02d %02d:%02d:%02d\n",
                  localTime.tm_year + 1900, localTime.tm_mon + 1,
                  localTime.tm_mday, localTime.tm_hour, localTime.tm_min,
                  localTime.tm_sec);
  }
  return true;
}

bool NetworkClock::getLocalTime(tm& result) const {
  if (!isSynchronized()) {
    return false;
  }

  const time_t currentTime = time(nullptr);
  return localtime_r(&currentTime, &result) != nullptr;
}

bool NetworkClock::isSynchronized() const {
  return time(nullptr) >= kMinimumValidEpoch;
}
