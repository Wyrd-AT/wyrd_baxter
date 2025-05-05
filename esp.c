#include <BLEDevice.h>
#include <WiFi.h>
#include <ArduinoJson.h>
#include "lwip/sockets.h"

// ============ CONFIG Wi-Fi e Servidor ============
const char* SSID        = "MVISIA_2.4GHz";
const char* PASSWORD    = "mvisia2020";
const char* SERVER_IP   = "10.0.0.149";
const uint16_t SERVER_PORT = 9500;

// ============ CONFIG NTP ============
const char* NTP_SERVER        = "pool.ntp.org";
const long   GMT_OFFSET_SEC   = -3 * 3600;  // Brasília
const int    DAYLIGHT_OFFSET_SEC = 0;

// ============ CONFIG BLE ============
#define RSSI_THRESHOLD     -64
#define INERCIA_CHEGADA    20000
#define TEMPO_SAIDA        10000
#define RSSI_HISTORY_SIZE     5
#define BEACON_SCAN_TIME      4   // segundos de scan BLE
#define POLL_INTERVAL         5   // em segundos

// ============ Estrutura de estado por cama ============
struct CamaBLE {
  const char* mac;
  const char* nome;
  int         rssiHistory[RSSI_HISTORY_SIZE];
  uint8_t     index          = 0;
  bool        isFull         = false;
  bool        confirmada     = false;
  unsigned long ultimaPresenca = 0;
  unsigned long inicioInercia = 0;

  // NOVOS campos para comunicação:
  const char* state;         // "OUT", "IN" ou "ON"
  char        room[5];       // ex: "405" ou vazio
};

const char* quartoESP = "405";

const char* MAC_CAMA_1 = "da:80:c7:51:ac:11";
const char* MAC_CAMA_2 = "fd:2c:d4:cd:fc:49";

CamaBLE camas[] = {
  { MAC_CAMA_1, "HRP004201693" },
  { MAC_CAMA_2, "HRP000005656" }
};
const int NUM_CAMAS = sizeof(camas)/sizeof(camas[0]);

WiFiClient client;

// ========= Funções de tempo e NTP =========
String obterDataAtualISO8601() {
  struct tm timeinfo;
  if (!getLocalTime(&timeinfo)) return "1970-01-01T00:00:00.000Z";
  char buf[30];
  strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S.000Z", &timeinfo);
  return String(buf);
}

void configurarNTP() {
  configTime(GMT_OFFSET_SEC, DAYLIGHT_OFFSET_SEC, NTP_SERVER);
}

// ========= Função de envio de state (igual ao client.py) =========
bool sendState(const char* bed, const char* room, const char* state) {
  if (!client.connect(SERVER_IP, SERVER_PORT)) {
    Serial.println("[ESP] falha ao conectar ao servidor TCP");
    return false;
  }

  // monta JSON { bed, room, state, dataOn }
  StaticJsonDocument<128> doc;
  doc["bed"]   = bed;
  // só envia room se IN ou ON
  if (strcmp(state,"IN")==0 || strcmp(state,"ON")==0) 
    doc["room"]  = room;
  else 
    doc["room"] = nullptr;
  doc["state"] = state;
  doc["dataOn"] = obterDataAtualISO8601();

  String out;
  serializeJson(doc, out);
  out += "\n";
  Serial.println("JSON: " + out);

  client.print(out);
  client.flush();
  // fecha escrita, mantém leitura
  int fd = client.fd();
  shutdown(fd, SHUT_WR);

  // lê até o '\n' ou 2s de timeout
  unsigned long start = millis();
bool receivedValidAck = false;
while (millis() - start < 10000) {
  if (client.available()) {
    String resp = client.readStringUntil('\n');
    Serial.println("[ESP] ACK recebido: " + resp);

    StaticJsonDocument<128> doc;
    DeserializationError error = deserializeJson(doc, resp);

    if (!error) {
      if (doc.containsKey("status") && doc["status"] == "300") {
        receivedValidAck = true;
        Serial.println("[ESP] ACK válido recebido com status 300.");
        break;
      } else {
        Serial.println("[ESP] ACK recebido, mas inválido ou status diferente.");
      }
    } else {
      Serial.println("[ESP] ACK recebido mas não é JSON válido.");
    }
  }
  delay(50);
}

if (!receivedValidAck) {
  Serial.println("[ESP] Timeout ou ACK inválido.");
}
  client.stop();
  return true;
}

// ========= Função de polling de comando (igual ao client.py) =========
bool fetchCommand(const char* bed, String &action, String &dataOn) {
  if (!client.connect(SERVER_IP, SERVER_PORT)) {
    Serial.println("[ESP] falha ao conectar para polling");
    return false;
  }
  // envia {"bed":...}
  StaticJsonDocument<64> doc;
  doc["bed"] = bed;
  String out;
  serializeJson(doc, out);
  out += "\n";
  client.print(out);
  client.flush();
  shutdown(client.fd(), SHUT_WR);

  // lê até '\n' ou timeout curto
  unsigned long start = millis();
  String line;
  while (millis() - start < 500) {
    if (client.available()) {
      line = client.readStringUntil('\n');
      break;
    }
    delay(20);
  }
  client.stop();
  if (line.length() == 0) return false;

  // parseia
  StaticJsonDocument<96> resp;
  DeserializationError err = deserializeJson(resp, line);
  if (err || !resp.containsKey("action")) return false;
  action = resp["action"].as<String>();
  dataOn = resp["dataOn"].as<String>();
  return true;
}

// ========= BLE → RSSI → média =========
void updateRSSI(CamaBLE& cama, int rssi) {
  cama.rssiHistory[cama.index] = rssi;
  cama.index = (cama.index + 1) % RSSI_HISTORY_SIZE;
  if (cama.index == 0) cama.isFull = true;
}
int calcularMediaRSSI(CamaBLE& cama) {
  if (!cama.isFull) return -999;
  int s=0;
  for (int i=0;i<RSSI_HISTORY_SIZE;i++) s+=cama.rssiHistory[i];
  return s/RSSI_HISTORY_SIZE;
}

// Callback BLE
class MyAdvertisedDeviceCallbacks : public BLEAdvertisedDeviceCallbacks {
  void onResult(BLEAdvertisedDevice advertisedDevice) override {
    const char* addr = advertisedDevice.getAddress().toString().c_str();
    int rssi = advertisedDevice.getRSSI();
    for (int i=0;i<NUM_CAMAS;i++) {
      if (strcmp(addr, camas[i].mac)==0) {
        updateRSSI(camas[i], rssi);
      }
    }
  }
};

// ========= Processamento de entradas/saídas via BLE =========
void processarCamas() {
  unsigned long agora = millis();
  for (int i=0;i<NUM_CAMAS;i++) {
    auto &c = camas[i];
    int media = calcularMediaRSSI(c);
    bool presente = (media > RSSI_THRESHOLD);
    Serial.printf("Cama %s → RSSI méd=%d, estado=%s\n",
                  c.nome, media, c.state);

    // lógica de entrada
    if (presente) {
      if (!c.confirmada) {
        if (c.inicioInercia==0) {
          c.inicioInercia = agora;
        } else if (agora - c.inicioInercia >= INERCIA_CHEGADA) {
          c.confirmada = true;
          c.inicioInercia = 0;
          // state change: OUT -> IN
          c.state = "IN";
          strcpy(c.room, quartoESP);
          Serial.printf(">>> %s ENTROU em quarto %s\n", c.nome, c.room);
          sendState(c.nome, c.room, c.state);
        }
      }
      // reset último sinal de saída
      c.ultimaPresenca = 0;
    }
    else {
      // registra início de saída, se já estava confirmada
      if (c.confirmada && c.ultimaPresenca==0) {
        c.ultimaPresenca = agora;
      }
      // reset inércia de chegada
      c.inicioInercia = 0;
    }

    // lógica de saída
    if (c.confirmada && c.ultimaPresenca!=0 &&
        agora - c.ultimaPresenca >= TEMPO_SAIDA) {
      c.confirmada = false;
      c.ultimaPresenca = 0;
      c.isFull = false;
      c.index  = 0;
      // state change: IN/ON -> OUT
      c.state = "OUT";
      c.room[0] = '\0';  // sem quarto
      Serial.printf(">>> %s SAIU\n", c.nome);
      sendState(c.nome, nullptr, c.state);
    }
  }
}

void setup() {
  Serial.begin(115200);
  // Inicializa cada cama em OUT sem quarto
  for (int i=0;i<NUM_CAMAS;i++) {
    camas[i].state = "OUT";
    camas[i].room[0] = '\0';
  }

  // Wi-Fi, NTP e BLE
  WiFi.begin(SSID, PASSWORD);
  while (WiFi.status()!=WL_CONNECTED) delay(500);
  Serial.println("Wi-Fi ok: " + WiFi.localIP().toString());
  configurarNTP();
  BLEDevice::init("");
}

void loop() {
  // 1) scan BLE
  BLEScan* scan = BLEDevice::getScan();
  scan->setAdvertisedDeviceCallbacks(new MyAdvertisedDeviceCallbacks());
  scan->setActiveScan(true);
  scan->start(BEACON_SCAN_TIME, false);

  // 2) processa lógica BLE (entra/sai)
  processarCamas();

  // 3) polling de comando a cada POLL_INTERVAL
  static unsigned long lastPoll = 0;
  if (millis() - lastPoll >= POLL_INTERVAL*1000UL) {
    lastPoll = millis();
    for (int i=0;i<NUM_CAMAS;i++) {
      String action, dataOn;
      if (fetchCommand(camas[i].nome, action, dataOn)) {
        Serial.printf("[ESP] cmd para %s → %s @%s\n",
                      camas[i].nome,
                      action.c_str(),
                      dataOn.c_str());
        // aplica turnon/turnoff tal como o client.py
        if (action=="turnon" && strcmp(camas[i].state,"IN")==0) {
          camas[i].state = "ON";
          Serial.printf(">>> %s → ON confirmado\n", camas[i].nome);
          sendState(camas[i].nome, camas[i].room, "ON");
        }
        else if (action=="turnoff" && strcmp(camas[i].state,"ON")==0) {
          camas[i].state = "IN";
          Serial.printf(">>> %s → IN confirmado\n", camas[i].nome);
          sendState(camas[i].nome, camas[i].room, "IN");
        }
      }
    }
  }

  delay(500);
}
