from flask import Flask, render_template_string
from flask_socketio import SocketIO, emit
from datetime import datetime, timezone

# === estado compartilhado ===
rooms      = { str(i): None for i in range(401, 410) }
bed_list   = ["HRP004201693", "HRP000005656"]
bed_states = { bed: "OUT" for bed in bed_list }
pending    = {}

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

# === Flask + Socket.IO ===
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# HTML/JS básico para browser (inclui cliente Socket.IO)
TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <title>Servidor de Camas (WS)</title>
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
  <h1>Servidor de Camas (WebSockets)</h1>
  <h2>Tabela de Camas</h2>
  <table>
    <thead>
      <tr><th>Cama</th><th>Estado</th><th>Quarto</th><th>Ações</th></tr>
    </thead>
    <tbody id="beds-body"></tbody>
  </table>

  <h2>Mapa de Quartos</h2>
  <table>
    <tr>{% for r in rooms.keys() %}<th>{{r}}</th>{% endfor %}</tr>
    <tr id="map-row"></tr>
  </table>

  <script src="https://cdn.socket.io/4.5.1/socket.io.min.js"></script>
  <script>
    const socket = io();

    const bedList = {{ bed_list|tojson }};
    function updateUI(rooms, bed_states) {
      // tabela de camas
      const tbody = document.getElementById('beds-body');
      tbody.innerHTML = '';
      bedList.forEach(bed => {
        const state = bed_states[bed];
        const roomEntry = Object.entries(rooms).find(([r,b])=>b===bed);
        const room = roomEntry ? roomEntry[0] : '—';
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td>${bed}</td>
          <td>${state}</td>
          <td>${room}</td>
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
        if (bed) {
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
    }

    socket.on('connect', () => {
      console.log('Conectado ao servidor WS');
    });

    // sempre que o servidor emitir um update de estado
    socket.on('state_update', data => {
      updateUI(data.rooms, data.bed_states);
    });

    // ack de comando (opcional)
    socket.on('command_ack', data => {
      console.log('Comando enfileirado:', data);
    });

    function sendCommand(bed, action) {
      socket.emit('command', {
        bed, 
        action, 
        dataOn: new Date().toISOString()
      });
    }
  </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(TEMPLATE,
                                  rooms=rooms,
                                  bed_list=bed_list)

def broadcast_state():
    socketio.emit('state_update', {
        'rooms': rooms,
        'bed_states': bed_states
    })

@socketio.on('connect')
def on_connect():
    # logo que o cliente conecta, envia o estado atual
    broadcast_state()

@socketio.on('state')
def on_state(msg):
    # msg: {bed, room, state, dataOn}
    bed   = msg.get('bed')
    room  = msg.get('room')
    state = msg.get('state')
    # atualiza rooms e bed_states
    for r,b in list(rooms.items()):
        if b == bed:
            rooms[r] = None
    if state in ('IN','ON') and room in rooms:
        rooms[room] = bed
    bed_states[bed] = state
    # broadcast
    broadcast_state()
    # opcional: enviar ack
    emit('state_ack', {'bed':bed,'state':state})

@socketio.on('command')
def on_command(msg):
    # msg: {bed, action, dataOn}
    bed    = msg.get('bed')
    action = msg.get('action')
    ts     = msg.get('dataOn', now_iso())
    # armazena pendente (se ainda quiser usar fila)
    pending[bed] = {'action':action,'dataOn':ts}
    # notifica quem quiser: ack para quem enviou
    emit('command_ack', {'bed':bed,'action':action,'dataOn':ts})
    # e broadcast do comando a todos (por exemplo, ESP clients)
    socketio.emit('command', {'bed':bed,'action':action,'dataOn':ts})
    # também podemos rebroadcastar o estado atual
    broadcast_state()

if __name__ == '__main__':
    # threaded para suportar múltiplos clientes simultâneos
    socketio.run(app, host='0.0.0.0', port=9500)
