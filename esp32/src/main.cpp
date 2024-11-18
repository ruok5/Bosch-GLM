#include <Arduino.h>
#include <BleKeyboard.h>
#include <NimBLEDevice.h>
#include "Button2.h"
#include "CRC.h"
#include "CRC8.h"
#include "glm50c.h"
#include "utils.h"
union float2bytes {
  float f;
  int32_t i;
  char b[sizeof(float)];
};

Button2 button1(35);
NimBLEScan* pBLEScan;
BleKeyboard bleKeyboard("Bosch keyboard");
NimBLERemoteCharacteristic* characteristic = NULL;
TaskHandle_t xHandle                       = NULL;

NimBLEUUID svcUUID  = NimBLEUUID("02a6c0d0-0451-4000-b000-fb3210111989");
NimBLEUUID charUUID = NimBLEUUID("02a6c0d1-0451-4000-b000-fb3210111989");

void vTaskCode(void* pvParameters) {
  Serial.println("task started!");
  for (;;) {
    button1.loop();
    delay(10);
  }
  Serial.println("task ended!");
}

class MyAdvertisedDeviceCallbacks : public NimBLEAdvertisedDeviceCallbacks {
  void onResult(NimBLEAdvertisedDevice* advertisedDevice) override {
    // Print the address of the found device
    // Serial.print("Found device: ");
    Serial.println(advertisedDevice->getAddress().toString().c_str());

    // Get the device name from the advertisement data
    std::string deviceName = advertisedDevice->getName();

    if (deviceName.length() > 0) {
      // Serial.print("Device Name: ");
      // Serial.println(deviceName.c_str());
    } else {
      // Serial.println("No name available for this device.");
    }
  }
};

void dumpServices(NimBLEClient* pClient) {
  std::vector<NimBLERemoteService*>* m_servicesVector = pClient->getServices(true);

  Serial.println("Printing services:");
  for (NimBLERemoteService* eachSvc : *m_servicesVector) {
    std::vector<NimBLERemoteCharacteristic*>* characteristics = eachSvc->getCharacteristics(true);
    Serial.println(eachSvc->toString().c_str());
  }
}

// uint8_t gencrc(const uint8_t* data, size_t len) {
//   return calcCRC8(data, len, 0xa6, 0xaa);
//   // uint8_t crc = 0xaa;  // 170 - initial value
//   // size_t i, j;
//   // for (i = 0; i < len; i++) {
//   //   crc ^= data[i];
//   //   for (j = 0; j < 8; j++) {
//   //     if ((crc & 0x80) != 0)
//   //       crc = (uint8_t)((crc << 1) ^ 0xa6);  // 166
//   //     else
//   //       crc <<= 1;
//   //   }
//   // }
//   // return crc;
// }

size_t addCrc(const uint8_t* input, size_t len, uint8_t* out) {
  memcpy(out, input, len);
  out[len] = gencrc(input, len);
  return len + 1;
}

// void writeCommand(const uint8_t* cmd, size_t len) {
//   uint8_t buf[16];
//   size_t newLen = addCrc(cmd, len, buf);
//   hexDump(buf, newLen);
//   characteristic->writeValue(buf, newLen, false);
// }

// void laserOnCommand(uint8_t* out, Reference ref, MeasurementRate rate, MeasurementTime time, MeasurementMode mode) {
//   const uint8_t measure = {} memcpy(out, measure, sizeof(measure));
//   out[len]              = gencrc(input, len);
// }

void button1Handler(Button2& btn) {
  Serial.println("Button1 - laser on");
  if (characteristic) {
    laserOn(characteristic);
    delay(10);

    buzzerOn(characteristic);
    delay(50);
    buzzerOff(characteristic);

    delay(50);

    buzzerOn(characteristic);
    delay(50);
    buzzerOff(characteristic);

    delay(1000);

    buzzerOn(characteristic);
    delay(50);
    sendDistanceMeasurement(characteristic, DistanceMeasurementReference::REAR);
    buzzerOff(characteristic);

    laserOff(characteristic);
  }
}

void setup() {
  // button1.begin(35);
  // button2.begin(0);

  button1.setClickHandler(button1Handler);
  Serial.begin(115200);

  Serial.println("ESP32 BLE");
  bleKeyboard.begin();
  pBLEScan = NimBLEDevice::getScan();
  // pBLEScan->setAdvertisedDeviceCallbacks(new MyAdvertisedDeviceCallbacks());

  pBLEScan->setActiveScan(true);

  if (xTaskCreate(vTaskCode, "Button", 8096, NULL, tskIDLE_PRIORITY, &xHandle) != pdPASS) {
    Serial.println("Unable to create task");
  }
}

void loop() {
  NimBLEScanResults foundDevices = pBLEScan->start(1);

  // Serial.printf("Found %d devices\r\n", foundDevices.getCount());

  for (NimBLEAdvertisedDevice* advertisedDevice : foundDevices) {
    if (advertisedDevice->isAdvertisingService(svcUUID)) {
      const char* deviceAddress = advertisedDevice->getAddress().toString().c_str();
      NimBLEClient* pClient     = NimBLEDevice::createClient();
      Serial.printf("Connecting to device address=%s, name=%s.\n", deviceAddress, advertisedDevice->getName().c_str());
      if (pClient->connect(advertisedDevice->getAddress())) {
        Serial.printf("Connected to device address=%s, name=%s.\n", deviceAddress, advertisedDevice->getName().c_str());

        dumpServices(pClient);

        NimBLERemoteService* svc = pClient->getService(svcUUID);

        if (svc) {
          characteristic = svc->getCharacteristic(charUUID);
          if (characteristic) {
            auto handler = [=](NimBLERemoteCharacteristic* pBLERemoteCharacteristic,
                               uint8_t* pData,
                               size_t length,
                               bool isNotify) {
              // Serial.println(String("Got data "));
              hexDump(pData, length);

              // starts with C0 55 10 06
              if (pData[0] == 0xc0 && pData[1] == 0x55 && pData[2] == 0x10 && pData[3] == 0x06) {
                float2bytes buf;
                buf.b[0] = pData[7];
                buf.b[1] = pData[8];
                buf.b[2] = pData[9];
                buf.b[3] = pData[10];
                Serial.printf("Got length, %.3fm\r\n", buf.f);
                if (bleKeyboard.isConnected()) {
                  bleKeyboard.printf("%.3f", buf.f);
                  bleKeyboard.write(KEY_RETURN);
                }
              } else if (length > 5) {
                float2bytes buf;
                buf.b[0]             = pData[2];
                buf.b[1]             = pData[3];
                buf.b[2]             = pData[4];
                buf.b[3]             = pData[5];
                float offsetFromBack = 0.12;
                Serial.printf("Got length, %.3fm\r\n", buf.i * 0.00005 + offsetFromBack);
                if (bleKeyboard.isConnected()) {
                  bleKeyboard.printf("%.3f", buf.f);
                  bleKeyboard.write(KEY_RETURN);
                }
              }
            };

            bool subscribed = characteristic->subscribe(false, handler);

            if (subscribed) {
              Serial.println("Subscribed");
              // enable logging/auto-sync
              characteristic->writeValue({0xc0, 0x55, 0x02, 0x01, 0x00, 0x1a}, false);
            } else {
              Serial.println("Unable to subscribe");
            }
          } else {
            Serial.println(String("Unable to find characteristic with UUID ") + charUUID.toString().c_str());
          }
        } else {
          Serial.println(String("Unable to find service with UUID ") + svcUUID.toString().c_str());
        }

      } else {
        Serial.println(String("Unable to connect to device ") + advertisedDevice->getAddress().toString().c_str());
      }

    } else {
      // Serial.println(String("Skipping device ") +
      //                advertisedDevice->getAddress().toString().c_str());
    }
  }
}
