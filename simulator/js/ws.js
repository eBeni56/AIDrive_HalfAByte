// WebSocket client (connect / emit). Every outgoing message is printed to the browser console (F12).

let ws = null;
let connected = null;   // last logged connection state; null = nothing logged yet

// Log the connection state only when it changes, so a server that is down does not spam the
// console with a line every RECONNECT_MS.
function setConn(up) {
  if (up === connected) return;
  connected = up;
  if (up) console.log(`[ws] connected to ${WS_URL}`);
  else console.warn(`[ws] not connected to ${WS_URL}, retrying every ${RECONNECT_MS / 1000} s`);
}

function connect() {
  try {
    ws = new WebSocket(WS_URL);
    ws.onopen = () => setConn(true);
    ws.onclose = () => { setConn(false); setTimeout(connect, RECONNECT_MS); };
    ws.onerror = () => {};   // onclose always follows; nothing to do here
  } catch (e) {
    setConn(false);
    setTimeout(connect, RECONNECT_MS);
  }
}

// Send an event and print it to the console. Never throws; if the socket is down the message is
// lost and printed as a "NOT SENT" warning. Returns whether it was sent.
function emit(ev) {
  const text = JSON.stringify(ev);
  let sent = false;
  try {
    if (ws && ws.readyState === WebSocket.OPEN) { ws.send(text); sent = true; }
  } catch (e) { sent = false; }
  if (sent) console.log(text);
  else console.warn("NOT SENT " + text);
  return sent;
}
