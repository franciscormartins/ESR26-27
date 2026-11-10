"""Painel web do ``ottctl``: serve uma página que lê o estado dos nós e chama o orquestrador.

Só escuta em 127.0.0.1. Como as ações correm no CORE como root dentro dos nós, o servidor recusa pedidos cujo
``Host`` não seja local (DNS rebinding) e só aceita ``POST`` com ``Content-Type: application/json`` (uma página
de outro sítio não consegue enviar isso sem o consentimento do servidor).
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .backend import BackendError

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
NODE_ACTIONS = ("start", "stop", "kill", "freeze", "thaw")
MAX_BODY = 4096


def _text(value):
    return None if value in (None, "") else str(value)


def do_action(orch, body):
    """Executa uma ação pedida pelo painel; devolve uma mensagem curta. Levanta BackendError se for inválida."""
    action = body.get("action")
    nodes = body.get("nodes") or ([body["node"]] if body.get("node") else [])
    if not isinstance(nodes, list) or not all(isinstance(n, str) for n in nodes):
        raise BackendError("nós inválidos")
    params = {k: _text(body.get(k)) for k in ("delay", "jitter", "loss", "rate")}
    if action in NODE_ACTIONS:
        if not nodes:
            raise BackendError("indica o nó")
        getattr(orch, action)(nodes)
        return f"{action}: {', '.join(nodes)}"
    if action == "up":
        ids = orch.up(spare=bool(body.get("spare")))
        return f"{len(ids)} nós lançados"
    if action == "down":
        orch.down()
        return "nós parados"
    if action in ("netem", "netem_clear", "link_state"):
        node, iface = _text(body.get("node")), _text(body.get("iface"))
        if not node or not iface:
            raise BackendError("indica o nó e a interface")
        if action == "netem":
            orch.netem(node, iface, **params)
        elif action == "netem_clear":
            orch.netem_clear(node, iface)
        else:
            orch.link_state(node, iface, bool(body.get("up")))
        return f"{action}: {node} {iface}"
    if action in ("link_netem", "link_clear", "link_set"):
        ends = [_text(body.get(k)) for k in ("a", "ia", "b", "ib")]
        if not all(ends):
            raise BackendError("indica os dois extremos da ligação")
        if action == "link_netem":
            orch.link_netem(*ends, **params)
        elif action == "link_clear":
            orch.link_clear(*ends)
        else:
            orch.link_set(*ends, bool(body.get("up")))
        return f"{action}: {ends[0]}:{ends[1]} ↔ {ends[2]}:{ends[3]}"
    raise BackendError(f"ação desconhecida: {action!r}")


def make_handler(orch, port_getter):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ottctl"

        def log_message(self, *args):   # sem ruído no terminal
            pass

        def _host_ok(self):
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            return host in ("127.0.0.1", "localhost", "::1")

        def _send(self, code, payload, ctype="application/json"):
            raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, {"error": "host não permitido"})
            if self.path in ("/", "/index.html"):
                with open(PAGE, "rb") as f:
                    return self._send(200, f.read(), "text/html")
            if self.path == "/api/state":
                return self._send(200, orch.state())
            if self.path == "/api/underlay":
                return self._send(200, orch.underlay())
            self._send(404, {"error": "não existe"})

        def do_POST(self):
            if not self._host_ok():
                return self._send(403, {"error": "host não permitido"})
            if self.path != "/api/action":
                return self._send(404, {"error": "não existe"})
            if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
                return self._send(415, {"error": "o pedido tem de ser application/json"})
            try:
                size = int(self.headers.get("Content-Length") or 0)
                if not 0 < size <= MAX_BODY:
                    raise ValueError
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._send(400, {"ok": False, "error": "pedido inválido"})
            try:
                message = do_action(orch, body)
            except BackendError as exc:
                return self._send(400, {"ok": False, "error": str(exc)})
            self._send(200, {"ok": True, "message": message})

    return Handler


def make_server(orch, port=8080):
    server = ThreadingHTTPServer(("127.0.0.1", port), None)
    server.RequestHandlerClass = make_handler(orch, lambda: server.server_address[1])
    return server


def serve(orch, port=8080):
    server = make_server(orch, port)
    print(f"painel de {orch.plan.name} em http://127.0.0.1:{server.server_address[1]}  (Ctrl+C para sair)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
