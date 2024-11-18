#include "glm50c.h"
#include "CRC.h"
#include "CRC8.h"
#include "utils.h"

uint8_t getParameter(DistanceMeasurementReference ref,
                     DistanceMeasurementRate rate,
                     DistanceMeasurementTime time,
                     DistanceMeasurementMode mode) {
  uint8_t parameter = 0;

  // Set the bits for the reference type (bits 7-6)
  parameter |= (ref << 6);

  // Set the bits for the measurement rate (bits 4-3)
  parameter |= (rate << 3);

  // Set the bit for the measurement time (bit 2)
  parameter |= (time << 2);

  // Set the bits for the measurement mode (bits 1-0)
  parameter |= (mode);

  return parameter;
}

// const uint8_t measure[] = {0xc0, 0x40, 0b00000001, 0x00};

// const uint8_t laserOn[]  = {0xc0, 0x41, 0x00};
// const uint8_t laserOff[] = {0xc0, 0x42, 0x00};

// const uint8_t buzzerOn[]  = {0xc0, 0x45, 0x00};
// const uint8_t buzzerOff[] = {0xc0, 0x46, 0x00};

// const uint8_t triggerButton[] = {0xc0, 0x56, 0x00};

// 'measure':          b'\xC0\x40\x00\xEE',
// 'laser_on':         b'\xC0\x41\x00\x96',
// 'laser_off':        b'\xC0\x42\x00\x1E',
// 'backlight_on':     b'\xC0\x47\x00\x20',
// 'backlight_off':    b'\xC0\x48\x00\x62'

// const uint8_t laserOn[]  = {0xc0, 0x41, 0x00};
// const uint8_t laserOff[] = {0xc0, 0x42, 0x00};

// const uint8_t buzzerOn[]  = {0xc0, 0x45, 0x00};
// const uint8_t buzzerOff[] = {0xc0, 0x46, 0x00};

// https://github.com/philipptrenz/BOSCH-GLM-rangefinder/issues/10
// #[192, 64, 0, 238], #measure length
// #[192, 65, 0, 150], #laser on
// #[192, 66, 0, 205], #laser off
// #[192, 69, 0, 208], #horn on
// #[192, 70, 0, 139], #horn off
// #[192, 71, 0, 243], #backlight on
// #[192, 72, 0, 177], #backlight off
// #[192, 84, 0, 22], #level in mainframe off
// #[192, 85, 0, ???],#lock angle on device (first time locked, second call releases the measuring)
// #[194, 64, 0, ???],#measure length but with longer received data b'\x00\x044<\x96\x08\x00\x00'

void laserOn(NimBLERemoteCharacteristic* characteristic) {
  uint8_t message[4];
  message[0] = 0xc0;
  message[1] = 0x41;
  message[2] = 0x00;
  message[3] = gencrc(message, 3);
  Serial.println("laserOn");
  hexDump(message, sizeof(message));
  characteristic->writeValue(message, sizeof(message), true);
}

void laserOff(NimBLERemoteCharacteristic* characteristic) {
  uint8_t message[4];
  message[0] = 0xc0;
  message[1] = 0x42;
  message[2] = 0x00;
  message[3] = gencrc(message, 3);
  Serial.println("laserOff");
  hexDump(message, sizeof(message));
  characteristic->writeValue(message, sizeof(message), true);
}

void buzzerOn(NimBLERemoteCharacteristic* characteristic) {
  uint8_t message[4];
  message[0] = 0xc0;
  message[1] = 0x45;
  message[2] = 0x00;
  message[3] = gencrc(message, 3);
  Serial.println("buzzerOn");
  hexDump(message, sizeof(message));
  characteristic->writeValue(message, sizeof(message), true);
}
void buzzerOff(NimBLERemoteCharacteristic* characteristic) {
  uint8_t message[4];
  message[0] = 0xc0;
  message[1] = 0x46;
  message[2] = 0x00;
  message[3] = gencrc(message, 3);
  Serial.println("buzzerOff");
  hexDump(message, sizeof(message));
  characteristic->writeValue(message, sizeof(message), true);
}

void sendDistanceMeasurement(NimBLERemoteCharacteristic* characteristic,
                             DistanceMeasurementReference ref,
                             DistanceMeasurementRate rate,
                             DistanceMeasurementTime time,
                             DistanceMeasurementMode mode) {
  uint8_t message[4];
  message[0] = 0x40;
  message[1] = getParameter(ref, rate, time, mode);
  message[2] = 0x00;
  message[3] = gencrc(message, 3);
  Serial.println("measure");
  hexDump(message, sizeof(message));
  characteristic->writeValue(message, sizeof(message), true);
}

uint8_t gencrc(const uint8_t* data, size_t len) {
  return calcCRC8(data, len, 0xa6, 0xaa);
}
