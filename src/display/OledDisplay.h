#pragma once

#include <Arduino.h>

#include <stdio.h>
#include <time.h>

#include <string>

class OledDisplay {
 public:
  bool begin();
  void showStatus(const char* line1, const char* line2 = "",
                  const char* line3 = "");
  void showStatus(const String& line1);
  void showStatus(const String& line1, const String& line2,
                  const String& line3 = "");
  // Printf-style single line. String args work directly:
  //   showStatusF("%d", value);
  //   showStatusF("%s", name);  // const char*, char*, String, std::string
  // A single String with no args is shown as-is, never used as a format,
  // so a "%" in the text is harmless. The const char* overload below does
  // the same for literals. Calls WITH args use snprintf; mismatched
  // format/args are UB, as with snprintf. For multiple formatted lines,
  // format into buffers first, then showStatus(). Output longer than
  // kStatusBufSize is truncated.
  static constexpr size_t kStatusBufSize = 64;
  template <typename... Args>
  void showStatusF(const char* format, const Args&... args) {
    char buffer[kStatusBufSize];
    snprintf(buffer, sizeof(buffer), format, toCStr(args)...);
    showStatus(buffer);
  }
  template <typename... Args>
  void showStatusF(const String& format, const Args&... args) {
    showStatusF(format.c_str(), args...);
  }
  // Plain single line; shadows the template when called with no args.
  void showStatusF(const String& line);
  // Plain C string with no args; never treated as a format string, so
  // showStatusF("50% done") displays literally instead of formatting.
  void showStatusF(const char* line);
  void showClock(const tm* localTime, const char* statusLine);

 private:
  // Implementation detail: unwrap string-like args for snprintf's %s.
  static const char* toCStr(const char* s) { return s; }
  static const char* toCStr(char* s) { return s; }
  static const char* toCStr(const String& s) { return s.c_str(); }
  static const char* toCStr(const std::string& s) { return s.c_str(); }
  template <typename T>
  static const T& toCStr(const T& value) {
    return value;
  }
};
