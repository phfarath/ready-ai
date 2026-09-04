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
}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence test output
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
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
    """Start the fixture server on a free port. Returns (server, thread, base_url)."""
    server = ThreadingHTTPServer((host, port), _Handler)
    base_url = f"http://{host}:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, base_url


def stop_server(server: ThreadingHTTPServer) -> None:
    server.shutdown()
    server.server_close()
