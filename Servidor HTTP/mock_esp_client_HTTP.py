#!/usr/bin/env python3
import sys
import time
from datetime import datetime, timezone
import socketio

SERVER_URL = 'http://10.0.0.149:9500'  # ajuste conforme seu servidor

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

bed_id = input("ID da cama: ").strip()

sio = socketio.Client()

# estado local da cama
state_info = {'state': 'OUT', 'room': None}

@sio.event
def connect():
    print("🔗 Conectado ao servidor WS")
    # ao conectar, podemos registrar nossa cama
    # (opcional, se quiser usar rooms no Socket.IO)
    # sio.emit('register', {'bed': bed_id})

@sio.on('state_update')
def on_state_update(data):
    print("🗺️  Estado global:")
    print(data)

@sio.on('command')
def on_command(data):
    # só processa se for para a nossa cama
    if data.get('bed') != bed_id:
        return
    action = data.get('action')
    ts     = data.get('dataOn')
    print(f"📩 Comando recebido para {bed_id}: {action} @ {ts}")

    st = state_info['state']
    if action == 'turnon' and st == 'IN':
        state_info['state'] = 'ON'
    elif action == 'turnoff' and st == 'ON':
        state_info['state'] = 'IN'
    else:
        print(f"Comando inválido em state={st}")
        return

    # envia o novo state de volta ao servidor
    sio.emit('state', {
        'bed': bed_id,
        'room': state_info['room'],
        'state': state_info['state'],
        'dataOn': now_iso()
    })
    print(f"Enviado novo state={state_info['state']}")

@sio.event
def disconnect():
    print("Desconectado do servidor")

def main():
    sio.connect(SERVER_URL)
    try:
        while True:
            evt = input("Evento (get_in / out_of / q): ").strip().lower()
            if evt == 'q':
                break
            if evt == 'get_in' and state_info['state'] == 'OUT':
                room = input("  Quarto (ex: 402): ").strip()
                state_info.update(state='IN', room=room)
                sio.emit('state', {
                    'bed': bed_id,
                    'room': room,
                    'state': 'IN',
                    'dataOn': now_iso()
                })
                print("Enviado state=IN")
            elif evt == 'out_of' and state_info['state'] in ('IN','ON'):
                state_info.update(state='OUT', room=None)
                sio.emit('state', {
                    'bed': bed_id,
                    'room': None,
                    'state': 'OUT',
                    'dataOn': now_iso()
                })
                print("Enviado state=OUT")
            else:
                print(f"Transição inválida em state={state_info['state']}")
            time.sleep(0.1)
    finally:
        sio.disconnect()

if __name__ == '__main__':
    main()

#cama esquerda - HRP004201693 - 1
#cama direita  - HRP000005656 - 2