#include "prefs.h"
#include <EEPROM.h>

uint8_t calculateChecksum(const uint8_t* data, size_t length) {
  uint8_t checksum = 0;
  for (size_t i = 0; i < length; ++i) {
    checksum ^= data[i];
  }
  return checksum;
}

void clearPrefsInEEPROM() {
  if (EEPROM.begin(64) == 0) {
    Serial.println("Failed to initialise EEPROM");
    return;
  }

  prefs_t p;
  uint8_t* data     = (uint8_t*)&p;
  size_t structSize = sizeof(prefs_t);

  for (size_t i = 0; i < structSize; ++i) {
    EEPROM.write(i, data[i]);
  }

  uint8_t badChecksum = calculateChecksum(data, structSize) - 1;
  EEPROM.write(structSize, badChecksum);

  EEPROM.commit();
  EEPROM.end();

  Serial.println("Cleared preferences from EEPROM");
}

void writePrefsToEEPROM(prefs_t& p) {
  if (EEPROM.begin(64) == 0) {
    Serial.println("Failed to initialise EEPROM");
    return;
  }

  uint8_t* data     = (uint8_t*)&p;
  size_t structSize = sizeof(prefs_t);

  for (size_t i = 0; i < structSize; ++i) {
    EEPROM.write(i, data[i]);
  }

  uint8_t checksum = calculateChecksum(data, structSize);
  EEPROM.write(structSize, checksum);
  EEPROM.commit();
  EEPROM.end();

  dumpPrefs(p);
  Serial.println("Wrote preferences to EEPROM");
}

bool readPrefsFromEEPROM(prefs_t& settings) {
  if (EEPROM.begin(64) == 0) {
    Serial.println("Failed to initialise EEPROM");
    return false;
  }
  uint8_t* data     = (uint8_t*)&settings;
  size_t structSize = sizeof(settings);

  // Read structure data from EEPROM
  for (size_t i = 0; i < structSize; ++i) {
    data[i] = EEPROM.read(i);
  }

  // Read checksum from EEPROM (after structure)
  uint8_t storedChecksum = EEPROM.read(structSize);
  EEPROM.end();

  // Calculate checksum of the read data
  uint8_t calculatedChecksum = calculateChecksum(data, structSize);

  // Compare checksums to verify data integrity
  if (storedChecksum == calculatedChecksum) {
    dumpPrefs(settings);
    return true;  // Data is valid
  } else {
    return false;  // Data is invalid (corrupted)
  }
}

void dumpPrefs(prefs_t& p) {
  Serial.printf("Neutral position: %.1f\n", p.neutralPositionMag);
  Serial.printf("Trigger position: %.1f\n", p.triggerPositionMag);
}
