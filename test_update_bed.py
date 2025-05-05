import sys
import json
import socket
import threading
import time
from datetime import datetime, timezone

# — endereço do servidor raw-TCP que implementa o protocolo JSON cru —
SERVER_IP   = "192.168.0.108"
SERVER_PORT = 9500
POLL_INTERVAL = 5  # segundos entre cada polling de comando

def iso_ts():
    """Timestamp ISO-8601 UTC (ex: '2025-05-04T15:30:00.000Z')."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

def send_state(bed, room, state):
    """
    Envia UM JSON de estado (só quando o estado mudar!) no formato:
      { "bed":…, "room":…, "state":…, "dataOn":… }
    ao servidor raw-TCP (porta 9500), e imprime a resposta JSON que chegar.
    """
    msg = {
        "bed":   bed,
        "room":  room,
        "state": state,
        "dataOn": iso_ts()
    }
    data = json.dumps(msg) + "\n"
    try:
        sock = socket.create_connection((SERVER_IP, SERVER_PORT), timeout=5)
        sock.sendall(data.encode("utf-8"))

        # lê até '\n' e desserializa
        resp = b""
        while not resp.endswith(b"\n"):
            chunk = sock.recv(1024)
            if not chunk:
                break
            resp += chunk
        sock.close()

        j = json.loads(resp.decode().strip())
        print(f"[ESP→TCP] enviou state → [TCP→ESP] recebeu: {j}")
        return j
    except Exception as e:
        print(f"[ESP] erro ao enviar state via TCP:", e)
        return None

def fetch_command(bed):
    """
    Polling de comando: a cada POLL_INTERVAL conecta no servidor,
    envia apenas {"bed":…} e espera um JSON de resposta:
      { "bed":…, "action":… , "dataOn":… }
    imprime esse JSON retornado e o devolve.
    """
    poll_msg = { "bed": bed }
    data = json.dumps(poll_msg) + "\n"
    try:
        sock = socket.create_connection((SERVER_IP, SERVER_PORT), timeout=5)
        sock.sendall(data.encode("utf-8"))

        resp = b""
        while not resp.endswith(b"\n"):
            chunk = sock.recv(1024)
            if not chunk:
                break
            resp += chunk
        sock.close()

        decoded = resp.decode().strip()
        if not decoded:
            # não veio JSON, então não há comando pendente
            return {}

        cmd = json.loads(decoded)
        print(f"[ESP←TCP] polling → recebeu: {cmd}")
        return cmd
    except Exception as e:
        print(f"[ESP] erro no polling via TCP:", e)
        return {}

def command_poller(bed, state_info, stop_evt):
    """
    Thread que, a cada POLL_INTERVAL segundos,
    faz fetch_command() e, se vier action, aplica localmente
    e dispara send_state() para confirmar a mudança.
    """
    while not stop_evt.is_set():
        cmd = fetch_command(bed)
        action = cmd.get("action")
        if action:
            dataOn = cmd.get("dataOn")
            print(f"[SV→ESP] comando {action} (dataOn={dataOn})")
            st, rm = state_info["state"], state_info["room"]

            if action == "turnon" and st == "IN":
                state_info["state"] = "ON"
                print(f"[ESP] aplicando turnon → novo state=ON")
                send_state(bed, rm, "ON")

            elif action == "turnoff" and st == "ON":
                state_info["state"] = "IN"
                print(f"[ESP] aplicando turnoff → novo state=IN")
                send_state(bed, rm, "IN")

            else:
                print(f"[ESP] comando inválido em state={st}")
        time.sleep(POLL_INTERVAL)

def main():
    bed = sys.argv[1] if len(sys.argv)>1 else input("ID da cama: ").strip()
    state_info = {"state": "OUT", "room": None}

    # inicia só o polling de comando (não reenvia state aqui)
    stop_evt = threading.Event()
    threading.Thread(target=command_poller,
                     args=(bed, state_info, stop_evt),
                     daemon=True).start()

    print(f"=== ESP simulator for {bed} (raw-TCP only) ===")
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

    except KeyboardInterrupt:
        pass
    finally:
        stop_evt.set()
        print("\n=== Encerrando ESP simulator ===")

if __name__ == "__main__":
    main()

#cama esquerda - HRP004201693 - 1
#cama direita  - HRP000005656 - 2