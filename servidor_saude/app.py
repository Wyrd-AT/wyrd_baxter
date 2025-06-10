from flask import Flask, jsonify, render_template, request, Response
import threading
import json
import socket
import requests
from datetime import datetime, timezone
from waitress import serve
import math
import io
import csv

app = Flask(__name__)

# -------------------------------------------------------
# CONFIGURAÇÃO DO COUCHDB
# -------------------------------------------------------
COUCHDB_BASE = "https://admin:wyrd@db.vpn.ind.br"
DB_LOGS     = "saude_esp_logs"
URL_DB      = f"{COUCHDB_BASE}/{DB_LOGS}"

# -------------------------------------------------------
# Variáveis globais de estado (em memória)
# -------------------------------------------------------
rooms  = {}  # rooms[<quarto>][<ativo>] = dataOn
ativos = {}  # ativos[<ativo>] = {"quarto":…, "dataOn":…}
resets_pend = {}
lock   = threading.Lock()


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ---------------------------------------------
# Função que trata cada conexão TCP da ESP
# ---------------------------------------------
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
        rssi   = msg.get("rssi")  # só vem quando status == "RSSI"

        if quarto and ativo and status:

            # ── 1) Leitura de RSSI: insere direto no CouchDB e retorna ──
            if status == "RSSI" and (rssi is not None):

                # ── Verifica se há RESET pendente para esse (quarto, ativo) ──
                with lock:
                    if quarto in resets_pend and ativo in resets_pend[quarto]:
                        # Se houver reset pendente, envia RESET e remove da lista
                        print(f"[TCP] Enviando RESET para quarto={quarto} | ativo={ativo}")
                        conn.sendall((json.dumps({"status": "RESET"}) + "\n").encode('utf-8'))
                        resets_pend[quarto].remove(ativo)  # Remove da lista de pendências
                        if len(resets_pend[quarto]) == 0:
                            del resets_pend[quarto]  # Remove o quarto se não tiver mais resets pendentes
                        pass

                # ── Se não houver RESET pendente, insere normalmente no CouchDB ──
                payload = {
                    "tipo":      "rssi",
                    "server_ts": now_iso(),
                    "quarto":    quarto,
                    "ativo":     ativo,
                    "rssi":      rssi,
                    "dataOn":    dataOn
                }
                try:
                    resp = requests.post(URL_DB, json=payload, timeout=5)
                    if resp.status_code in (201, 202):
                        conn.sendall((json.dumps({"status": "300"}) + "\n").encode('utf-8'))
                    else:
                        print(f"[CouchDB] Falha ao inserir RSSI (HTTP {resp.status_code}): {resp.text}")
                        conn.sendall((json.dumps({"status": "500", "erro": "falha ao gravar RSSI"}) + "\n").encode('utf-8'))
                except Exception as e:
                    print(f"[CouchDB] Exceção ao conectar em {URL_DB}: {e}")
                    conn.sendall((json.dumps({"status": "500", "erro": "exceção ao gravar RSSI"}) + "\n").encode('utf-8'))
                return


            # ── 2) Leitura GET: insere no CouchDB e processa associação ──
            if status == "GET" and dataOn:
                payload = {
                    "tipo":      "conn",
                    "server_ts": now_iso(),
                    "quarto":    quarto,
                    "ativo":     ativo,
                    "status":    "GET",
                    "dataOn":    dataOn
                }
                try:
                    resp = requests.post(URL_DB, json=payload, timeout=5)
                    if resp.status_code not in (201, 202):
                        print(f"[CouchDB] Falha ao inserir GET (HTTP {resp.status_code}): {resp.text}")
                except Exception as e:
                    print(f"[CouchDB] Exceção ao conectar em {URL_DB}: {e}")

                with lock:
                    if quarto not in rooms:
                        rooms[quarto] = {}
                    ativo_associado = any(ativo in mapa for mapa in rooms.values())
                    if not ativo_associado:
                        rooms[quarto][ativo] = dataOn
                        ativos[ativo] = {"quarto": quarto, "dataOn": dataOn}
                        print(f"[TCP] Ativo associado: quarto={quarto} | ativo={ativo} → {dataOn}")
                        resposta = {"status": "300"}
                    else:
                        print(f"[TCP] Ativo {ativo} já está associado a outro quarto!")
                        resposta = {"status": "400", "erro": "ativo já associado a outro quarto"}

                conn.sendall((json.dumps(resposta) + "\n").encode('utf-8'))
                return

            # ── 3) Leitura OUT: insere no CouchDB e processa desassociação ──
            if status == "OUT":
                payload = {
                    "tipo":      "conn",
                    "server_ts": now_iso(),
                    "quarto":    quarto,
                    "ativo":     ativo,
                    "status":    "OUT",
                    "dataOn":    dataOn
                }
                try:
                    resp = requests.post(URL_DB, json=payload, timeout=5)
                    if resp.status_code not in (201, 202):
                        print(f"[CouchDB] Falha ao inserir OUT (HTTP {resp.status_code}): {resp.text}")
                except Exception as e:
                    print(f"[CouchDB] Exceção ao conectar em {URL_DB}: {e}")

                with lock:
                    ativo_removido = False
                    for r, mapa in rooms.items():
                        if ativo in mapa:
                            del mapa[ativo]
                            print(f"[TCP] Ativo desassociado do quarto: {ativo}")
                            ativo_removido = True
                            break

                    if ativo_removido:
                        ativos[ativo]["quarto"] = "Sem quarto"
                        resposta = {"status": "300"}
                    else:
                        resposta = {"status": "400", "erro": "ativo não encontrado"}

                conn.sendall((json.dumps(resposta) + "\n").encode('utf-8'))
                return

            # ── 4) status inválido ──
            resposta = {"status": "400", "erro": "status inválido"}
            conn.sendall((json.dumps(resposta) + "\n").encode('utf-8'))
            return

        # ── Payload incompleto ──
        resposta = {"status": "400", "erro": "payload incompleto"}
        conn.sendall((json.dumps(resposta) + "\n").encode('utf-8'))

    except Exception as e:
        print(f"[TCP] Erro ao tratar cliente {addr}: {e}")
    finally:
        conn.close()


# ---------------------------------------------
# Thread que roda o servidor TCP na porta 9500
# ---------------------------------------------
def tcp_server():
    host = "0.0.0.0"
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


# Inicia a thread TCP assim que o app carregar
threading.Thread(target=tcp_server, daemon=True).start()


# ---------------------------------------------
# Rota para página inicial
# ---------------------------------------------
@app.route('/')
def index():
    return render_template('index.html')

# Rota para fornecer o status dos quartos e ativos
@app.route('/status')
def status():
    with lock:
        copia_rooms  = {r: {ato: ts for ato, ts in rooms[r].items()} for r in rooms}
        copia_ativos = {ato: dados for ato, dados in ativos.items()}
    return jsonify(rooms=copia_rooms, ativos=copia_ativos)

# Rota para exibir os logs RSSI
@app.route('/logs_rssi')
def logs_rssi():
    per_page = 50
    try:
        page = int(request.args.get('page', '1'))
        if page < 1:
            page = 1
    except ValueError:
        page = 1

    skip = (page - 1) * per_page

    try:
        # Busca os logs RSSI do CouchDB
        url = f"{URL_DB}/_all_docs?include_docs=true&limit={per_page}&skip={skip}"
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()

        copia = []
        for row in data.get('rows', []):
            doc = row.get('doc', {})
            if doc.get('tipo') == 'rssi':
                copia.append(doc)

        # Obter informações sobre o banco de dados para paginação
        meta_resp = requests.get(f"{URL_DB}", timeout=5)
        meta_resp.raise_for_status()
        info_db = meta_resp.json()
        total_docs = info_db.get('doc_count', 0)
        total_pages = (total_docs // per_page) + 1

    except Exception as e:
        print(f"[CouchDB] Erro ao buscar logs_rssi paginados: {e}")
        copia = []
        total_pages = 1
        page = 1

    return render_template('logs_rssi.html', logs_rssi=copia, page=page, total_pages=total_pages)

# Rota de streaming para baixar logs de RSSI em CSV
@app.route('/download_rssi')
def download_rssi():
    def generate():
        buf = io.StringIO()
        writer = csv.writer(buf)

        # Cabeçalho
        writer.writerow(['server_ts', 'quarto', 'ativo', 'rssi', 'dataOn'])
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)

        limit = 100
        skip  = 0

        while True:
            url = f"{URL_DB}/_all_docs?include_docs=true&limit={limit}&skip={skip}"
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            rows = data.get('rows', [])
            if not rows:
                break

            for row in rows:
                doc = row.get('doc', {})
                if doc.get('tipo') == 'rssi':
                    writer.writerow([doc.get('server_ts', ''),
                                     doc.get('quarto', ''),
                                     doc.get('ativo', ''),
                                     doc.get('rssi', ''),
                                     doc.get('dataOn', '')])
                    yield buf.getvalue()
                    buf.seek(0)
                    buf.truncate(0)

            skip += limit

    headers = {
        'Content-Disposition': 'attachment; filename="rssi_logs.csv"'
    }
    return Response(generate(), mimetype='text/csv', headers=headers)


# -------------------------------------------------------
# Rota paginada para exibir logs de conexão (GET/OUT) em HTML (sem redirect)
# -------------------------------------------------------
# Rota para exibir os logs de conexão
@app.route('/logs_conn')
def logs_conn():
    per_page = 50
    try:
        page = int(request.args.get('page', '1'))
        if page < 1:
            page = 1
    except ValueError:
        page = 1

    skip = (page - 1) * per_page

    try:
        # Busca os logs de conexão do CouchDB
        url_all = f"{URL_DB}/_all_docs?include_docs=true"
        resp = requests.get(url_all, timeout=5)
        resp.raise_for_status()
        data = resp.json()

        all_conn = []
        for row in data.get('rows', []):
            doc = row.get('doc', {})
            if doc.get('tipo') == 'conn':
                all_conn.append(doc)

        total_docs_conn = len(all_conn)
        total_pages = math.ceil(total_docs_conn / per_page) if total_docs_conn > 0 else 1

        # Ajustar "page" caso ultrapasse o total de páginas
        if page > total_pages:
            page = total_pages

        skip = (page - 1) * per_page
        page_docs = all_conn[skip: skip + per_page]

    except Exception as e:
        print(f"[CouchDB] Erro ao buscar/filtrar logs_conn: {e}")
        page_docs = []
        total_pages = 1
        page = 1
        total_docs_conn = 0

    return render_template('logs_conn.html', logs_conn=page_docs, page=page, total_pages=total_pages)

# Rota de streaming para baixar logs de conexão em CSV
@app.route('/download_conn')
def download_conn():
    def generate():
        buf = io.StringIO()
        writer = csv.writer(buf)

        writer.writerow(['server_ts', 'quarto', 'ativo', 'status', 'dataOn'])
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)

        limit = 100
        skip  = 0

        while True:
            url = f"{URL_DB}/_all_docs?include_docs=true&limit={limit}&skip={skip}"
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            rows = data.get('rows', [])
            if not rows:
                break

            for row in rows:
                doc = row.get('doc', {})
                if doc.get('tipo') == 'conn':
                    writer.writerow([doc.get('server_ts', ''),
                                     doc.get('quarto', ''),
                                     doc.get('ativo', ''),
                                     doc.get('status', ''),
                                     doc.get('dataOn', '')])
                    yield buf.getvalue()
                    buf.seek(0)
                    buf.truncate(0)

            skip += limit

    headers = {
        'Content-Disposition': 'attachment; filename="conn_logs.csv"'
    }
    return Response(generate(), mimetype='text/csv', headers=headers)


# ---------------------------------------------
# Inicia o Flask via Waitress
# ---------------------------------------------
if __name__ == '__main__':
    serve(app, host='0.0.0.0', port=5000)
