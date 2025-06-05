#!/usr/bin/env python3
import socket
import json

HOST = '10.0.0.149'   # pode trocar para um IP específico, se quiser
PORT = 9500

def handle_client(conn, addr):
    print(f'Conexão de {addr}')
    data = b''
    # lê até encontrar \n ou conexão fechar
    while True:
        chunk = conn.recv(1024)
        if not chunk:
            break
        data += chunk
        if b'\n' in chunk:
            break

    text = data.decode('utf-8').strip()
    print(f'Recebido: {text}')
    try:
        obj = json.loads(text)
        quarto = obj.get('quarto')
        cama = obj.get('cama')
        dataOn = obj.get('dataOn')
        # opcional: log do status recebido
        print(f'   → status recebido: {obj.get("status")}')

        # prepara resposta
        response = {
            'quarto': quarto,
            'cama': cama,
            'status': 300,
            'dataOn': dataOn
        }
        resp_str = json.dumps(response) + '\n'
        conn.sendall(resp_str.encode('utf-8'))
        print(f'Enviado: {resp_str.strip()}')

    except json.JSONDecodeError:
        print('JSON inválido recebido, ignorando.')
    finally:
        conn.close()

def main():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, PORT))
        s.listen()
        print(f'Servidor TCP escutando em {HOST}:{PORT}')
        while True:
            conn, addr = s.accept()
            handle_client(conn, addr)

if __name__ == '__main__':
    main()
