/*
  Smart IoT Environmental Monitoring - ESP32 Edge Node Example
  
  Reads DHT22 (Temperature & Humidity) and an analog Air Quality Sensor (e.g., MQ-135)
  and streams telemetry to the Smart IoT Ingest API endpoint (/api/ingest).

  Hardware Setup:
  - ESP32 Development Board (e.g. ESP32-WROOM-32)
  - DHT22 (AM2302) Data pin connected to GPIO 4 (with 10k pull-up resistor to 3.3V)
  - MQ-135 / Analog AQI sensor Analog Out connected to ADC GPIO 34 (A0)
  
  Required Arduino Libraries:
  - DHT sensor library by Adafruit
  - Adafruit Unified Sensor
*/

#include <WiFi.h>
#include <HTTPClient.h>
#include <time.h>
#include <DHT.h>

// --- Configuration ---
const char* WIFI_SSID     = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

// Server Ingest URL (Replace with your server host IP / domain)
const char* SERVER_INGEST_URL = "http://192.168.1.100:5000/api/ingest";

// Unique identifier for this hardware node
const char* NODE_ID = "node-esp32-01";

// Sensor Pins
#define DHTPIN 4
#define DHTTYPE DHT22
#define AQI_PIN 34  // Analog ADC input

// Telemetry parameters
const unsigned long SEND_INTERVAL_MS = 2000; // 0.5 Hz (matches typical sensor limits)
unsigned long lastSendTime = 0;
uint32_t sequenceNumber = 0;

// NTP Server settings for epoch timestamps (ts)
const char* NTP_SERVER = "pool.ntp.org";
const long  GMT_OFFSET_SEC = 0;
const int   DAYLIGHT_OFFSET_SEC = 0;

DHT dht(DHTPIN, DHTTYPE);

// Get current Unix timestamp (seconds with fraction)
double getEpochTime() {
  time_t now;
  struct tm timeinfo;
  if (!getLocalTime(&timeinfo)) {
    // Fallback: estimate epoch using millis if NTP sync is still settling
    return 1700000000.0 + (millis() / 1000.0);
  }
  time(&now);
  return (double)now + ((millis() % 1000) / 1000.0);
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n[IoT Node] Initializing sensors and network...");

  // Initialize DHT sensor
  dht.begin();
  pinMode(AQI_PIN, INPUT);

  // Connect to Wi-Fi
  Serial.printf("[IoT Node] Connecting to Wi-Fi '%s'...\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\n[IoT Node] Connected! Local IP: %s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("\n[IoT Node] Wi-Fi connection timed out. Will retry during loop.");
  }

  // Initialize NTP time sync
  configTime(GMT_OFFSET_SEC, DAYLIGHT_OFFSET_SEC, NTP_SERVER);
  Serial.println("[IoT Node] NTP time sync configured.");
}

void loop() {
  // Check Wi-Fi reconnection if lost
  if (WiFi.status() != WL_CONNECTED) {
    WiFi.reconnect();
    delay(1000);
    return;
  }

  unsigned long currentMillis = millis();
  if (currentMillis - lastSendTime >= SEND_INTERVAL_MS) {
    lastSendTime = currentMillis;

    // Read Sensors
    float temperature = dht.readTemperature(); // Celsius
    float humidity = dht.readHumidity();       // Percentage

    // Analog AQI reading: ADC 0..4095 -> map to index 0..500
    int rawAqi = analogRead(AQI_PIN);
    float aqi = (float)map(rawAqi, 0, 4095, 0, 500);

    // Validate sensor readings
    bool tempValid = !isnan(temperature);
    bool humValid = !isnan(humidity);
    bool aqiValid = !isnan(aqi);

    double ts = getEpochTime();
    sequenceNumber++;

    // Format JSON payload according to SPEC.md data contract
    // Required fields: node_id (str), ts (float), seq (int), temperature, humidity, aqi, status
    char payload[256];
    char tempBuf[16], humBuf[16], aqiBuf[16];

    if (tempValid) snprintf(tempBuf, sizeof(tempBuf), "%.2f", temperature);
    else snprintf(tempBuf, sizeof(tempBuf), "null");

    if (humValid) snprintf(humBuf, sizeof(humBuf), "%.2f", humidity);
    else snprintf(humBuf, sizeof(humBuf), "null");

    if (aqiValid) snprintf(aqiBuf, sizeof(aqiBuf), "%.1f", aqi);
    else snprintf(aqiBuf, sizeof(aqiBuf), "null");

    snprintf(payload, sizeof(payload),
      "{\"node_id\":\"%s\",\"ts\":%.3f,\"seq\":%u,\"temperature\":%s,\"humidity\":%s,\"aqi\":%s,\"status\":\"ok\"}",
      NODE_ID, ts, sequenceNumber, tempBuf, humBuf, aqiBuf
    );

    Serial.printf("[POST -> Ingest] %s\n", payload);

    // Transmit to Smart IoT Ingest API
    HTTPClient http;
    http.begin(SERVER_INGEST_URL);
    http.addHeader("Content-Type", "application/json");
    http.setTimeout(1500); // 1.5s timeout

    int httpResponseCode = http.POST((uint8_t*)payload, strlen(payload));

    if (httpResponseCode > 0) {
      Serial.printf("[HTTP Response] Code: %d\n", httpResponseCode);
      if (httpResponseCode == 202) {
        // 202 Accepted into priority pipeline
      } else if (httpResponseCode == 429) {
        Serial.println("[Warning] Ingest queue full (Rate-limited / dropped by backpressure)");
      } else if (httpResponseCode >= 400) {
        String response = http.getString();
        Serial.printf("[Error] Server returned: %s\n", response.c_str());
      }
    } else {
      Serial.printf("[HTTP Error] Connection failed: %s\n", http.errorToString(httpResponseCode).c_str());
    }

    http.end();
  }
}
