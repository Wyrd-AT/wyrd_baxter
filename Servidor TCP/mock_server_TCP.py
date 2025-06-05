import socket
import threading
import json
from datetime import datetime, timezone
from flask import Flask, request, jsonify, render_template_string

# ============ SHARED STATE ============
rooms      = { str(i): None for i in range(401, 410) }
bed_list   = ["HRP004201693", "HRP000005656"]
bed_states = { bed: "OUT" for bed in bed_list }
pending    = {}  

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

# ============ RAW-TCP SERVER (porta 9500) ============
def handle_tcp_client(conn, addr):
    try:
        data = b''
        # lê até '\n'
        while b'\n' not in data:
            chunk = conn.recv(1024)
            if not chunk:
                break
            data += chunk
        if not data:
            return

        msg = json.loads(data.decode().strip())

        # 1) State update da ESP
        if 'state' in msg:
            bed   = msg['bed']
            room  = msg.get('room')
            state = msg['state']

            for r,b in rooms.items():
                if b == bed:
                    rooms[r] = None
            if state in ('IN','ON') and room in rooms:
                rooms[room] = bed
            bed_states[bed] = state

            resp = {"room":room,"bed":bed,"state":state,"status":"300"}
            conn.sendall((json.dumps(resp)+"\n").encode())

        # 2) Poll de comando da ESP
        elif 'bed' in msg and len(msg)==1:
            bed = msg['bed']
            cmd = pending.pop(bed, None)
            if cmd:
                resp = {
                    "bed":    bed,
                    "action": cmd['action'],
                    "dataOn": cmd['dataOn']
                }
                conn.sendall((json.dumps(resp)+"\n").encode())

        # 3) Comando vindo do HTTP (/command)
        elif 'action' in msg:
            bed    = msg['bed']
            action = msg['action']
            ts     = msg.get('dataOn', now_iso())
            pending[bed] = {"action": action, "dataOn": ts}

            resp = {"status":"queued","bed":bed,"action":action,"dataOn":ts}
            conn.sendall((json.dumps(resp)+"\n").encode())

        else:
            resp = {"error":"invalid payload"}
            conn.sendall((json.dumps(resp)+"\n").encode())

    except Exception as e:
        print("TCP handler error:", e)
    finally:
        conn.close()

def tcp_server():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("10.0.0.149", 9500))
    srv.listen(5)
    print("📡 Raw-TCP server listening on port 9500")
    while True:
        conn, addr = srv.accept()
        threading.Thread(target=handle_tcp_client,
                         args=(conn, addr),
                         daemon=True).start()

threading.Thread(target=tcp_server, daemon=True).start()


# ============ HTTP/FLASK SERVER (porta 9501) ============
app = Flask(__name__)

TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <title>Servidor de Camas</title>
  <style>
    .cell-on  { background-color: lightgreen; }
    .cell-in  { background-color: lightcoral; }
    .cell-off { background-color: transparent; }
    table { border-collapse: collapse; margin-top: 1em; width: 100%; }
    th, td { border: 1px solid #666; padding: 0.5em; text-align: center; }
    button { margin: 0 0.2em; }
  </style>
</head>
<body>
  <h1>Servidor de Camas</h1>

  <h2>Tabela de Camas</h2>
  <table>
    <thead>
      <tr>
        <th>Cama</th><th>Estado</th><th>Quarto</th><th>Ações</th>
      </tr>
    </thead>
    <tbody id="beds-body"></tbody>
  </table>

  <h2>Mapa de Quartos</h2>
  <table>
    <tr>
      {% for r in rooms.keys() %}<th>{{r}}</th>{% endfor %}
    </tr>
    <tr id="map-row"></tr>
  </table>

  <script>
    const bedList = {{ bed_list|tojson }};
    function isoTs(){ return new Date().toISOString(); }

    function sendCommand(bed, action){
      fetch('/command', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ bed, action, dataOn: isoTs() })
      })
      .then(_ => refreshAll());
    }

    function refreshAll(){
      fetch('/occupancy')
        .then(r => r.json())
        .then(data => {
          const { rooms, bed_states } = data;
          // tabela de camas
          const tbody = document.getElementById('beds-body');
          tbody.innerHTML = '';
          bedList.forEach(bed => {
            const state = bed_states[bed];
            const roomEntry = Object.entries(rooms).find(([r,b])=>b===bed);
            const room = roomEntry ? roomEntry[0] : null;
            const tr = document.createElement('tr');
            tr.innerHTML = `
              <td>${bed}</td>
              <td>${state}</td>
              <td>${room||'—'}</td>
              <td>
                <button onclick="sendCommand('${bed}','turnon')">ON</button>
                <button onclick="sendCommand('${bed}','turnoff')">OFF</button>
              </td>`;
            tbody.appendChild(tr);
          });
          // mapa de quartos
          const mapRow = document.getElementById('map-row');
          mapRow.innerHTML = '';
          Object.keys(rooms).forEach(room => {
            const td = document.createElement('td');
            const bed = rooms[room];
            if(bed){
              const st = bed_states[bed];
              td.textContent = bed;
              td.className = st==='ON' ? 'cell-on'
                           : st==='IN'? 'cell-in'
                                      : 'cell-off';
            } else {
              td.textContent = '—';
              td.className = 'cell-off';
            }
            mapRow.appendChild(td);
          });
        });
    }

    window.addEventListener('load', refreshAll);
    setInterval(refreshAll, 5000);
  </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(TEMPLATE, rooms=rooms, bed_list=bed_list)

@app.route('/occupancy', methods=['GET'])
def occupancy():
    return jsonify(rooms=rooms, bed_states=bed_states)

@app.route('/command', methods=['POST'])
def post_command():
    data   = request.get_json() or {}
    bed    = data.get('bed')
    action = data.get('action')
    ts     = data.get('dataOn', now_iso())

    cmd_msg = {"bed":bed, "action":action, "dataOn":ts}
    try:
        sock = socket.create_connection(("127.0.0.1", 9500), timeout=2)
        sock.sendall((json.dumps(cmd_msg)+"\n").encode())
        _ = sock.recv(1024)
        sock.close()
        return jsonify(status="queued", bed=bed, action=action, dataOn=ts)
    except Exception as e:
        return jsonify(error=str(e)), 500

if __name__ == '__main__':
    threading.Thread(target=tcp_server, daemon=True).start()
    app.run(host='0.0.0.0', port=9501, debug=True, use_reloader=False)
