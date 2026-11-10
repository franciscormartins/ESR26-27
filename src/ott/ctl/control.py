"""Orquestrador de um cenário: arranca e pára nós, injeta falhas e junta o estado de todos numa só vista."""
import json
import os
import tempfile
import threading
import time

from .backend import BackendError, CoreBackend, LocalBackend, netem_command
from .plan import load_plan, underlay_of

STALE_AFTER = 3.0   # segundos sem o nó atualizar o ficheiro de estado
EVENTS_KEPT = 300


class Orchestrator:
    def __init__(self, plan, backend, rundir=None, verbose=False):
        self.plan = plan
        self.backend = backend
        self.rundir = rundir or os.path.join(tempfile.gettempdir(), "ottctl", plan.name)
        self.verbose = verbose
        self.flags = {}          # id -> {"frozen": bool, "killed": bool, "stopped": bool}
        self._lock = threading.RLock()
        self._underlay = None
        os.makedirs(self.rundir, exist_ok=True)

    # -- caminhos e estado em disco -----------------------------------

    def status_path(self, node_id):
        return os.path.join(self.rundir, f"{node_id}.json")

    def log_path(self, node_id):
        return os.path.join(self.rundir, f"{node_id}.log")

    @property
    def state_path(self):
        return os.path.join(self.rundir, "state.json")

    def save(self, started=None):
        meta = {"scenario": self.plan.name, "backend": self.backend.name, "verbose": self.verbose, "flags": self.flags,
                "pids": getattr(self.backend, "pids", {}), "started": started or self.load().get("started")}
        if isinstance(self.backend, CoreBackend):
            meta["session"] = self.backend.session
        if isinstance(self.backend, LocalBackend):
            meta.update(port=self.backend.port, data_port=self.backend.data_port)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(meta, f)
        os.replace(tmp, self.state_path)

    def load(self):
        try:
            with open(self.state_path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    @classmethod
    def attach(cls, scenario, rundir=None, runner=None):
        """Liga-se a um cenário já lançado (por outro processo), com o modo guardado em ``state.json``."""
        plan = load_plan(scenario)
        rundir = rundir or os.path.join(tempfile.gettempdir(), "ottctl", plan.name)
        try:
            with open(os.path.join(rundir, "state.json")) as f:
                meta = json.load(f)
        except (OSError, ValueError):
            raise BackendError(f"o cenário {plan.name} não está lançado (faz primeiro: ottctl up {plan.name})") from None
        if meta.get("backend") == "core":
            backend = CoreBackend(plan, session=meta.get("session"), runner=runner)
        else:
            backend = LocalBackend(plan, meta.get("port", 5000), meta.get("data_port", 6000))
            backend.rundir = rundir
            backend.adopt(meta.get("pids", {}))
        orch = cls(plan, backend, rundir, meta.get("verbose", False))
        orch.flags = meta.get("flags", {})
        return orch

    # -- ciclo de vida -------------------------------------------------

    def up(self, only=None, spare=False, verbose=None, settle=0.8):
        """Arranca os nós do plano: primeiro os servidores, depois os outros. Devolve os ids lançados."""
        with self._lock:
            if verbose is not None:
                self.verbose = verbose
            self.backend.prepare(self.rundir)
            chosen = [n for n in self.plan.nodes.values()
                      if (only is None or n.id in only) and (spare or only is not None or not n.spare)]
            for spec in chosen:   # começa de limpo: estado e logs antigos enganariam
                self.flags.pop(spec.id, None)
                for path in (self.status_path(spec.id), self.log_path(spec.id)):
                    try:
                        os.remove(path)
                    except FileNotFoundError:
                        pass
            servers = [n for n in chosen if n.serve]
            others = [n for n in chosen if not n.serve]
            for group in (servers, others):
                for spec in group:
                    self._start(spec)
                if group is servers and servers and others:
                    time.sleep(settle)
            self.save(started=time.time())
            return [n.id for n in servers + others]

    def _start(self, spec):
        if self.backend.alive(spec):
            raise BackendError(f"{spec.id} já está a correr (usa primeiro: ottctl down {self.plan.name})")
        try:
            os.remove(self.status_path(spec.id))
        except FileNotFoundError:
            pass
        argv = self.backend.argv(spec, self.status_path(spec.id), self.verbose)
        self.backend.start(spec, argv, self.log_path(spec.id))
        self.flags.pop(spec.id, None)

    def start(self, ids):
        with self._lock:
            for node_id in ids:
                self._start(self._spec(node_id))
            self.save()

    def _spec(self, node_id):
        if node_id not in self.plan.nodes:
            raise BackendError(f"nó desconhecido: {node_id} (nós: {', '.join(sorted(self.plan.nodes))})")
        return self.plan.nodes[node_id]

    def signal(self, ids, name, flag=None):
        with self._lock:
            for node_id in ids:
                spec = self._spec(node_id)
                self.backend.signal(spec, name)
                if flag:
                    self.flags.setdefault(node_id, {})[flag] = True
                if name == "CONT":
                    self.flags.get(node_id, {}).pop("frozen", None)
            self.save()

    def stop(self, ids):
        """SIGTERM: saída limpa (BYE aos vizinhos), como o Ctrl+C."""
        self.signal(ids, "TERM", "stopped")

    def kill(self, ids):
        """SIGKILL: morte abrupta; o TCP fecha e os vizinhos veem-no logo."""
        self.signal(ids, "KILL", "killed")

    def freeze(self, ids):
        """SIGSTOP: o processo continua vivo mas calado; só se deteta pelo timeout do keepalive."""
        self.signal(ids, "STOP", "frozen")

    def thaw(self, ids):
        self.signal(ids, "CONT")

    def down(self, wait=3.0):
        """Pára todos os nós (SIGTERM e, se for preciso, SIGKILL)."""
        with self._lock:
            ids = [n for n in self.plan.nodes]
            for node_id in ids:
                try:
                    self.backend.signal(self._spec(node_id), "CONT")   # um nó congelado não responde ao TERM
                except BackendError:
                    pass
                try:
                    self.backend.signal(self._spec(node_id), "TERM")
                except BackendError:
                    pass
            deadline = time.time() + wait
            while time.time() < deadline and any(self.backend.alive(self._spec(i)) for i in ids):
                time.sleep(0.1)
            for node_id in ids:
                if self.backend.alive(self._spec(node_id)):
                    try:
                        self.backend.signal(self._spec(node_id), "KILL")
                    except BackendError:
                        pass
            self.flags = {}
            self.save()

    # -- rede (só no CORE) --------------------------------------------

    def _core_node(self, name):
        known = {n.core for n in self.plan.nodes.values()} | set(self.plan.free) | \
            {u["name"] for u in self.underlay()["nodes"]}
        name = self.plan.nodes[name].core if name in self.plan.nodes else name
        if name not in known:
            raise BackendError(f"nó do CORE desconhecido: {name}")
        return name

    def underlay(self):
        if self._underlay is None:
            self._underlay = underlay_of(self.plan)
        return self._underlay

    def netem(self, node, iface, delay=None, jitter=None, loss=None, rate=None):
        cmd = netem_command(iface, delay, jitter, loss, rate)
        rc, out = self.backend.execute(self._core_node(node), cmd)
        if rc != 0:
            raise BackendError(f"tc falhou em {node} {iface}: {out}")

    def netem_clear(self, node, iface):
        netem_command(iface, delay="1ms")   # só valida a interface
        self.backend.execute(self._core_node(node), ["tc", "qdisc", "del", "dev", iface, "root"])

    def link_netem(self, a, ia, b, ib, **params):
        """Atraso, jitter, perdas ou débito numa ligação do underlay: aplica-se nos dois extremos."""
        self.netem(a, ia, **params)
        self.netem(b, ib, **params)

    def link_clear(self, a, ia, b, ib):
        self.netem_clear(a, ia)
        self.netem_clear(b, ib)

    def link_set(self, a, ia, b, ib, up):
        """Corta ou repõe uma ligação do underlay (nos dois extremos)."""
        self.link_state(a, ia, up)
        self.link_state(b, ib, up)

    def link_state(self, node, iface, up):
        netem_command(iface, delay="1ms")
        rc, out = self.backend.execute(self._core_node(node), ["ip", "link", "set", iface, "up" if up else "down"])
        if rc != 0:
            raise BackendError(f"ip link falhou em {node} {iface}: {out}")

    # -- observação ----------------------------------------------------

    def read_status(self, node_id):
        try:
            with open(self.status_path(node_id)) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def health(self, spec, status, now):
        flags = self.flags.get(spec.id, {})
        if flags.get("frozen"):    # primeiro o que o próprio orquestrador fez: o ficheiro de estado ainda é recente
            return "frozen"
        if flags.get("killed"):
            return "dead"
        if flags.get("stopped"):
            return "stopped"
        if status is None:
            return "off"
        if not status.get("online", True):
            return "stopped"
        if now - status.get("ts", 0) <= STALE_AFTER:
            return "up"
        return "dead" if self.backend.alive(spec) is False else "stale"

    def state(self):
        """Estado de todo o cenário: nós, estado de cada um, arestas, árvore de cada fluxo e eventos."""
        now = time.time()
        meta = self.load()
        self.flags = meta.get("flags", self.flags)
        nodes, events, flows = {}, [], set()
        tree = {}
        for spec in self.plan.nodes.values():
            status = self.read_status(spec.id)
            h = self.health(spec, status, now)
            nodes[spec.id] = {"id": spec.id, "core": spec.core, "role": spec.role, "spare": spec.spare, "health": h,
                              "age": None if status is None else round(now - status.get("ts", now), 1),
                              "publish": spec.publish, "subscribe": spec.subscribe, "status": status}
            for flow in spec.publish:
                flows.add(f"{spec.id}:{flow}")
            if status:
                for e in status.get("events", []):
                    events.append({**e, "node": spec.id})
                for r in status.get("routes", []):
                    if r["upstream"] and h == "up":   # a última fotografia de um nó morto já não vale
                        tree.setdefault(f"{r['origin']}:{r['flow']}", []).append(
                            {"from": r["upstream"], "to": spec.id, "active": r["state"] == "ativa"})
        events.sort(key=lambda e: e["t"])
        return {"scenario": self.plan.name, "backend": self.backend.name, "time": round(now, 3),
                "started": meta.get("started"), "rundir": self.rundir, "nodes": nodes, "edges": self.plan.edges(),
                "positions": self.plan.positions(), "flows": sorted(flows), "tree": tree,
                "events": events[-EVENTS_KEPT:], "free": self.plan.free,
                "can_network": isinstance(self.backend, CoreBackend)}


def build(scenario, backend="local", session=None, port=5000, data_port=6000, rundir=None, runner=None):
    """Cria um orquestrador novo para o cenário (o ``up`` ainda é preciso)."""
    plan = load_plan(scenario)
    rundir = rundir or os.path.join(tempfile.gettempdir(), "ottctl", plan.name)
    os.makedirs(rundir, exist_ok=True)
    if backend == "core":
        be = CoreBackend(plan, session=session, runner=runner)
    elif backend == "local":
        be = LocalBackend(plan, port, data_port)
        be.rundir = rundir
    else:
        raise BackendError(f"modo desconhecido: {backend} (usa local ou core)")
    return Orchestrator(plan, be, rundir)
