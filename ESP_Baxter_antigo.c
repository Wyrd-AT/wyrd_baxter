#include <BLEDevice.h>
#include <WiFi.h>
#include <ArduinoJson.h>
#include "lwip/sockets.h"

// ============ CONFIG Wi-Fi e Servidor ============
const char* SSID = "BaxShowroom";
const char* PASSWORD = "Welcome@Baxter";
const char* SERVER_IP = "172.28.74.150";
const uint16_t SERVER_PORT = 9500;

// ============ CONFIG NTP ============
const char* NTP_SERVER = "pool.ntp.org";
const long GMT_OFFSET_SEC = -3 * 3600; // Brasília
const int DAYLIGHT_OFFSET_SEC = 0;

const char* quartoESP = "409";

// ============ CONFIG BLE ============
#define RSSI_THRESHOLD -64
#define INERCIA_CHEGADA 20000
#define TEMPO_SAIDA 10000
#define RSSI_HISTORY_SIZE 5
#define BEACON_SCAN_TIME 4

// ============ CLIENTE TCP ============
WiFiClient client;

// ============ Estrutura BLE ============
const char* MAC_CAMA_1 = "da:80:c7:51:ac:11";
const char* MAC_CAMA_2 = "fd:2c:d4:cd:fc:49";

struct CamaBLE {
  const char* mac;
  const char* nome;
  int rssiHistory[RSSI_HISTORY_SIZE];
  uint8_t index = 0;
  bool isFull = false;
  bool confirmada = false;
  unsigned long ultimaPresenca = 0;
  unsigned long inicioInercia = 0;
};

CamaBLE camas[] = {
  { MAC_CAMA_1, "HRP004201693" }, // esquerda
  { MAC_CAMA_2, "HRP000005656" }  // direita
};

const int NUM_CAMAS = sizeof(camas) / sizeof(CamaBLE);

// ============ FUNÇÕES ============
void conectarWiFi() {
  Serial.print("Conectando ao Wi-Fi...");
  WiFi.begin(SSID, PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
  }
  Serial.println("\nWi-Fi conectado. IP: " + WiFi.localIP().toString());
}

void configurarNTP() {
  configTime(GMT_OFFSET_SEC, DAYLIGHT_OFFSET_SEC, NTP_SERVER);
}

bool shutdownWrite(WiFiClient &client) {
  int fd = client.fd();
  return fd >= 0 && shutdown(fd, SHUT_WR) == 0;
}

String obterDataAtualISO8601() {
  struct tm timeinfo;
  if (!getLocalTime(&timeinfo)) return "1970-01-01T00:00:00.000Z";
  char buffer[30];
  strftime(buffer, sizeof(buffer), "%Y-%m-%dT%H:%M:%S.000Z", &timeinfo);
  return String(buffer);
}

bool enviarJson(const String& quarto, const String& cama, const String& status) {
  if (!client.connect(SERVER_IP, SERVER_PORT)) {
    Serial.println("Falha ao conectar no servidor.");
    return false;
  }

  StaticJsonDocument<96> doc;
  doc["quarto"] = quarto;
  doc["cama"] = cama;
  //doc["status"] = status;
  doc["dataOn"] = obterDataAtualISO8601();

  String jsonString;
  serializeJson(doc, jsonString);
  jsonString += "\n";

  client.print(jsonString);
  Serial.println("Enviado ao servidor: " + jsonString);
  client.flush();
  shutdownWrite(client);

  unsigned long inicio = millis();
  while (millis() - inicio < 20000) {
    if (client.available()) {
      Serial.println("Resposta servidor: " + client.readStringUntil('\n'));
      break;
    }
    delay(100);
  }

  client.stop();
  return true;
}

// ============ Lógica BLE ============
void updateRSSI(CamaBLE& cama, int rssi) {
  cama.rssiHistory[cama.index] = rssi;
  cama.index = (cama.index + 1) % RSSI_HISTORY_SIZE;
  if (cama.index == 0) cama.isFull = true;
}

int calcularMediaRSSI(CamaBLE& cama) {
  if (!cama.isFull) return -999;
  int soma = 0;
  for (int i = 0; i < RSSI_HISTORY_SIZE; i++) soma += cama.rssiHistory[i];
  return soma / RSSI_HISTORY_SIZE;
}

class MyAdvertisedDeviceCallbacks : public BLEAdvertisedDeviceCallbacks {
  void onResult(BLEAdvertisedDevice advertisedDevice) {
    const char* addr = advertisedDevice.getAddress().toString().c_str();
    int rssi = advertisedDevice.getRSSI();
    for (int i = 0; i < NUM_CAMAS; i++) {
      if (strcmp(addr, camas[i].mac) == 0) {
        updateRSSI(camas[i], rssi);
      }
    }
  }
};

void processarCamas() {
  unsigned long agora = millis();

  for (int i = 0; i < NUM_CAMAS; i++) {
    CamaBLE& cama = camas[i];
    int media = calcularMediaRSSI(cama);
    bool novoPresente = (media > RSSI_THRESHOLD);

    // Exibindo a média de RSSI
    Serial.printf("Cama: %s, Média RSSI: %d\n", cama.nome, media);

    //(Verificação de Cama Confirmada): Verifica se a cama está ocupada e, caso contrário, 
    //registra o tempo da última presença ou reseta o tempo caso a cama seja detectada novamente.
    if (cama.confirmada) {
      if (!novoPresente && cama.inicioInercia == 0) {
        if (cama.ultimaPresenca == 0) cama.ultimaPresenca = agora;
      } else if (novoPresente) {
        cama.ultimaPresenca = 0;
      }
    }

    //(Cama Presente: Confirmação de Entrada): Quando a cama é detectada como presente por tempo suficiente,
    // confirma que a cama foi ocupada e envia o status "GET" para o servidor.
    if (novoPresente) {
      if (!cama.confirmada && cama.inicioInercia == 0) {
        cama.inicioInercia = agora;
      } else if (!cama.confirmada && agora - cama.inicioInercia >= INERCIA_CHEGADA) {
        cama.confirmada = true;
        cama.inicioInercia = 0;
        Serial.printf(">>> %s ENTROU\n", cama.nome);
        enviarJson(quartoESP, cama.nome, "GET");
      }
    } else {
      cama.inicioInercia = 0;
    }

    //(Cama Confirmada: Tempo de Saída): Quando a cama permanece não presente por tempo suficiente,
    // a desmarca como confirmada, reseta os dados e envia o status "OFF" para o servidor.
    if (cama.confirmada && cama.ultimaPresenca != 0 && (agora - cama.ultimaPresenca > TEMPO_SAIDA)) {
      cama.confirmada = false;
      cama.index = 0;
      cama.isFull = false;
      cama.ultimaPresenca = 0;
      Serial.printf(">>> %s SAIU\n", cama.nome);
     
      // Definindo o quarto com base no nome da cama
      String quarto = (strcmp(cama.nome, "HRP004201693") == 0) ? "401" : "402"; // Cama 1 vai para o quarto 1 e cama 2 para o quarto 2
      enviarJson(quarto, cama.nome, "OFF");
    }
  }
}


// ============ SETUP/LOOP ============
void setup() {
  Serial.begin(115200);
  conectarWiFi();
  configurarNTP();
  BLEDevice::init("");
}

void loop() {
  BLEScan* scanner = BLEDevice::getScan();
  scanner->setAdvertisedDeviceCallbacks(new MyAdvertisedDeviceCallbacks());
  scanner->setActiveScan(true);
  scanner->start(BEACON_SCAN_TIME, false);
  processarCamas();
  delay(500);
}