"""Local E2E fixture server (stdlib only, no creds, no external network).

Serves the Fase 1 slice-1 pages over 127.0.0.1:
- ``/``       index with links
- ``/healthz`` liveness probe (``ok``)
- ``/spa``     client-side navigation via history.pushState
- ``/shadow``   open Shadow DOM card + light-DOM mirror of its state

The shadow page is deliberately honest about the current engine limit:
``document.querySelector`` does not pierce shadow roots, so the shadow
button itself is *discoverable* (via ``get_interactive_elements``, which
traverses shadow roots) but actuated through the light-DOM mirror button
``#shadow-mirror-btn``. The mirror handler updates both the shadow status
and ``#shadow-status-mirror``, which is what flow asserts check.
Direct shadow-root clicking is Fase 2 work (T-6 precise core).
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

INDEX_HTML = """<!doctype html><html><body>
<h1>ready-ai e2e fixtures</h1>
<ul><li><a href="/spa">SPA</a></li><li><a href="/shadow">Shadow DOM</a></li></ul>
</body></html>"""

SPA_HTML = """<!doctype html><html><body>
<h1>SPA fixture</h1>
<nav><button id="nav-home">Home</button><button id="nav-products">Products</button></nav>
<div id="spa-status">Home</div>
<script>
document.getElementById('nav-products').addEventListener('click', () => {
  history.pushState({}, '', '/spa/products');
  document.getElementById('spa-status').textContent = 'Products';
});
document.getElementById('nav-home').addEventListener('click', () => {
  history.pushState({}, '', '/spa');
  document.getElementById('spa-status').textContent = 'Home';
});
</script>
</body></html>"""

SHADOW_HTML = """<!doctype html><html><body>
<h1>Shadow DOM fixture</h1>
<my-card></my-card>
<button id="shadow-mirror-btn">Toggle shadow</button>
<div id="shadow-status-mirror">off</div>
<script>
class MyCard extends HTMLElement {
  constructor() {
    super();
    const root = this.attachShadow({ mode: 'open' });
    root.innerHTML = `<button id="shadow-btn">shadow toggle</button>
      <span id="shadow-status">off</span>
      <input id="shadow-input" value="" />`;
    root.getElementById('shadow-btn').addEventListener('click', () => {
      const s = root.getElementById('shadow-status');
      s.textContent = s.textContent === 'off' ? 'on' : 'off';
    });
  }
}
customElements.define('my-card', MyCard);
document.getElementById('shadow-mirror-btn').addEventListener('click', () => {
  const card = document.querySelector('my-card');
  card.shadowRoot.getElementById('shadow-btn').click();
  const state = card.shadowRoot.getElementById('shadow-status').textContent;
  document.getElementById('shadow-status-mirror').textContent = state;
});
</script>
</body></html>"""

_ROUTES = {
    "/": INDEX_HTML,
    "/healthz": "ok",
    "/spa": SPA_HTML,
    "/spa/products": SPA_HTML,
    "/shadow": SHADOW_HTML,
    "/landing": """<!doctype html><html><body>
<h1>Landing</h1>
<div id="landing-status">Welcome</div>
</body></html>""",
    "/inner": """<!doctype html><html><body>
<button id="inner-btn">inner action</button>
<span id="inner-status">idle</span>
<script>
document.getElementById('inner-btn').addEventListener('click', () => {
  document.getElementById('inner-status').textContent = 'done';
});
</script>
</body></html>""",
    "/xframe": """<!doctype html><html><body>
<button id="xframe-btn">xframe action</button>
<span id="xframe-status">idle</span>
<script>
window.addEventListener('message', (ev) => {
  if (ev.data === 'toggle') {
    const s = document.getElementById('xframe-status');
    s.textContent = s.textContent === 'idle' ? 'toggled' : 'idle';
    parent.postMessage('xframe:' + s.textContent, '*');
  }
});
</script>
</body></html>""",
    "/popup-opener": """<!doctype html><html><body>
<h1>Popup opener</h1>
<button id="opener-btn">Open popup</button>
<div id="popup-status">closed</div>
<script>
// NOTE: no window.open here on purpose. A real window.open hijacks the
// engine session (recv-loop replaces _session_id on every
// Target.attachedToTarget page event) — that is T-6 TargetRegistry work.
// The opener half is exercised here; /popup is driven via direct
// navigation in the same flow.
document.getElementById('opener-btn').addEventListener('click', () => {
  document.getElementById('popup-status').textContent = 'opened';
});
</script>
</body></html>""",
    "/popup": """<!doctype html><html><body>
<h1 id="popup-title">Popup page</h1>
</body></html>""",
    "/dialog": """<!doctype html><html><body>
<h1>Dialog fixture (custom modal — native alert/confirm is T-7 scope)</h1>
<button id="open-modal">Delete item</button>
<div id="modal" style="display:none">
  <p>Are you sure?</p>
  <button id="modal-accept">Accept</button>
  <button id="modal-dismiss">Dismiss</button>
</div>
<div id="dialog-result">pending</div>
<script>
document.getElementById('open-modal').addEventListener('click', () => {
  document.getElementById('modal').style.display = 'block';
});
document.getElementById('modal-accept').addEventListener('click', () => {
  document.getElementById('modal').style.display = 'none';
  document.getElementById('dialog-result').textContent = 'accepted';
});
document.getElementById('modal-dismiss').addEventListener('click', () => {
  document.getElementById('modal').style.display = 'none';
  document.getElementById('dialog-result').textContent = 'dismissed';
});
</script>
</body></html>""",
    "/downloads": """<!doctype html><html><body>
<h1>Downloads fixture</h1>
<a id="dl-link" href="/files/report.csv" download>Download report</a>
</body></html>""",
    "/login": """<!doctype html><html><body>
<h1>Login fixture (no backend — fields only)</h1>
<input id="login-email" name="email" autocomplete="email" />
<input id="login-pass" name="password" type="password" autocomplete="current-password" />
<button id="login-submit">Sign in</button>
</body></html>""",
}

# Binary-ish payloads served with explicit headers (not in _ROUTES).
_FILES = {
    "/files/report.csv": (
        "text/csv; charset=utf-8",
        'id,name\n1,alice\n2,bob\n',
    ),
}

# Redirects must bypass the static map (302, no body).
_REDIRECTS = {
    "/redirect": "/landing",
}


def _iframe_html(peer_base: str) -> str:
    """Parent page: same-origin iframe + cross-origin iframe + sync mirror.

    The mirror button sets ``#iframe-status-mirror`` synchronously (what the
    flow asserts) and *also* postMessages the cross-origin frame. The reply
    lands in a separate ``#iframe-reply-log`` div on purpose: sharing the
    mirror would race the assert (reply arriving first flips the text).
    """
    return f"""<!doctype html><html><body>
<h1>Iframe fixture</h1>
<iframe id="same-frame" src="/inner"></iframe>
<iframe id="x-frame" src="{peer_base}/xframe"></iframe>
<button id="iframe-mirror-btn">Ping xframe</button>
<div id="iframe-status-mirror">idle</div>
<div id="iframe-reply-log">none</div>
<script>
const xframe = document.getElementById('x-frame');
window.addEventListener('message', (ev) => {{
  if (typeof ev.data === 'string' && ev.data.startsWith('xframe:')) {{
    document.getElementById('iframe-reply-log').textContent = ev.data;
  }}
}});
document.getElementById('iframe-mirror-btn').addEventListener('click', () => {{
  document.getElementById('iframe-status-mirror').textContent = 'pinged';
  xframe.contentWindow.postMessage('toggle', '*');
}});
</script>
</body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence test output
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in _REDIRECTS:
            self.send_response(302)
            self.send_header("Location", _REDIRECTS[path])
            self.end_headers()
            return
        if path in _FILES:
            content_type, text = _FILES[path]
            raw = text.encode()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{path.rsplit("/", 1)[-1]}"',
            )
            self.end_headers()
            self.wfile.write(raw)
            return
        if path == "/iframe":
            body = _iframe_html(getattr(self.server, "peer_base", ""))
        else:
            body = _ROUTES.get(path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        raw = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def start_server(host: str = "127.0.0.1", port: int = 0) -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    """Start the fixture server on a free port. Returns (server, thread, base_url).

    ``server.peer_base`` is a writable slot for the cross-origin peer URL
    (used only by ``/iframe``); conftest wires the two servers together.
    """
    server = ThreadingHTTPServer((host, port), _Handler)
    server.peer_base = ""  # type: ignore[attr-defined]
    base_url = f"http://{host}:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, base_url


def stop_server(server: ThreadingHTTPServer) -> None:
    server.shutdown()
    server.server_close()
