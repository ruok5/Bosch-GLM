#include <Adafruit_LIS3MDL.h>
#include <TFT_eSPI.h>

#ifndef UTILS_H
#  define UTILS_H

const int SAMPLE_RATE_MS = 250;

enum TriggerStatus { UNKNOWN, CLOSE_TO_TRIGGER, CLOSE_TO_NEUTRAL, IN_BETWEEN, OUTSIDE_RANGE };

const char* triggerStatusToString(TriggerStatus status);
void hexDump(const void* object, long size);

void displayMessageWithCountdown(TFT_eSPI& tft, const char* msgLine1, const char* msgLine2, uint8_t timeoutSeconds);

void drawRecordIcon(TFT_eSPI& tft);

float captureMagValues(TFT_eSPI& tft, Adafruit_LIS3MDL& lis3mdl);

void drawBTIcon(TFT_eSPI& tft);
void drawResetIcon(TFT_eSPI& tft);

float measureMag(TFT_eSPI& tft, Adafruit_LIS3MDL& lis3mdl);

TriggerStatus getTriggerStatus(TFT_eSPI& tft, float sensorReading, float neutralPoint, float triggerPoint);
#endif  // UTILS_H
