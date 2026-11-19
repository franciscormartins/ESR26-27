"""Orquestrador de um cenário: arranca e pára nós, injeta falhas e junta o estado de todos numa só vista."""
import json
import os
import tempfile
import threading
import time

from .backend import BackendError, CoreBackend, LocalBackend, netem_command
from ..subscriptions import parse_subscription
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
        self.actions = []        # [{t, text, kind}]: o que o orquestrador fez (marcas nos gráficos, medição de tempos)
        self.net_effects = {}    # "a:ia|b:ib" ou "nó:iface" -> {a, ia, b, ib, params, down}
        self.sub_override = {}   # nó -> subscrições escolhidas no painel (em vez das do plano)
        self.plan_subs = {n.id: list(n.subscribe) for n in plan.nodes.values()}   # as do plano, para as repor
        self.hop_links = {}      # "a>b" -> ligação do underlay onde se pôs um obstáculo nesse troço overlay
        self._lock = threading.RLock()
        self._underlay = None
        os.makedirs(self.rundir, exist_ok=True)

    # -- caminhos e estado em disco -----------------------------------

    def status_path(self, node_id):
        return os.path.join(self.rundir, f"{node_id}.json")

    def frame_path(self, node_id, origin, flow):
        return os.path.join(self.rundir, "frames", node_id, f"{origin}_{flow}.jpg")

    def log_path(self, node_id):
        return os.path.join(self.rundir, f"{node_id}.log")

    @property
    def state_path(self):
        return os.path.join(self.rundir, "state.json")

    def save(self, started=None):
        meta = {"scenario": self.plan.name, "backend": self.backend.name, "verbose": self.verbose, "flags": self.flags,
                "pids": getattr(self.backend, "pids", {}), "started": started or self.load().get("started"),
                "actions": self.actions[-200:], "net": self.net_effects, "subs": self.sub_override,
                "hops": self.hop_links}
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
        orch.actions = meta.get("actions", [])
        orch.net_effects = meta.get("net", {})
        orch.hop_links = meta.get("hops", {})
        orch.sub_override = {k: v for k, v in meta.get("subs", {}).items() if k in plan.nodes}
        for node_id, subs in orch.sub_override.items():
            plan.nodes[node_id].subscribe = list(subs)
        return orch

    def record(self, text, kind="acao", short=None):
        """Regista uma ação (com a hora) para as marcas nos gráficos; devolve a hora."""
        t = time.time()
        self.actions.append({"t": round(t, 3), "text": text, "kind": kind, "short": short or text[:16]})
        del self.actions[:-200]
        return t

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
            self.record(f"lançados {len(servers) + len(others)} nós ({self.backend.name})", "up")
            self.save(started=time.time())
            return [n.id for n in servers + others]

    def _start(self, spec, extra=(), bootstrap=None):
        if self.backend.alive(spec):
            raise BackendError(f"{spec.id} já está a correr (usa primeiro: ottctl down {self.plan.name})")
        try:
            os.remove(self.status_path(spec.id))
        except FileNotFoundError:
            pass
        frames = os.path.join(self.rundir, "frames", spec.id)   # imagens de uma execução anterior enganariam o painel
        if os.path.isdir(frames):
            for name in os.listdir(frames):
                try:
                    os.remove(os.path.join(frames, name))
                except OSError:
                    pass
        argv = self.backend.argv(spec, self.status_path(spec.id), self.verbose, extra=extra, bootstrap=bootstrap)
        self.backend.start(spec, argv, self.log_path(spec.id))
        self.flags.pop(spec.id, None)

    def start(self, ids, extra=(), bootstrap=None):
        """(Re)lança nós; ``extra`` acrescenta argumentos e ``bootstrap`` usa outro nó como bootstrapper."""
        with self._lock:
            for node_id in ids:
                self._start(self._spec(node_id), extra, bootstrap)
                self.record(f"lançado {node_id}" + (f" (bootstrapper {bootstrap})" if bootstrap else "")
                            + (f" {' '.join(extra)}" if extra else ""), "start", short=f"start {node_id}")
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
                self.record({"TERM": "parado", "KILL": "morto (kill -9)", "STOP": "congelado", "CONT": "retomado"}
                            .get(name, name) + f": {node_id}", name.lower(),
                            short={"TERM": "stop", "KILL": "kill", "STOP": "freeze", "CONT": "thaw"}.get(name, name) + f" {node_id}")
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
            self.record("todos os nós parados", "down")
            self.save()

    # -- subscrições dos clientes (o professor escolhe o que cada um vê) --------

    def subscription_options(self):
        """O que um cliente pode subscrever: ``[{key, flow, server, publishers, video}]``.

        ``SERVIDOR:FLUXO`` pede esse servidor; só ``FLUXO`` (D38) usa o servidor mais perto e muda se ele cair (só se
        mais do que um servidor publica o fluxo).
        """
        out = []
        for flow in self.plan.all_flows():
            pubs = self.plan.publishers_of(flow)
            video = self.plan.video_label(flow)
            out += [{"key": f"{s}:{flow}", "flow": flow, "server": s, "publishers": pubs, "video": video} for s in pubs]
            if len(pubs) > 1:
                out.append({"key": flow, "flow": flow, "server": None, "publishers": pubs, "video": video})
        return out

    def _check_subscriptions(self, subs):
        if not isinstance(subs, list) or not all(isinstance(s, str) for s in subs):
            raise BackendError("subscrições inválidas")
        allowed = {o["key"] for o in self.subscription_options()}
        parsed = []
        for sub in subs:
            try:
                parsed.append(parse_subscription(sub))
            except ValueError as exc:
                raise BackendError(str(exc)) from None
            if sub not in allowed:
                raise BackendError(f"ninguém publica {sub} neste cenário")
        for i, (server, flow) in enumerate(parsed):
            for other, other_flow in parsed[:i]:
                if flow == other_flow and (server is None or other is None or server == other):
                    raise BackendError(f"o fluxo {flow} está repetido ou sobreposto")

    def set_subscriptions(self, node_id, subs):
        """Muda os fluxos que um nó subscreve: pára-o (BYE aos vizinhos) e relança-o com as subscrições novas.

        Se o nó não estiver a correr, só muda o que lançará. ``subs`` vazio faz do nó um intermédio.
        """
        spec = self._spec(node_id)
        if spec.serve:
            raise BackendError(f"{node_id} é um servidor: só os outros nós subscrevem fluxos")
        subs = list(dict.fromkeys(subs))
        self._check_subscriptions(subs)
        with self._lock:
            running = self.backend.running(spec)
            if running:
                self.signal([node_id], "TERM", "stopped")
                self.backend.wait_gone(spec)
            spec.subscribe = subs
            if subs == self._plan_subscriptions(node_id):
                self.sub_override.pop(node_id, None)
            else:
                self.sub_override[node_id] = subs
            self.record(f"{node_id} subscreve {', '.join(subs) or 'nada'}", "subs", short=f"subs {node_id}")
            if running:
                self._start(spec)
                self.flags.pop(node_id, None)
            self.save()

    def _plan_subscriptions(self, node_id):
        return list(self.plan_subs[node_id])

    def reset_subscriptions(self):
        """Repõe as subscrições do plano em todos os nós que as mudaram."""
        for node_id in list(self.sub_override):
            self.set_subscriptions(node_id, self._plan_subscriptions(node_id))

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

    def _effect(self, key, **info):
        entry = self.net_effects.setdefault(key, {})
        entry.update(info)
        if not entry.get("params") and not entry.get("down"):
            self.net_effects.pop(key, None)
        self.save()

    @staticmethod
    def _end_key(node, iface):
        return f"{node}:{iface}"

    @staticmethod
    def _link_key(a, ia, b, ib):
        return "|".join(sorted((f"{a}:{ia}", f"{b}:{ib}")))

    @staticmethod
    def _describe(params):
        return " ".join(f"{k}={v}" for k, v in params.items() if v)

    def netem(self, node, iface, delay=None, jitter=None, loss=None, rate=None, _key=None):
        cmd = netem_command(iface, delay, jitter, loss, rate)
        rc, out = self.backend.execute(self._core_node(node), cmd)
        if rc != 0:
            raise BackendError(f"tc falhou em {node} {iface}: {out}")
        params = {"delay": delay, "jitter": jitter, "loss": loss, "rate": rate}
        if _key is None:
            self.record(f"netem em {node}:{iface} ({self._describe(params)})", "netem")
            self._effect(self._end_key(node, iface), a=node, ia=iface, b=None, ib=None, params=params)

    def netem_clear(self, node, iface, _key=None):
        netem_command(iface, delay="1ms")   # só valida a interface
        self.backend.execute(self._core_node(node), ["tc", "qdisc", "del", "dev", iface, "root"])
        if _key is None:
            self.record(f"netem removido de {node}:{iface}", "netem")
            self._effect(self._end_key(node, iface), params={})

    def link_netem(self, a, ia, b, ib, **params):
        """Atraso, jitter, perdas ou débito numa ligação do underlay: aplica-se nos dois extremos."""
        self.netem(a, ia, _key=True, **params)
        self.netem(b, ib, _key=True, **params)
        shown = {k: params.get(k) for k in ("delay", "jitter", "loss", "rate")}
        self.record(f"ligação {a}:{ia} ↔ {b}:{ib}: {self._describe(shown)}", "netem")
        self._effect(self._link_key(a, ia, b, ib), a=a, ia=ia, b=b, ib=ib, params=shown)

    def link_clear(self, a, ia, b, ib):
        self.netem_clear(a, ia, _key=True)
        self.netem_clear(b, ib, _key=True)
        self.record(f"ligação {a}:{ia} ↔ {b}:{ib}: netem removido", "netem")
        self._effect(self._link_key(a, ia, b, ib), params={})

    def link_set(self, a, ia, b, ib, up):
        """Corta ou repõe uma ligação do underlay (nos dois extremos)."""
        self.link_state(a, ia, up, _key=True)
        self.link_state(b, ib, up, _key=True)
        self.record(f"ligação {a}:{ia} ↔ {b}:{ib} {'reposta' if up else 'cortada'}", "link")
        self._effect(self._link_key(a, ia, b, ib), a=a, ia=ia, b=b, ib=ib, down=not up)

    def link_state(self, node, iface, up, _key=None):
        netem_command(iface, delay="1ms")
        rc, out = self.backend.execute(self._core_node(node), ["ip", "link", "set", iface, "up" if up else "down"])
        if rc != 0:
            raise BackendError(f"ip link falhou em {node} {iface}: {out}")
        if _key is None:
            self.record(f"interface {node}:{iface} {'ligada' if up else 'desligada'}", "link")
            self._effect(self._end_key(node, iface), a=node, ia=iface, b=None, ib=None, down=not up)

    def ip_path(self, src, dst, max_hops=14):
        """Caminho que os pacotes IP seguem mesmo de ``src`` para ``dst`` (nós do CORE), perguntando às tabelas de rotas (``ip route get``).

        Devolve a lista de nós do CORE, ou None se não for possível (ex.: sem CORE). É o caminho verdadeiro, depois do OSPF e dos
        efeitos de rede, ao contrário de um caminho "mais curto" adivinhado a partir do desenho.
        """
        if not isinstance(self.backend, CoreBackend):
            return None
        under = self.underlay()
        owner, ips = {}, {}
        for l in under["links"]:
            for node, ip in ((l["a"], l.get("ipa")), (l["b"], l.get("ipb"))):
                if ip:
                    owner[ip] = node
                    ips.setdefault(node, ip)
        if dst not in ips or src not in ips:
            return None
        target, path, cur = ips[dst], [src], src
        while cur != dst and len(path) < max_hops:
            rc, out = self.backend.execute(cur, ["ip", "route", "get", target])
            if rc != 0:
                return None
            words = out.split()
            nxt = owner.get(words[words.index("via") + 1]) if "via" in words else dst
            if nxt is None or nxt in path:
                return None
            path.append(nxt)
            cur = nxt
        return path if cur == dst else None

    def paths(self, pairs, ttl=3.0):
        """{"A>B": [nós do CORE]} para pares de nós overlay (com cache de alguns segundos)."""
        cache, now, out = self.__dict__.setdefault("_paths", {}), time.time(), {}
        for a, b in pairs:
            if a not in self.plan.nodes or b not in self.plan.nodes:
                continue
            key = f"{a}>{b}"
            if key not in cache or now - cache[key][0] > ttl:
                try:
                    path = self.ip_path(self.plan.nodes[a].core, self.plan.nodes[b].core)
                except BackendError:
                    path = None
                cache[key] = (now, path)
            if cache[key][1]:
                out[key] = cache[key][1]
        return out

    def router_set(self, name, up):
        """Desliga (ou volta a ligar) um router do CORE: põe em baixo todas as suas interfaces, como se tivesse morrido.

        O OSPF dá pela falta dos vizinhos e recalcula; os nós overlay vêem os caminhos mudar.
        """
        node = next((u for u in self.underlay()["nodes"] if u["name"] == name and u["type"] == "router"), None)
        if node is None:
            raise BackendError(f"não é um router do CORE: {name}")
        ifaces = [l["ia"] for l in self.underlay()["links"] if l["a"] == name] + \
                 [l["ib"] for l in self.underlay()["links"] if l["b"] == name]
        failed = []
        for iface in ifaces:
            try:
                self.link_state(name, iface, up, _key=True)
                self._effect(self._end_key(name, iface), a=name, ia=iface, b=None, ib=None, down=not up)
            except BackendError as exc:
                failed.append(str(exc))
        self.record(f"router {name} {'ligado' if up else 'desligado'} ({len(ifaces)} interfaces)", "link")
        if failed:
            raise BackendError("; ".join(failed))

    def hop_link(self, a, b):
        """Ligação do underlay onde pôr um obstáculo no troço overlay ``a``→``b``: ``(x, ix, y, iy, sw)``.

        Descobre o caminho IP verdadeiro entre os dois nós (``ip route get`` em cada salto) e escolhe a ligação entre dois
        routers a meio dele; se não houver (os dois nós no mesmo router) usa a primeira. Só no CORE. O resultado fica
        guardado, para repor a mesma ligação mesmo que o OSPF desvie o caminho depois de um corte.
        """
        key = f"{a}>{b}"
        if key in self.hop_links:
            return tuple(self.hop_links[key])
        if not isinstance(self.backend, CoreBackend):
            raise BackendError("os obstáculos na rede só existem com o CORE")
        path = self.ip_path(self._spec(a).core, self._spec(b).core)
        if not path or len(path) < 2:
            raise BackendError(f"não consegui descobrir o caminho IP de {a} para {b}")
        under = self.underlay()
        routers = {u["name"] for u in under["nodes"] if u["type"] == "router"}
        found = []
        for x, y in zip(path, path[1:]):
            for l in under["links"]:
                if (l["a"], l["b"]) == (x, y):
                    found.append((l["a"], l["ia"], l["b"], l["ib"], l.get("sw")))
                    break
                if (l["a"], l["b"]) == (y, x):
                    found.append((l["b"], l["ib"], l["a"], l["ia"], l.get("sw")))
                    break
        if not found:
            raise BackendError(f"o caminho {'>'.join(path)} não tem ligações no desenho do CORE")
        between = [l for l in found if l[0] in routers and l[2] in routers and not l[4]]
        choice = between[len(between) // 2] if between else found[0]
        self.hop_links[key] = list(choice)
        return choice

    def hop_netem(self, a, b, **params):
        """Atraso, jitter, perdas ou débito no troço overlay ``a``→``b`` (um só sentido do nó para o outro é igual: dois extremos)."""
        x, ix, y, iy, sw = self.hop_link(a, b)
        if sw:   # ligação a um switch (cenário 3): só o extremo do nó tem espaço de nomes
            node, iface = (y, iy) if sw == "a" else (x, ix)
            return self.netem(node, iface, **params)
        return self.link_netem(x, ix, y, iy, **params)

    def hop_clear(self, a, b):
        x, ix, y, iy, sw = self.hop_link(a, b)
        if sw:
            node, iface = (y, iy) if sw == "a" else (x, ix)
            return self.netem_clear(node, iface)
        return self.link_clear(x, ix, y, iy)

    def hop_set(self, a, b, up):
        """Corta (``up=False``) ou repõe a ligação do underlay do troço overlay ``a``→``b``."""
        x, ix, y, iy, sw = self.hop_link(a, b)
        if sw:
            node, iface = (y, iy) if sw == "a" else (x, ix)
            return self.link_state(node, iface, up)
        return self.link_set(x, ix, y, iy, up)

    def clear_network(self):
        """Remove todos os efeitos de rede aplicados por este orquestrador (netem e cortes)."""
        for entry in list(self.net_effects.values()):
            ends = [(entry["a"], entry["ia"])] + ([(entry["b"], entry["ib"])] if entry.get("b") else [])
            for node, iface in ends:
                try:
                    if entry.get("params"):
                        self.netem_clear(node, iface, _key=True)
                    if entry.get("down"):
                        self.link_state(node, iface, True, _key=True)
                except BackendError:
                    pass
        self.net_effects = {}
        self.hop_links = {}
        self.record("rede reposta (sem atrasos nem cortes)", "netem")
        self.save()

    def probe(self, free_node, args, timeout=30):
        """Corre o ``ott`` num host livre (ex.: L1) e devolve (código, texto): serve para testar o bootstrap."""
        self.record(f"sonda em {free_node}: ott {' '.join(args)}", "probe")
        return self.backend.probe(free_node, list(args), timeout)

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
            return "waiting" if status.get("phase") == "a registar" else "up"
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
        interval = self.plan.route_test
        paths = []
        for spec in self.plan.nodes.values():   # o caminho que o último ROUTE_TEST fez até cada cliente
            status = nodes[spec.id]["status"]
            if not status or nodes[spec.id]["health"] != "up":
                continue
            for r in status.get("route_tests", []):
                if r["delivered"]:
                    age = round(now - r["t"], 1)
                    paths.append({"node": spec.id, "key": f"{r['origin']}:{r['flow']}", "path": r["path"], "hops": r["hops"],
                                  "seq": r["seq"], "age": age, "fresh": bool(interval) and age <= 3 * interval + 3})
        videos = {f: self.plan.video_label(f) for f in self.plan.all_flows()}
        return {"scenario": self.plan.name, "backend": self.backend.name, "time": round(now, 3),
                "started": meta.get("started"), "rundir": self.rundir, "nodes": nodes, "edges": self.plan.edges(),
                "positions": self.plan.positions(), "flows": sorted(flows), "tree": tree,
                "events": events[-EVENTS_KEPT:], "free": self.plan.free,
                "actions": meta.get("actions", self.actions)[-100:], "net": list(meta.get("net", self.net_effects).values()),
                "can_network": isinstance(self.backend, CoreBackend), "paths": paths, "route_test": interval,
                "videos": videos, "publishers": {f: self.plan.publishers_of(f) for f in videos},
                "options": self.subscription_options(), "subs_changed": sorted(self.sub_override)}


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
