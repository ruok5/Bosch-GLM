// 16:16:46.918 > I2C device found at address 0x1E
// 16:16:46.926 > I2C device found at address 0x5D
// 16:16:46.929 > I2C device found at address 0x6B
#include <Adafruit_LIS3MDL.h>
#include <Adafruit_Sensor.h>
#include <Arduino.h>
#include <BleKeyboard.h>
#include <Button2.h>
#include <NimBLEDevice.h>
#include <SPI.h>
#include <TFT_eSPI.h>  // Graphics and font library for ST7735 driver chip
#include <Wire.h>
#include <glm.h>
#include "prefs.h"
#include "shutdown_timer.h"
#include "utils.h"

#define TFT_GREY      0xBDF7
#define SCAN_DURATION 5  // seconds

TFT_eSPI tft = TFT_eSPI();  // Invoke library, pins defined in User_Setup.h
ShutdownTimer shutdownTimer(120000);

Button2 button0(0);
Button2 button1(35);

BleKeyboard bleKeyboard("Bosch keyboard");
NimBLEAdvertisedDevice* advertisedDevice         = nullptr;
bool doConnect                                   = false;
NimBLERemoteCharacteristic* remoteCharacteristic = nullptr;
NimBLEClient* client                             = nullptr;

NimBLEUUID serviceUUID("02a6c0d0-0451-4000-b000-fb3210111989");
NimBLEUUID characteristicUUID("02a6c0d1-0451-4000-b000-fb3210111989");

Adafruit_LIS3MDL lis3mdl;

TickType_t xLastWakeTime;

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
    shutdownTimer.stop();  // Stop the shutdown timer on successful connection

    NimBLERemoteService* remoteService = client->getService(serviceUUID);
    if (remoteService != nullptr) {
      remoteCharacteristic = remoteService->getCharacteristic(characteristicUUID);
      if (remoteCharacteristic != nullptr) {
        Serial.println("Characteristic found and stored.");

        if (remoteCharacteristic->canIndicate()) {
          bool subscribed = remoteCharacteristic->subscribe(false, onNotificationReceived);
          Serial.println("Subscribed to characteristic notifications.");

          if (subscribed) {
            remoteCharacteristic->writeValue({0xc0, 0x55, 0x02, 0x01, 0x00, 0x1a}, false);
          }
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

  void onDisconnect(NimBLEClient* _client) override {
    Serial.println("Disconnected from device.");
    client    = nullptr;
    doConnect = true;
    tft.fillScreen(TFT_BLACK);

    NimBLEDevice::getScan()->start(SCAN_DURATION);
    shutdownTimer.start();  // Restart the shutdown timer on disconnection
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
  glm_buttonPress(remoteCharacteristic);
}

bool prefsLoaded = false;
prefs_t preferences;

void setup() {
  Serial.begin(115200);
  prefsLoaded = readPrefsFromEEPROM(preferences);
  if (!prefsLoaded) {
    Serial.println("Failed to load preferences from EEPROM");
  }
  shutdownTimer.start();

  tft.init();
  tft.setRotation(0);
  tft.fillScreen(TFT_BLACK);
  tft.setTextSize(2);
  tft.setFreeFont(&FreeMono18pt7b);

  Serial.println("ESP32 BLE");

  if (!lis3mdl.begin_I2C(0x1e)) {
    Serial.println("Failed to find LIS3MDL chip");
    while (1) {
      delay(10);
    }
  }

  lis3mdl.setPerformanceMode(LIS3MDL_MEDIUMMODE);
  lis3mdl.setDataRate(lis3mdl_dataRate_t::LIS3MDL_DATARATE_5_HZ);
  lis3mdl.setOperationMode(lis3mdl_operationmode_t::LIS3MDL_CONTINUOUSMODE);
  lis3mdl.setRange(lis3mdl_range_t::LIS3MDL_RANGE_16_GAUSS);

  bleKeyboard.begin();
  button0.setTapHandler([](Button2& btn) {
    if (button1.isPressed()) {
      tft.fillScreen(TFT_BLACK);
      drawResetIcon(tft);

      clearPrefsInEEPROM();
      delay(1000);
      ESP.restart();
    }
  });

  button1.setTapHandler([](Button2& btn) {
    if (button0.isPressed()) {
      tft.fillScreen(TFT_BLACK);
      drawResetIcon(tft);

      clearPrefsInEEPROM();
      delay(1000);
      ESP.restart();
    }
  });

  NimBLEScan* pScan = NimBLEDevice::getScan();
  pScan->setActiveScan(true);
  pScan->setInterval(100);
  pScan->setWindow(99);
  pScan->setAdvertisedDeviceCallbacks(new AdvertisedDeviceCallbacks());
  pScan->start(SCAN_DURATION);

  xLastWakeTime = xTaskGetTickCount();

  xTaskCreate(
      [](void* pvParameters) {
        Serial.println("Button task started");
        while (1) {
          button0.loop();
          button1.loop();
          shutdownTimer.loop();
          vTaskDelay(10);
        }
      },
      "buttonTask",
      1024 * 8,
      NULL,
      1,
      NULL);
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

void initializePreferencesWorkflow(TFT_eSPI& tft, prefs_t& prefs) {
  Serial.println("Initializing preferences workflow");

  tft.fillScreen(TFT_BLACK);
  tft.setTextColor(TFT_WHITE, TFT_BLACK, false);
  displayMessageWithCountdown(tft, "Neutral", "Position", 5);

  drawRecordIcon(tft);
  tft.setTextColor(TFT_WHITE, TFT_BLACK, true);
  prefs.neutralPositionMag = captureMagValues(tft, lis3mdl);
  tft.fillScreen(TFT_GREY);
  tft.setTextColor(TFT_WHITE, TFT_GREY, false);
  displayMessageWithCountdown(tft, "Position", "Captured", 0);
  delay(3000);

  Serial.printf("Neutral position set to: %.1f\n", prefs.neutralPositionMag);

  tft.fillScreen(TFT_BLACK);
  tft.setTextColor(TFT_WHITE, TFT_BLACK, false);
  displayMessageWithCountdown(tft, "Trigger", "Position", 5);

  drawRecordIcon(tft);

  tft.setTextColor(TFT_WHITE, TFT_BLACK, true);
  prefs.triggerPositionMag = captureMagValues(tft, lis3mdl);
  tft.fillScreen(TFT_GREY);
  tft.setTextColor(TFT_WHITE, TFT_GREY, false);
  displayMessageWithCountdown(tft, "Position", "Captured", 0);
  delay(3000);

  writePrefsToEEPROM(prefs);
  prefsLoaded = true;

  tft.fillScreen(TFT_BLACK);
  Serial.printf("Trigger position set to: %.1f\n", prefs.triggerPositionMag);
}
TriggerStatus lastStatus              = TriggerStatus::UNKNOWN;
unsigned long closeToTriggerStartTime = 0;
bool messagePrinted                   = false;
bool laserIsOn                        = false;

void loop() {
  if (!prefsLoaded) {
    initializePreferencesWorkflow(tft, preferences);
  }

  if (prefsLoaded) {
    while (!doConnect) {
      if (client && client->isConnected()) {
        drawBTIcon(tft);
        float mag = measureMag(tft, lis3mdl);

        TriggerStatus currentStatus =
            getTriggerStatus(tft, mag, preferences.neutralPositionMag, preferences.triggerPositionMag);

        if (currentStatus != lastStatus) {
          Serial.printf("Transitioned from %s to %s\n",
                        triggerStatusToString(lastStatus),
                        triggerStatusToString(currentStatus));
        }

        if (lastStatus == TriggerStatus::CLOSE_TO_NEUTRAL && currentStatus == TriggerStatus::IN_BETWEEN) {
          glm_laserOff(remoteCharacteristic);
          laserIsOn = false;
        }

        if (lastStatus == TriggerStatus::IN_BETWEEN && currentStatus == TriggerStatus::CLOSE_TO_TRIGGER) {
          Serial.println("Transitioned to CLOSE_TO_TRIGGER");
          closeToTriggerStartTime = millis();
          messagePrinted          = false;
          if (!laserIsOn) {
            glm_buttonPress(remoteCharacteristic);
            laserIsOn = true;
          }
        }

        if (currentStatus == TriggerStatus::CLOSE_TO_TRIGGER) {
          if (!messagePrinted && (millis() - closeToTriggerStartTime > 2000)) {
            glm_buttonPress(remoteCharacteristic);
            laserIsOn = false;
            Serial.println("Stayed in CLOSE_TO_TRIGGER for over 2000ms");
            messagePrinted = true;
          }
        }

        if (currentStatus == TriggerStatus::CLOSE_TO_NEUTRAL || currentStatus == TriggerStatus::OUTSIDE_RANGE) {
          if (laserIsOn) {
            glm_laserOff(remoteCharacteristic);
            laserIsOn = false;
          }
        }

        lastStatus = currentStatus;
      }
    }

    doConnect = false;

    if (connectToBoschGLM()) {
      Serial.println("Success! we should now be getting notifications, scanning for more!");
    } else {
      Serial.println("Failed to connect, starting scan");
    }

    NimBLEDevice::getScan()->start(SCAN_DURATION);
    vTaskDelayUntil(&xLastWakeTime, pdMS_TO_TICKS(SAMPLE_RATE_MS));
  }
}
