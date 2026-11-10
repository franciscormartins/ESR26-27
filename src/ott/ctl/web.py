"""Painel web do ``ottctl``: serve uma página que lê o estado dos nós e chama o orquestrador.

Só escuta em 127.0.0.1. Como as ações correm no CORE como root dentro dos nós, o servidor recusa pedidos cujo
``Host`` não seja local (DNS rebinding) e só aceita ``POST`` com ``Content-Type: application/json`` (uma página
de outro sítio não consegue enviar isso sem o consentimento do servidor).
"""
import collections
import errno
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import scenarios as sc
from .backend import BackendError, CoreBackend
from .control import Orchestrator, build
from .plan import PlanError, available, load_plan

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
    if action == "clear_network":
        orch.clear_network()
        return "rede reposta"
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


class Controller:
    """O que o painel controla: o cenário escolhido, os testes em curso e a demonstração guiada."""

    def __init__(self, orch=None, backend="local", port=5000, data_port=6000, runner=None, session_base="/tmp",
                 rundir=None):
        self.rundir = rundir
        self.orch = orch
        self.backend_name = orch.backend.name if orch else backend
        self.port, self.data_port, self.runner, self.session_base = port, data_port, runner, session_base
        self._lock = threading.RLock()
        self.busy = None
        self._stop = False
        self._scen_cache = (0.0, [])
        self.run = {"running": False, "current": None, "ids": [], "repeat": 1, "results": [], "started": None,
                    "finished": None}
        self.demo = {"scenario": None, "steps": [], "index": -1, "state": "idle", "log": []}
        self.history = collections.deque(maxlen=600)   # uma amostra por segundo, para os gráficos
        self._closing = threading.Event()
        threading.Thread(target=self._sample_loop, daemon=True, name="ottctl-history").start()

    # -- histórico para os gráficos -------------------------------------

    @staticmethod
    def sample(state):
        """Amostra compacta: imagens/s e jitter de cada fluxo recebido e RTT de cada vizinho."""
        rates, jitter, rtts = {}, {}, {}
        for node in state["nodes"].values():
            st = node["status"]
            if not st or node["health"] != "up":
                continue
            for f in st.get("flows", []):
                if f["active"]:
                    rates[f"{node['id']}|{f['origin']}:{f['flow']}"] = f["rate"]
                    jitter[f"{node['id']}|{f['origin']}:{f['flow']}"] = f["jitter_ms"]
            for n in st.get("neighbors", []):
                if n["rtt_ms"] is not None:
                    rtts[f"{node['id']}|{n['id']}"] = n["rtt_ms"]
        return {"t": state["time"], "rate": rates, "jit": jitter, "rtt": rtts}

    def _sample_loop(self):
        while not self._closing.wait(1.0):
            orch = self.orch
            if orch is None:
                continue
            try:
                self.history.append(self.sample(orch.state()))
            except Exception:   # um ficheiro a meio de ser escrito não pode parar o histórico
                pass

    def samples_since(self, since):
        return [s for s in list(self.history) if s["t"] > since]

    # -- cenários -------------------------------------------------------

    def scenarios(self):
        """Cenários com plano e, para cada um, se há uma sessão do CORE aberta com os seus nós."""
        now = time.time()
        if now - self._scen_cache[0] < 3:
            return self._scen_cache[1]
        out = []
        for name in available():
            try:
                plan = load_plan(name)
            except PlanError:
                continue
            try:
                core = CoreBackend(plan, base=self.session_base, runner=self.runner).session
            except BackendError:
                core = None
            out.append({"name": name, "nodes": len(plan.nodes), "servers": [n.id for n in plan.nodes.values() if n.serve],
                        "core_session": core})
        self._scen_cache = (now, out)
        return out

    def select(self, scenario, backend="local", session=None):
        with self._lock:
            if self.busy:
                raise BackendError(f"ocupado: {self.busy}")
            if scenario not in available():
                raise BackendError(f"cenário desconhecido: {scenario}")
            if backend not in ("local", "core"):
                raise BackendError(f"modo desconhecido: {backend}")
            orch = None
            try:   # retoma um cenário já lançado neste modo
                orch = Orchestrator.attach(scenario, self.rundir, runner=self.runner)
                if orch.backend.name != backend:
                    orch = None
            except (BackendError, PlanError):
                orch = None
            if orch is None:
                orch = build(scenario, backend, session, self.port, self.data_port, self.rundir, runner=self.runner)
            self.orch, self.backend_name = orch, backend
            self.run = {"running": False, "current": None, "ids": [], "repeat": 1, "results": [], "started": None,
                        "finished": None}
            self.demo = {"scenario": scenario, "steps": [], "index": -1, "state": "idle", "log": []}
            self.history.clear()
            return f"cenário {scenario} ({backend})"

    # -- trabalho em segundo plano -------------------------------------

    def _background(self, label, fn):
        with self._lock:
            if self.busy:
                raise BackendError(f"ocupado: {self.busy}")
            self.busy = label
            self._stop = False

        def work():
            try:
                fn()
            finally:
                with self._lock:
                    self.busy = None
        threading.Thread(target=work, daemon=True, name="ottctl-work").start()

    # -- testes ----------------------------------------------------------

    def tests(self):
        if not self.orch:
            return {"tests": [], "run": self.run}
        run = dict(self.run)
        run["summary"] = sc.summarize(run["results"])
        run["markdown"] = sc.render_markdown(run["results"]) if run["results"] else ""
        return {"tests": sc.tests_for(self.orch.plan, self.orch.backend.name), "run": run}

    def run_tests(self, ids, repeat):
        if not self.orch:
            raise BackendError("escolhe primeiro um cenário")
        repeat = max(1, min(int(repeat or 1), 50))
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise BackendError("testes inválidos")
        orch = self.orch
        self.run = {"running": True, "current": None, "ids": ids, "repeat": repeat, "results": [],
                    "started": time.time(), "finished": None}

        def work():
            try:
                sc.run_tests(orch, ids or None, repeat, on_result=self.run["results"].append,
                             should_stop=lambda: self._stop,
                             on_start=lambda t, rep: self.run.update(current={"id": t["id"], "title": t["title"], "repeat": rep}))
            finally:
                self.run.update(running=False, current=None, finished=time.time())
        self._background("a correr testes", work)
        return f"{len(ids) or 'todos os'} testes × {repeat}"

    def stop_tests(self):
        self._stop = True
        return "a parar depois do teste em curso"

    # -- demonstração ------------------------------------------------------

    def demo_status(self):
        if not self.orch:
            return self.demo
        d = self.demo
        steps = sc.demo_for(self.orch.plan)
        d["steps"] = [{"title": s["title"], "core": s["core"], "flow": s["flow"], "focus": s["focus"], "text": s["text"]}
                      for s in steps]
        return d

    def demo_op(self, op):
        if not self.orch:
            raise BackendError("escolhe primeiro um cenário")
        steps = sc.demo_for(self.orch.plan)
        d = self.demo
        if op == "reset":
            if self.busy:
                raise BackendError(f"ocupado: {self.busy}")
            d.update(index=-1, state="idle", log=[])
            return "demonstração reposta"
        if op == "prev":
            d["index"] = max(-1, d["index"] - 1)
            d["state"] = "idle"
            return "passo anterior"
        if op != "next":
            raise BackendError(f"operação desconhecida: {op!r}")
        if d["index"] + 1 >= len(steps):
            d["state"] = "done"
            return "fim da demonstração"
        d["index"] += 1
        index, step, orch = d["index"], steps[d["index"]], self.orch
        d["state"] = "running"
        if step["run"] is None or (step["core"] and orch.backend.name != "core"):
            d["state"] = "idle" if index + 1 < len(steps) else "done"
            d["log"] = d["log"][:index] + [""] * (index - len(d["log"])) + [sc.run_demo_step(orch, step)]
            return step["title"]

        def work():
            try:
                extra = sc.run_demo_step(orch, step)
            except Exception as exc:   # um passo com erro não derruba o painel
                extra = f"(erro: {exc})"
            d["log"] = d["log"][:index] + [""] * (index - len(d["log"])) + [extra]
            d["state"] = "idle" if index + 1 < len(steps) else "done"
        self._background(f"demonstração: {step['title']}", work)
        return step["title"]

    # -- estado -------------------------------------------------------------

    def state(self):
        if not self.orch:
            return {"scenario": None, "scenarios": self.scenarios(), "busy": self.busy}
        s = self.orch.state()
        s["scenarios"] = self.scenarios()
        s["busy"] = self.busy
        return s


def make_handler(controller):
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
            c = controller
            if self.path in ("/", "/index.html"):
                with open(PAGE, "rb") as f:
                    return self._send(200, f.read(), "text/html")
            if self.path == "/api/state":
                return self._send(200, c.state())
            if self.path == "/api/underlay":
                return self._send(200, c.orch.underlay() if c.orch else {"nodes": [], "links": []})
            if self.path.startswith("/api/history"):
                try:
                    since = float(self.path.partition("since=")[2] or 0)
                except ValueError:
                    since = 0.0
                return self._send(200, {"samples": c.samples_since(since)})
            if self.path == "/api/tests":
                return self._send(200, c.tests())
            if self.path == "/api/demo":
                return self._send(200, c.demo_status())
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
                message = self.dispatch(body)
            except (BackendError, PlanError) as exc:
                return self._send(400, {"ok": False, "error": str(exc)})
            self._send(200, {"ok": True, "message": message})

        def dispatch(self, body):
            c, action = controller, body.get("action")
            if action == "select":
                session = body.get("session")
                return c.select(str(body.get("scenario")), str(body.get("backend") or "local"),
                                int(session) if str(session or "").isdigit() else None)
            if action == "tests_run":
                return c.run_tests(body.get("ids") or [], body.get("repeat"))
            if action == "tests_stop":
                return c.stop_tests()
            if action == "demo":
                return c.demo_op(str(body.get("op")))
            if not c.orch:
                raise BackendError("escolhe primeiro um cenário")
            if c.busy and action in ("up", "down", "start", "stop", "kill", "freeze", "thaw", "netem", "netem_clear",
                                     "link_netem", "link_clear", "link_set", "link_state", "clear_network"):
                raise BackendError(f"ocupado: {c.busy} (espera ou pára)")
            return do_action(c.orch, body)

    return Handler


def make_server(target, port=8080):
    """``target``: um Orchestrator, um Controller ou None (o painel pede para escolher o cenário)."""
    controller = target if isinstance(target, Controller) else Controller(target)
    server = ThreadingHTTPServer(("127.0.0.1", port), None)
    server.RequestHandlerClass = make_handler(controller)
    server.controller = controller
    return server


def serve(target=None, port=8080):
    for attempt in range(20):   # porta ocupada (ex.: outro programa na 8080): tenta a seguinte
        try:
            server = make_server(target, port + attempt if port else 0)
            break
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE or attempt == 19:
                raise
            print(f"porta {port + attempt} ocupada, a tentar a {port + attempt + 1}")
    name = server.controller.orch.plan.name if server.controller.orch else "(escolhe o cenário na página)"
    print(f"painel de {name} em http://127.0.0.1:{server.server_address[1]}  (Ctrl+C para sair)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.controller._closing.set()
        server.server_close()
