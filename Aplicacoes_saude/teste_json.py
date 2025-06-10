#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import socket
import json
from datetime import datetime, timezone

def agora_iso():
    """Retorna timestamp ISO-8601 em UTC (ex.: 2025-06-04T15:30:00.000Z)"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

def envia_json_teste(host='192.168.1.213', port=9500):
    # Monte aqui o dicionário de teste com as mesmas chaves que o ESP enviaria
    msg = {
        "quarto": "1",
        "ativo":   "B",
        "status": "OUT",
        "dataOn": agora_iso()
    }
    texto = json.dumps(msg) + "\n"  # lembre-se do '\n' no final
    
    try:
        # 1) Cria o socket e conecta no servidor TCP
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((host, port))
        print(f"Conectado em {host}:{port}")
        
        # 2) Envia o JSON codificado em UTF-8
        sock.sendall(texto.encode('utf-8'))
        print("JSON enviado:", texto.strip())
        
        # 3) Aguarda a resposta (ex.: {"status":"300"}\n)
        #    Vamos ler até encontrar '\n' ou até 1024 bytes
        data = b''
        while b'\n' not in data:
            parte = sock.recv(1024)
            if not parte:
                break
            data += parte
        
        if data:
            resposta = data.decode('utf-8').strip()
            print("Resposta do servidor:", resposta)
        else:
            print("Servidor fechou a conexão sem enviar resposta.")
        
    except Exception as e:
        print("Erro ao enviar JSON de teste:", e)
    finally:
        sock.close()

if __name__ == "__main__":
    envia_json_teste()
