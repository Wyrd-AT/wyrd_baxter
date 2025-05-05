import threading
import json
from datetime import datetime, timezone
from flask import Flask, request, jsonify, render_template_string

# === estado compartilhado ===
rooms      = { str(i): None for i in range(401, 410) }
bed_list   = ["HRP004201693", "HRP000005656"]
bed_states = { bed: "OUT" for bed in bed_list }
pending    = {}

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

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

# 1) rota do browser para enfileirar comando (já existia)
@app.route('/command', methods=['POST'])
def post_command():
    data   = request.get_json() or {}
    bed    = data.get('bed')
    action = data.get('action')
    ts     = data.get('dataOn', now_iso())

    pending[bed] = {"action": action, "dataOn": ts}
    return jsonify(status="queued", bed=bed, action=action, dataOn=ts)

# 2) rota nova: ESP envia estado
@app.route('/state', methods=['POST'])
def post_state():
    msg   = request.get_json() or {}
    bed   = msg.get('bed')
    room  = msg.get('room')
    state = msg.get('state')

    # limpa qualquer cama igual em rooms
    for r,b in list(rooms.items()):
        if b == bed:
            rooms[r] = None

    # atualiza occupancy
    if state in ('IN','ON') and room in rooms:
        rooms[room] = bed
    bed_states[bed] = state

    return jsonify(room=room, bed=bed, state=state, status="300")

# 3) rota nova: ESP faz polling de comando
@app.route('/poll', methods=['POST'])
def post_poll():
    data = request.get_json() or {}
    bed  = data.get('bed')
    cmd  = pending.pop(bed, None)
    if not cmd:
        # sem comando pendente
        return jsonify({}), 204
    return jsonify(bed=bed, action=cmd['action'], dataOn=cmd['dataOn'])

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=9500, debug=True)
