#pragma once

#include <time.h>

class OledDisplay {
 public:
  bool begin();
  void showStatus(const char* line1, const char* line2 = "",
                  const char* line3 = "");
  void showClock(const tm* localTime, const char* statusLine);
};
