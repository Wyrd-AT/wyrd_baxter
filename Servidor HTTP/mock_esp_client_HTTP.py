import time
import json
import requests
from datetime import datetime, timezone
import threading

SERVER_URL   = "http://10.0.0.149:9500"
POLL_INTERVAL = 5  # em segundos

def iso_ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

def send_state(bed, room, state):
    payload = {
        "bed":   bed,
        "room":  room,
        "state": state,
        "dataOn": iso_ts()
    }
    try:
        r = requests.post(f"{SERVER_URL}/state", json=payload, timeout=5)
        r.raise_for_status()
        resp = r.json()
        print(f"[ESP→HTTP] state sent → recebeu: {resp}")
        return resp
    except Exception as e:
        print("[ESP] erro ao enviar state:", e)
        return None

def fetch_command(bed):
    try:
        r = requests.post(f"{SERVER_URL}/poll", json={"bed": bed}, timeout=5)
        if r.status_code == 204:
            return {}
        r.raise_for_status()
        cmd = r.json()
        print(f"[ESP←HTTP] polling → recebeu: {cmd}")
        return cmd
    except Exception as e:
        print("[ESP] erro no polling:", e)
        return {}

def command_poller(bed, state_info, stop_evt):
    while not stop_evt.is_set():
        cmd = fetch_command(bed)
        action = cmd.get("action")
        if action:
            st, rm = state_info["state"], state_info["room"]
            if action == "turnon" and st == "IN":
                state_info["state"] = "ON"
                send_state(bed, rm, "ON")
            elif action == "turnoff" and st == "ON":
                state_info["state"] = "IN"
                send_state(bed, rm, "IN")
            else:
                print(f"[ESP] comando inválido em state={st}")
        time.sleep(POLL_INTERVAL)

def main():
    bed = input("ID da cama: ").strip()
    state_info = {"state": "OUT", "room": None}
    stop_evt = threading.Event()
    threading.Thread(target=command_poller,
                     args=(bed, state_info, stop_evt),
                     daemon=True).start()

    print(f"=== Simulator HTTP-only for {bed} ===")
    print("Eventos: get_in, out_of  (q para sair)\n")
    try:
        while True:
            evt = input("Evento ESP: ").strip().lower()
            if evt == 'q':
                break

            st, rm = state_info["state"], state_info["room"]
            if evt == "get_in" and st == "OUT":
                room = input("Quarto (ex: 402): ").strip()
                state_info.update(state="IN", room=room)
                send_state(bed, room, "IN")

            elif evt == "out_of" and st in ("IN","ON"):
                state_info.update(state="OUT", room=None)
                send_state(bed, None, "OUT")

            else:
                print(f"[ESP] transição inválida: evt={evt} em state={st}")

    finally:
        stop_evt.set()
        print("=== Encerrando ESP simulator ===")

if __name__ == "__main__":
    main()

#cama esquerda - HRP004201693 - 1
#cama direita  - HRP000005656 - 2