from flask import Flask, jsonify, render_template_string
import threading
import json
import socket
from datetime import datetime, timezone

app = Flask(__name__)

rooms = {}
lock_rooms = threading.Lock()

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

def handle_tcp_client(conn, addr):
    try:
        data = b''
        while b'\n' not in data:
            chunk = conn.recv(1024)
            if not chunk:
                break
            data += chunk

        if not data:
            return

        try:
            texto = data.decode('utf-8').strip()
            msg = json.loads(texto)
        except Exception as e:
            print(f"[TCP] JSON inválido de {addr}: '{data!r}' → erro: {e}")
            return

        quarto = msg.get("quarto")
        ativo  = msg.get("ativo")
        dataOn = msg.get("dataOn")
        status = msg.get("status")

        if quarto and ativo:
            if status == "GET" and dataOn:
                with lock_rooms:
                    if quarto not in rooms:
                        rooms[quarto] = {}
                    ativo_associado = False
                    for r in rooms.values():
                        if ativo in r:
                            ativo_associado = True
                            break
                    if not ativo_associado:
                        rooms[quarto][ativo] = dataOn
                        print(f"[TCP] Ativo associado: quarto={quarto} | ativo={ativo} → {dataOn}")
                        resposta = {"status": "300"}
                    else:
                        print(f"[TCP] Ativo {ativo} já está associado a outro quarto!")
                        resposta = {"status": "400", "erro": "ativo já associado a outro quarto"}
            elif status == "OUT":
                with lock_rooms:
                    for r in rooms.values():
                        if ativo in r:
                            del r[ativo]
                            print(f"[TCP] Ativo removido: {ativo}")
                            resposta = {"status": "300"}
                            break
                    else:
                        resposta = {"status": "400", "erro": "ativo não encontrado"}
            else:
                resposta = {"status": "400", "erro": "status inválido"}

            conn.sendall((json.dumps(resposta) + "\n").encode('utf-8'))
        else:
            print(f"[TCP] Payload sem campos esperados: {msg}")
            resposta = {"status": "400", "erro": "payload incompleto"}
            conn.sendall((json.dumps(resposta) + "\n").encode('utf-8'))

    except Exception as e:
        print(f"[TCP] Erro ao tratar cliente {addr}: {e}")
    finally:
        conn.close()

def tcp_server():
    host = "10.0.0.149"
    port = 9500
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(5)
    print(f"[TCP] Servidor escutando em {host}:{port} ...")

    try:
        while True:
            conn, addr = srv.accept()
            t = threading.Thread(target=handle_tcp_client, args=(conn, addr), daemon=True)
            t.start()
    except Exception as e:
        print(f"[TCP] Erro no laço principal: {e}")
    finally:
        srv.close()

threading.Thread(target=tcp_server, daemon=True).start()

@app.route('/')
def index():
    return render_template_string(TEMPLATE_HTML)

@app.route('/status')
def status():
    with lock_rooms:
        copia = { r: { ativo: ts for ativo, ts in rooms[r].items() }
                  for r in rooms }
    return jsonify(rooms=copia)

TEMPLATE_HTML = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <title>Painel de Quartos e Ativos</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 20px; }
    h1 { margin-bottom: 0.5em; }
    #device-table { width: 100%; border-collapse: collapse; margin-bottom: 20px; }
    #device-table th, #device-table td { border: 1px solid #aaa; padding: 8px; text-align: left; }
    #device-table th { background-color: #f0f0f0; }
    .rooms-container { display: flex; justify-content: space-between; gap: 10px; }
    .room-box { flex: 1; border: 2px solid #444; border-radius: 6px; padding: 15px; min-height: 200px; background-color: #fafafa; }
    .room-box h2 { font-size: 1.4em; margin-bottom: 0.8em; color: #333; text-align: center; }
    .ativo-item { margin-left: 10px; margin-bottom: 6px; }
    .timestamp { color: #555; font-size: 0.9em; margin-left: 5px; }
    #mensagem-status { margin-bottom: 10px; font-size: 0.9em; color: #007700; }
    .bolinha-verde { width: 12px; height: 12px; border-radius: 50%; background-color: green; display: inline-block; margin-left: 5px; }
    .tooltip { position: relative; display: inline-block; }
    .tooltip .tooltiptext { visibility: hidden; width: 200px; background-color: black; color: #fff; text-align: center; border-radius: 5px; padding: 5px; position: absolute; z-index: 1; bottom: 125%; left: 50%; margin-left: -100px; opacity: 0; transition: opacity 0.3s; }
    .tooltip:hover .tooltiptext { visibility: visible; opacity: 1; }
  </style>
</head>
<body>
  <h1>Painel de Quartos e Ativos</h1>
  <div id="mensagem-status">Carregando...</div>
  <table id="device-table">
    <thead>
      <tr>
        <th>Ativo (Dispositivo)</th>
        <th>Quarto</th>
        <th>Última Atualização</th>
      </tr>
    </thead>
    <tbody id="device-table-body"></tbody>
  </table>
  <div class="rooms-container">
    <div class="room-box" id="room-1">
        <h2>Quarto 1</h2>
        <div class="ativos-lista" id="ativos-1"></div>
    </div>
    <div class="room-box" id="room-2">
        <h2>Quarto 2</h2>
        <div class="ativos-lista" id="ativos-2"></div>
    </div>
    <div class="room-box" id="room-3">
        <h2>Quarto 3</h2>
        <div class="ativos-lista" id="ativos-3"></div>
    </div>
  </div>
  <script>
    async function fetchStatus() {
      try {
        const resp = await fetch('/status');
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        const data = await resp.json();
        return data;
      } catch (err) {
        console.error("Erro ao buscar /status:", err);
        return null;
      }
    }

    function atualizarTabelaDispositivos(rooms) {
      const tbody = document.getElementById('device-table-body');
      tbody.innerHTML = '';  // Limpa a tabela

      Object.keys(rooms).sort().forEach(quarto => {
        const ativos = rooms[quarto];
        Object.keys(ativos).sort().forEach(ativoId => {
          const ts = ativos[ativoId];
          const tr = document.createElement('tr');
          const tdAtivo = document.createElement('td');
          tdAtivo.textContent = ativoId;
          const tdQuarto = document.createElement('td');
          tdQuarto.textContent = quarto;
          const tdTs = document.createElement('td');
          tdTs.textContent = ts;
          tr.appendChild(tdAtivo);
          tr.appendChild(tdQuarto);
          tr.appendChild(tdTs);
          tbody.appendChild(tr);
        });
      });

      if (Object.keys(rooms).length === 0) {
        const tr = document.createElement('tr');
        const td = document.createElement('td');
        td.setAttribute('colspan', '3');
        td.style.textAlign = 'center';
        td.textContent = 'Nenhum dispositivo conectado.';
        tr.appendChild(td);
        tbody.appendChild(tr);
      }
    }

    function atualizarCards(rooms) {
      for (let i = 1; i <= 3; i++) {
        const roomId = i.toString();
        const containerAtivos = document.getElementById('ativos-' + i);
        containerAtivos.innerHTML = '';

        const ativosDoQuarto = rooms[roomId];
        if (ativosDoQuarto && Object.keys(ativosDoQuarto).length > 0) {
          Object.keys(ativosDoQuarto).sort().forEach(ativo => {
            const ts = ativosDoQuarto[ativo];
            const divA = document.createElement('div');
            divA.className = 'ativo-item';

            const bolinha = document.createElement('span');
            bolinha.className = 'bolinha-verde';

            const tooltipContainer = document.createElement('div');
            tooltipContainer.className = 'tooltip';
            
            const tooltipText = document.createElement('span');
            tooltipText.className = 'tooltiptext';
            tooltipText.textContent = `Ativo: ${ativo} — Hora: ${ts}`;

            tooltipContainer.appendChild(bolinha);
            tooltipContainer.appendChild(tooltipText);
            divA.appendChild(tooltipContainer);
            
            containerAtivos.appendChild(divA);
          });
        } else {
          const p = document.createElement('div');
          p.textContent = 'Nenhum ativo cadastrado.';
          p.style.color = '#666';
          containerAtivos.appendChild(p);
        }
      }
    }

    async function refreshLoop() {
      const dados = await fetchStatus();
      if (dados && dados.rooms) {
        document.getElementById('mensagem-status').textContent = 
          "Última atualização: " + new Date().toLocaleTimeString();
        atualizarTabelaDispositivos(dados.rooms);
        atualizarCards(dados.rooms);
      } else {
        document.getElementById('mensagem-status').textContent = 
          "Erro ao obter dados do servidor.";
      }
    }

    window.addEventListener('DOMContentLoaded', () => {
      refreshLoop();
      setInterval(refreshLoop, 2000);
    });
  </script>
</body>
</html>
"""

if __name__ == '__main__':
    app.run(debug=True, host='10.0.0.149', port=5000)
