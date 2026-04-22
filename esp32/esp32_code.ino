

#include <WiFi.h>
#include <HTTPClient.h>
#include <DHT.h>
#include <ArduinoJson.h>

#define WIFI_SSID "YOUR_WIFI_SSID"
#define WIFI_PASSWORD "YOUR_WIFI_PASSWORD"
#define API_URL "https://cold-storage-ai-dashboard-production.up.railway.app/api/ingest"
#define API_KEY "super-secret-edge-key-2026"
#define POST_INTERVAL_MS 2000

#define DHTPIN 4
#define DHTTYPE DHT11
#define MQ2_PIN 34
#define MQ135_PIN 35
#define LED_PIN 2
#define BUZZER_PIN 15
#define RELAY_PIN 13

DHT dht(DHTPIN, DHTTYPE);
unsigned long lastPostMs = 0;
bool lastDanger = false;

void setActuators(bool danger)
{
    digitalWrite(RELAY_PIN, danger ? HIGH : LOW);
    digitalWrite(LED_PIN, danger ? HIGH : LOW);
    digitalWrite(BUZZER_PIN, danger ? HIGH : LOW);
}

void connectWiFi()
{
    Serial.printf("\nConnecting to %s", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    unsigned long start = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - start < 15000)
    {
        delay(500);
        Serial.print(".");
    }
    if (WiFi.status() == WL_CONNECTED)
    {
        Serial.printf("\nWiFi connected!  IP: %s\n", WiFi.localIP().toString().c_str());
    }
    else
    {
        Serial.println("\n[WARN] WiFi failed. Retrying in next cycle.");
    }
}

void setup()
{
    Serial.begin(115200);
    delay(1000);
    Serial.println("=== Cold Storage AI Sensor Node ===");

    pinMode(LED_PIN, OUTPUT);
    pinMode(BUZZER_PIN, OUTPUT);
    pinMode(RELAY_PIN, OUTPUT);
    setActuators(false); // Safe state on boot

    dht.begin();
    connectWiFi();
}

// ─── MAIN LOOP ───────────────────────────────────────────────────────────────
void loop()
{
    // Reconnect WiFi if dropped
    if (WiFi.status() != WL_CONNECTED)
    {
        connectWiFi();
        return;
    }

    unsigned long now = millis();
    if (now - lastPostMs < POST_INTERVAL_MS)
        return;
    lastPostMs = now;

    // ── 1. Read sensors ───────────────────────────────────────────────────────
    float temp = dht.readTemperature();
    float hum = dht.readHumidity();
    int mq2 = analogRead(MQ2_PIN);
    int mq135 = analogRead(MQ135_PIN);

    if (isnan(temp) || isnan(hum))
    {
        Serial.println("[WARN] DHT read failed — skipping this cycle.");
        return;
    }

    Serial.printf("Sensors → T:%.1f°C  H:%.1f%%  MQ2:%d  MQ135:%d\n",
                  temp, hum, mq2, mq135);

    // ── 2. Build JSON payload ─────────────────────────────────────────────────
    StaticJsonDocument<128> doc;
    doc["temperature"] = temp;
    doc["humidity"] = hum;
    doc["mq2"] = (float)mq2;
    doc["mq135"] = (float)mq135;

    char payload[128];
    serializeJson(doc, payload, sizeof(payload));

    // ── 3. POST to FastAPI ────────────────────────────────────────────────────
    HTTPClient http;
    http.begin(API_URL);
    http.addHeader("Content-Type", "application/json");
    http.addHeader("x-api-key", API_KEY);
    http.setTimeout(5000);

    int httpCode = http.POST(payload);

    if (httpCode == HTTP_CODE_OK)
    {
        // ── 4. Parse prediction from server response ──────────────────────────
        String body = http.getString();
        StaticJsonDocument<256> resp;
        DeserializationError err = deserializeJson(resp, body);

        if (!err)
        {
            const char *status = resp["prediction"]["status"] | "UNKNOWN";
            float prob = resp["prediction"]["probability"] | 0.0f;
            float conf = resp["prediction"]["confidence"] | 0.0f;
            bool danger = strcmp(status, "DANGER") == 0;

            Serial.printf("  → Server: %s (prob=%.3f, conf=%.1f%%)\n",
                          status, prob, conf);

            // Only toggle actuators on state change (reduces relay wear)
            if (danger != lastDanger)
            {
                setActuators(danger);
                lastDanger = danger;
                Serial.printf("  → Actuators: %s\n", danger ? "ACTIVATED" : "CLEARED");
            }
        }
        else
        {
            Serial.printf("[WARN] JSON parse error: %s\n", err.c_str());
        }
    }
    else if (httpCode == 401)
    {
        Serial.println("[ERROR] API key rejected — check API_KEY in config.");
    }
    else
    {
        Serial.printf("[ERROR] HTTP %d: %s\n", httpCode, http.errorToString(httpCode).c_str());
    }

    http.end();
}
