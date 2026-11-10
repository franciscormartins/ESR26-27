"""Estado de um nó em JSON, para as ferramentas de teste (``ottctl`` e o painel web).

Com ``ott --status-file CAMINHO`` um :class:`StatusWriter` reescreve, de segundo a segundo (por omissão), um ficheiro
com o que interessa saber de um nó: vizinhos e RTT, **tabela de rotas**, fluxos recebidos, contadores do plano de
dados e uma lista curta de **eventos relevantes** (vizinho que cai ou volta, rota nova, montante que muda, rota sem
caminho, fluxo que pára ou volta). Os nós do CORE partilham o sistema de ficheiros do anfitrião, por isso o painel lê
estes ficheiros sem precisar de rede.

Só lê: usa a API pública do nó e olha para a tabela de rotas sem a alterar. Os eventos de rotas e de fluxos saem de
comparar fotografias sucessivas, por isso duas mudanças dentro do mesmo intervalo contam como uma só.
"""
import json
import logging
import math
import os
import threading
import time
from collections import deque

log = logging.getLogger("ott.status")

STALL_AFTER = 2.0   # segundos sem imagens novas para dar um fluxo como parado


def _num(value):
    """Números finitos como estão; infinito ou NaN como None (o JSON não os representa)."""
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


class StatusWriter:
    """Escreve o estado de um nó num ficheiro JSON e mantém os seus eventos recentes."""

    def __init__(self, path, node, routing_table, forwarder=None, receivers=None, published=(),
                 subscriptions=None, interval=1.0, max_events=200):
        self.path = path
        self.node = node
        self.table = routing_table
        self.forwarder = forwarder
        self.receivers = receivers if receivers is not None else {}
        self.published = list(published)
        self.subscriptions = subscriptions or (lambda: ([], []))   # () -> (pendentes, ativas)
        self.interval = interval
        self.started = time.time()
        self.events = deque(maxlen=max_events)
        self._prev_routes = None
        self._prev_flows = {}
        self._rates = {}
        self._last = None
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    # -- eventos -------------------------------------------------------

    def event(self, kind, text, **extra):
        """Regista um evento (instante, tipo e texto curto)."""
        with self._lock:
            self.events.append({"t": round(time.time(), 3), "kind": kind, "text": text, **extra})

    def _on_up(self, _node, neighbor):
        self.event("vizinho_up", f"vizinho {neighbor} ligado", peer=neighbor)

    def _on_down(self, _node, neighbor):
        self.event("vizinho_down", f"vizinho {neighbor} desligado", peer=neighbor)

    # -- fotografias ---------------------------------------------------

    def _routes(self):
        lock = getattr(self.table, "_lock", None)
        routes = {}
        now = time.monotonic()
        for _ in range(3):   # a tabela pode mudar enquanto a copiamos
            try:
                if lock is not None:
                    with lock:
                        entries = list(self.table.routes.values())
                else:
                    entries = list(self.table.routes.values())
                break
            except RuntimeError:
                continue
        else:
            entries = []
        for r in entries:
            routes[(r.origin, r.flow)] = {
                "origin": r.origin, "flow": r.flow, "state": r.state, "upstream": r.upstream,
                "metric": _num(r.metric), "delay_ms": None if _num(r.delay) is None else round(r.delay * 1000, 3),
                "epoch": r.epoch, "destinations": sorted(r.destinations),
                "local_subscribers": sorted(r.local_subscribers),
                "withdrawn": r.withdrawn_at is not None,
                "candidates": {n: {"metric": _num(c.metric),
                                   "delay_ms": None if _num(c.delay) is None else round(c.delay * 1000, 3),
                                   "age": round(now - c.seen_at, 1)} for n, c in sorted(r.candidates.items())},
            }
        return routes

    def _diff_routes(self, routes):
        prev = self._prev_routes
        self._prev_routes = routes
        if prev is None:
            return
        for key, r in routes.items():
            name = f"{key[0]}:{key[1]}"
            old = prev.get(key)
            if old is None:
                self.event("rota_nova", f"rota nova {name} via {r['upstream'] or 'origem'} "
                           f"({r['metric'] if r['metric'] is not None else '?'} saltos)", route=name)
                continue
            if r["upstream"] != old["upstream"]:
                if r["upstream"] is None:
                    self.event("rota_perdida", f"{name}: sem caminho (montante {old['upstream']} perdido)", route=name)
                elif old["upstream"] is None:
                    self.event("rota_recuperada", f"{name}: caminho recuperado via {r['upstream']}", route=name)
                else:
                    self.event("montante", f"{name}: montante {old['upstream']} → {r['upstream']}", route=name)
            if r["state"] != old["state"]:
                self.event("rota_estado", f"{name}: {old['state']} → {r['state']}", route=name)
            added = sorted(set(r["destinations"]) - set(old["destinations"]))
            removed = sorted(set(old["destinations"]) - set(r["destinations"]))
            if added or removed:
                parts = [f"+{n}" for n in added] + [f"-{n}" for n in removed]
                self.event("ramo", f"{name}: ramos a jusante {' '.join(parts)}", route=name)
            if r["withdrawn"] and not old["withdrawn"]:
                self.event("withdraw", f"{name}: rota retirada (WITHDRAW)", route=name)
        for key in prev.keys() - routes.keys():
            self.event("rota_esquecida", f"rota {key[0]}:{key[1]} esquecida", route=f"{key[0]}:{key[1]}")

    def _flows(self, now):
        _pending, active = self.subscriptions()
        active = {tuple(k) for k in active}
        flows = []
        for (origin, flow), rx in sorted(self.receivers.items()):
            st = rx.stats
            name = f"{origin}:{flow}"
            prev = self._prev_flows.get(name)
            if prev is None:
                prev = {"received": st.received, "t": now, "last_rx": now, "stalled": False, "seen": False}
            rate = 0.0
            if now > prev["t"]:
                rate = (st.received - prev["received"]) / (now - prev["t"])
            if st.received > prev["received"]:
                prev["last_rx"] = now
                prev["seen"] = True
                if prev["stalled"]:
                    self.event("fluxo_voltou", f"fluxo {name} voltou a chegar", route=name)
                    prev["stalled"] = False
            elif (name in {f"{a}:{b}" for a, b in active} and prev["seen"] and not prev["stalled"]
                  and now - prev["last_rx"] >= STALL_AFTER):
                self.event("fluxo_parou", f"fluxo {name} parou de chegar", route=name)
                prev["stalled"] = True
            prev["received"], prev["t"] = st.received, now
            self._prev_flows[name] = prev
            flows.append({"origin": origin, "flow": flow, "active": (origin, flow) in active,
                          "received": st.received, "lost": st.lost, "late": st.late,
                          "loss_pct": round(st.loss_ratio() * 100, 2), "jitter_ms": round(st.jitter * 1000, 2),
                          "restarts": st.restarts, "rate": round(rate, 1),
                          "idle_s": round(now - prev["last_rx"], 1), "stalled": prev["stalled"]})
        return flows

    def snapshot(self):
        """Devolve o estado atual do nó como dicionário (serializável em JSON)."""
        now = time.time()
        routes = self._routes()
        self._diff_routes(routes)
        rtts = self.node.rtts()
        neighbors = [{"id": n, "rtt_ms": None if rtts.get(n) is None else round(rtts[n] * 1000, 2)}
                     for n in self.node.neighbor_ids()]
        data = None
        if self.forwarder is not None:
            f = self.forwarder
            prev = self._rates.get("data")
            rate = {"received": 0.0, "forwarded": 0.0}
            if prev and now > prev["t"]:
                rate = {"received": round((f.received - prev["received"]) / (now - prev["t"]), 1),
                        "forwarded": round((f.forwarded - prev["forwarded"]) / (now - prev["t"]), 1)}
            self._rates["data"] = {"t": now, "received": f.received, "forwarded": f.forwarded}
            data = {"received": f.received, "forwarded": f.forwarded, "dropped": f.dropped,
                    "rpf_dropped": f.rpf_dropped, "received_rate": rate["received"],
                    "forwarded_rate": rate["forwarded"]}
        service = getattr(self.node, "bootstrap_service", None)
        pending, active = self.subscriptions()
        with self._lock:
            events = list(self.events)
        return {
            "node": self.node.id, "online": True, "ts": round(now, 3), "started": round(self.started, 3),
            "pid": os.getpid(), "port": self.node.port,
            "bootstrapper": sorted(service.registered) if service is not None else None,
            "neighbors": neighbors,
            "published": self.published,
            "subscriptions": {"pending": sorted(f"{a}:{b}" for a, b in pending),
                              "active": sorted(f"{a}:{b}" for a, b in active)},
            "routes": sorted(routes.values(), key=lambda r: (r["origin"], r["flow"])),
            "flows": self._flows(now),
            "data": data,
            "events": events,
        }

    # -- escrita -------------------------------------------------------

    def write(self, snapshot=None):
        """Escreve o ficheiro de forma atómica (escreve num temporário e renomeia)."""
        snap = snapshot if snapshot is not None else self.snapshot()
        self._last = snap
        tmp = f"{self.path}.tmp{os.getpid()}"
        with open(tmp, "w") as f:
            json.dump(snap, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, self.path)

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                self.write()
            except Exception:   # o estado é só para observar: nunca derruba o nó
                log.exception("não foi possível escrever o estado")

    def start(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self.node.on_neighbor_up(self._on_up)
        self.node.on_neighbor_down(self._on_down)
        self.event("arranque", f"nó {self.node.id} arrancou")
        try:
            self.write()
        except Exception:
            log.exception("não foi possível escrever o estado")
        self._thread = threading.Thread(target=self._run, name="ott-status", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        """Pára a escrita e deixa um último estado com ``online: false``."""
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        self.event("paragem", f"nó {self.node.id} parou")
        try:
            snap = dict(self._last or {"node": self.node.id})
            snap.update(online=False, ts=round(time.time(), 3))
            with self._lock:
                snap["events"] = list(self.events)
            self.write(snap)
        except Exception:
            log.exception("não foi possível escrever o estado final")
