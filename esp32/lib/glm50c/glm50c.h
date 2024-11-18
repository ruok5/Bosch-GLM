#ifndef GLM50C_H
#define GLM50C_H
#include "NimBLERemoteCharacteristic.h"

// Enum for the reference type (Bit[7..6])
enum DistanceMeasurementReference {
  FRONT  = 0,  // 00
  TRIPOD = 1,  // 01
  REAR   = 2,  // 10
  PIN    = 3   // 11
};

// Enum for the measurement rate (Bit[4..3])
enum DistanceMeasurementRate {
  RATE_5HZ  = 0,  // 00
  RATE_10HZ = 1,  // 01
  RATE_20HZ = 2,  // 10
  RATE_30HZ = 3   // 11
};

// Enum for the measurement time (Bit[2])
enum DistanceMeasurementTime {
  AUTOMATIC = 0,  // 0
  FIXED     = 1   // 1
};

// Enum for the measurement mode (Bits[1..0])
enum DistanceMeasurementMode {
  SINGLE          = 0,  // 00
  CONTINUOUS      = 1,  // 01
  STOP_CONTINUOUS = 2   // 10
};

void laserOn(NimBLERemoteCharacteristic* characteristic);
void laserOff(NimBLERemoteCharacteristic* characteristic);

void buzzerOn(NimBLERemoteCharacteristic* characteristic);
void buzzerOff(NimBLERemoteCharacteristic* characteristic);

void sendDistanceMeasurement(NimBLERemoteCharacteristic* characteristic,
                             DistanceMeasurementReference ref = DistanceMeasurementReference::FRONT,
                             DistanceMeasurementRate rate     = DistanceMeasurementRate::RATE_5HZ,
                             DistanceMeasurementTime time     = DistanceMeasurementTime::AUTOMATIC,
                             DistanceMeasurementMode mode     = DistanceMeasurementMode::SINGLE);
uint8_t gencrc(const uint8_t* data, size_t len);

#endif  // GLM50C_H
