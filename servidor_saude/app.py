from flask import Flask, jsonify, render_template, request, redirect, url_for
import threading
import json
import socket
from datetime import datetime, timezone
from waitress import serve

app = Flask(__name__)

# Variáveis de dados e lock para sincronização
rooms = {}
ativos = {}
lock_rooms = threading.Lock()

def now_iso():
    """Retorna um timestamp ISO-8601 em UTC"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

# Função para tratar os dados recebidos pelo servidor TCP
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
                        ativos[ativo] = {"quarto": quarto, "dataOn": dataOn}
                        print(f"[TCP] Ativo associado: quarto={quarto} | ativo={ativo} → {dataOn}")
                        resposta = {"status": "300"}
                    else:
                        print(f"[TCP] Ativo {ativo} já está associado a outro quarto!")
                        resposta = {"status": "400", "erro": "ativo já associado a outro quarto"}
            elif status == "OUT":
                with lock_rooms:
                    ativo_removido = False
                    for r in rooms.values():
                        if ativo in r:
                            del r[ativo]  # Remove o ativo do quarto
                            print(f"[TCP] Ativo desassociado do quarto: {ativo}")
                            ativo_removido = True
                            break

                    if ativo_removido:
                        # Agora você pode manter o ativo registrado sem quarto
                        ativos[ativo]["quarto"] = "Sem quarto"
                        resposta = {"status": "300"}
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

# Função do servidor TCP
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

# Inicia a thread do servidor TCP assim que o módulo é importado/executado
threading.Thread(target=tcp_server, daemon=True).start()

# Rota para a página inicial (HTML)
@app.route('/')
def index():
    return render_template('index.html')

# Rota para fornecer o status dos quartos e ativos
@app.route('/status')
def status():
    with lock_rooms:
        copia_rooms = {r: {ativo: ts for ativo, ts in rooms[r].items()} for r in rooms}
        copia_ativos = {ativo: dados for ativo, dados in ativos.items()}
    return jsonify(rooms=copia_rooms, ativos=copia_ativos)

@app.route('/reset', methods=['POST'])
def reset_data():
    with lock_rooms:
        rooms.clear()
        ativos.clear()
    return jsonify({"status": "dados resetados"})  # ou: redirect(url_for('index'))

if __name__ == '__main__':
    serve(app, host='0.0.0.0', port=5000)
