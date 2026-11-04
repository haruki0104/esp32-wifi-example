#include "OledDisplay.h"

#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Wire.h>

namespace {

constexpr int kScreenWidth = 128;
constexpr int kScreenHeight = 64;
constexpr int kSdaPin = 21;
constexpr int kSclPin = 22;
constexpr uint8_t kI2cAddress = 0x3C;

Adafruit_SSD1306 screen(kScreenWidth, kScreenHeight, &Wire, -1);
const char* const kWeekdays[] = {"Sun", "Mon", "Tue", "Wed",
                                 "Thu", "Fri", "Sat"};

}  // namespace

bool OledDisplay::begin() {
  Wire.begin(kSdaPin, kSclPin);
  return screen.begin(SSD1306_SWITCHCAPVCC, kI2cAddress);
}

void OledDisplay::showStatus(const char* line1, const char* line2,
                             const char* line3) {
  screen.clearDisplay();
  screen.setTextSize(1);
  screen.setTextColor(WHITE);
  screen.setCursor(0, 0);
  screen.print(line1);
  if (line2[0] != '\0') {
    screen.setCursor(0, 16);
    screen.print(line2);
  }
  if (line3[0] != '\0') {
    screen.setCursor(0, 32);
    screen.print(line3);
  }
  screen.display();
}

void OledDisplay::showClock(const tm* localTime) {
  screen.clearDisplay();
  screen.setTextColor(WHITE);
  screen.setTextSize(2);
  screen.setCursor(0, 0);

  if (localTime != nullptr) {
    screen.printf("%02d:%02d:%02d", localTime->tm_hour, localTime->tm_min,
                  localTime->tm_sec);

    const int weekdayIndex = localTime->tm_wday;
    const char* weekday = weekdayIndex >= 0 && weekdayIndex < 7
                              ? kWeekdays[weekdayIndex]
                              : "---";
    screen.setTextSize(1);
    screen.setCursor(0, 32);
    screen.printf("%04d-%02d-%02d  %s", localTime->tm_year + 1900,
                  localTime->tm_mon + 1, localTime->tm_mday, weekday);
    screen.setCursor(0, 48);
    screen.print("NTP: synchronized");
  } else {
    screen.print("--:--:--");
    screen.setTextSize(1);
    screen.setCursor(0, 32);
    screen.print("Waiting for NTP time");
    screen.setCursor(0, 48);
    screen.print("NTP: connecting");
  }

  screen.display();
}
