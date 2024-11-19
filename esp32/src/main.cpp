#include <Arduino.h>
#include <BleKeyboard.h>
#include <NimBLEDevice.h>
#include "Button2.h"
#include "utils.h"

#define SCAN_DURATION 5  // seconds
Button2 button1(35);
BleKeyboard bleKeyboard("Bosch keyboard");
NimBLEAdvertisedDevice* advertisedDevice         = nullptr;
bool doConnect                                   = false;
NimBLERemoteCharacteristic* remoteCharacteristic = nullptr;
NimBLEClient* client                             = nullptr;

NimBLEUUID serviceUUID("02a6c0d0-0451-4000-b000-fb3210111989");
NimBLEUUID characteristicUUID("02a6c0d1-0451-4000-b000-fb3210111989");

void onNotificationReceived(NimBLERemoteCharacteristic* characteristic, uint8_t* data, size_t length, bool isNotify) {
  hexDump(data, length);

  // starts with C0 55 10
  if (data[0] == 0xc0 && data[1] == 0x55 && data[2] == 0x10) {
    union {
      float f;
      uint8_t b[sizeof(float)];
    } buf;

    buf.b[0] = data[7];
    buf.b[1] = data[8];
    buf.b[2] = data[9];
    buf.b[3] = data[10];

    Serial.printf("Got length %.3fm\r\n", buf.f);
    if (buf.f != 0 && bleKeyboard.isConnected()) {
      bleKeyboard.printf("%.3f", buf.f);
      bleKeyboard.write(KEY_RETURN);
    }
  }
}

class ClientCallbacks : public NimBLEClientCallbacks {
  void onConnect(NimBLEClient* client) override {
    Serial.println("Connected to the device.");

    NimBLERemoteService* remoteService = client->getService(serviceUUID);
    if (remoteService != nullptr) {
      remoteCharacteristic = remoteService->getCharacteristic(characteristicUUID);
      if (remoteCharacteristic != nullptr) {
        Serial.println("Characteristic found and stored.");

        if (remoteCharacteristic->canIndicate()) {
          remoteCharacteristic->subscribe(false, onNotificationReceived);
          Serial.println("Subscribed to characteristic notifications.");
        } else {
          Serial.println("Characteristic does not support notifications.");
        }
      } else {
        Serial.println("Characteristic not found!");
      }
    } else {
      Serial.println("Service not found!");
    }
  }

  void onDisconnect(NimBLEClient* client) override {
    Serial.println("Disconnected from device.");
    client = nullptr;
    doConnect = true;

    NimBLEDevice::getScan()->start(SCAN_DURATION);
  }
};
class AdvertisedDeviceCallbacks : public NimBLEAdvertisedDeviceCallbacks {
  void onResult(NimBLEAdvertisedDevice* device) override {
    if (device->haveServiceUUID() && device->isAdvertisingService(serviceUUID)) {
      Serial.print("Found device with matching service UUID: ");
      Serial.println(device->toString().c_str());

      NimBLEDevice::getScan()->stop();

      advertisedDevice = device;
      doConnect        = true;
    }
  }
};

void button1Handler(Button2& btn) {
  Serial.println("Button1 - laser on");
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

void setup() {
  Serial.begin(115200);

  Serial.println("ESP32 BLE");
  bleKeyboard.begin();
  button1.setClickHandler(button1Handler);

  // NimBLEDevice::setPower(ESP_PWR_LVL_P9);  // Max power for long range
  NimBLEScan* pScan = NimBLEDevice::getScan();
  pScan->setActiveScan(true);
  pScan->setInterval(100);
  pScan->setWindow(99);
  pScan->setAdvertisedDeviceCallbacks(new AdvertisedDeviceCallbacks());
  pScan->start(SCAN_DURATION);
}

bool connectToBoschGLM() {
  client = NimBLEDevice::createClient();
  client->setClientCallbacks(new ClientCallbacks());
  Serial.println("Connecting to client");
  if (client->connect(advertisedDevice)) {
    Serial.println("Connected to the device.");
    return true;
  } else {
    Serial.println("Failed to connect, deleting client");
    NimBLEDevice::deleteClient(client);
    return false;
  }
}
void loop() {
  // wait here for scan to complete, and for a connection to be established

  while (!doConnect) {
    if (client && client->isConnected()) {
      button1.loop();
    }
    delay(10);
  }

  doConnect = false;

  if (connectToBoschGLM()) {
    Serial.println("Success! we should now be getting notifications, scanning for more!");
  } else {
    Serial.println("Failed to connect, starting scan");
  }

  NimBLEDevice::getScan()->start(SCAN_DURATION);
  delay(10);
}
