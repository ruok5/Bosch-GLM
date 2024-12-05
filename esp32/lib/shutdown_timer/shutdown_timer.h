#ifndef SHUTDOWN_TIMER_H
#define SHUTDOWN_TIMER_H

#include <stdint.h>
#include <esp32-hal.h>
#include <HardwareSerial.h>
class ShutdownTimer {
 public:
  ShutdownTimer(uint32_t timeoutMs) : timeoutMs(timeoutMs), startTime(0), running(false) {
  }

  void start() {
    startTime = millis();
    running   = true;
  }

  void stop() {
    running = false;
  }

  bool isExpired() {
    return running && (millis() - startTime >= timeoutMs);
  }

  void loop() {
    if (isExpired()) {
      Serial.println("No connection within 60 seconds, shutting down.");
      esp_deep_sleep_start();
    }
  }

 private:
  uint32_t timeoutMs;
  uint32_t startTime;
  bool running;
};

#endif // SHUTDOWN_TIMER_H
