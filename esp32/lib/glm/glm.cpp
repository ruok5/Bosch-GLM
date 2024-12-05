#include <glm.h>
#include <utils.h>

void glm_laserOff(NimBLERemoteCharacteristic* remoteCharacteristic) {
  if (remoteCharacteristic) {
    uint8_t message[4] = {0xc0, 0x42, 0x00, 0x1e};
    Serial.println("laserOff");
    hexDump(message, sizeof(message));
    remoteCharacteristic->writeValue(message, sizeof(message), true);
  }
}

void glm_buttonPress(NimBLERemoteCharacteristic* remoteCharacteristic) {
  if (remoteCharacteristic) {
    uint8_t message[5];
    message[0] = 0xc0;
    message[1] = 0x56;  // command - 86 "Do Remote Trigger Button"
    message[2] = 0x01;  // the button number
    message[3] = 0x00;  // payload size
    message[4] = 0x1e;  // checksum
    Serial.println("buttonPress");
    hexDump(message, sizeof(message));
    remoteCharacteristic->writeValue(message, sizeof(message), true);
  }
}
