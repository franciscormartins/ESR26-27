"""Plano de um cenário: o que corre em cada nó (servidor, fluxos, subscrições) e onde está desenhado.

O plano vive em ``topologies/<cenario>.plano.json``; o overlay (vizinhos e IPs) em ``<cenario>.json`` e o desenho do
CORE em ``<cenario>.xml``. Formato do plano::

    {"xml": "cenario4.xml", "overlay": "cenario4.json", "bootstrap": "S1",
     "nodes": {"S1": {"serve": true, "publish": ["filme_a"]},
               "C1": {"subscribe": ["S1:filme_a"]},
               "O5": {"spare": true}},
     "free": ["L1", "L2"]}

Campos de cada nó: ``core`` (nome do nó no CORE; por omissão o campo ``core`` do overlay ou o próprio id), ``serve``,
``publish``, ``subscribe``, ``bootstrap`` (id do bootstrapper deste nó; por omissão o do plano; ``null`` = nenhum),
``neighbors`` (ids dos vizinhos, estratégia 1), ``core_bootstrap`` e ``core_neighbors`` (endereços a usar no CORE,
quando o IP do ficheiro overlay não serve: ligações diretas sem rotas), ``spare`` (só arranca se pedido) e ``extra``
(argumentos acrescentados à linha de comandos do ``ott``).
"""
import json
import math
import os
import xml.etree.ElementTree as ET  # só lê ficheiros do próprio repositório

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
TOPOLOGIES = os.path.join(ROOT, "topologies")
DEFAULT_PORT = 5000
DEFAULT_DATA_PORT = 6000


class PlanError(ValueError):
    pass


class NodeSpec:
    def __init__(self, node_id, core, info):
        self.id = node_id
        self.core = core
        self.serve = bool(info.get("serve"))
        self.publish = list(info.get("publish", []))
        self.subscribe = list(info.get("subscribe", []))
        self.bootstrap = info.get("bootstrap", "__plan__")
        self.neighbors = list(info.get("neighbors", []))
        self.core_bootstrap = info.get("core_bootstrap")
        self.core_neighbors = list(info.get("core_neighbors", []))
        self.spare = bool(info.get("spare"))
        self.extra = list(info.get("extra", []))

    @property
    def role(self):
        if self.serve:
            return "servidor"
        if self.subscribe:
            return "cliente"
        return "spare" if self.spare else "intermédio"


class Plan:
    def __init__(self, name, data, base_dir):
        self.name = name
        self.base_dir = base_dir
        self.bootstrap = data.get("bootstrap")
        self.free = list(data.get("free", []))
        self.overlay_path = os.path.join(base_dir, data["overlay"])
        with open(self.overlay_path) as f:
            self.overlay = json.load(f)["nodes"]
        self.xml_path = os.path.join(base_dir, data["xml"]) if data.get("xml") else None
        self.nodes = {}
        for node_id, info in data["nodes"].items():
            if node_id not in self.overlay:
                raise PlanError(f"{name}: o nó {node_id} do plano não existe no overlay")
            core = info.get("core") or self.overlay[node_id].get("core") or node_id
            self.nodes[node_id] = NodeSpec(node_id, core, info)
        self.validate()

    def validate(self):
        publishes = {n.id: set(n.publish) for n in self.nodes.values()}
        if self.bootstrap is not None and self.bootstrap not in self.nodes:
            raise PlanError(f"{self.name}: bootstrapper {self.bootstrap} não está no plano")
        for n in self.nodes.values():
            for sub in n.subscribe:
                server, _, flow = sub.partition(":")
                if flow not in publishes.get(server, ()):
                    raise PlanError(f"{self.name}: {n.id} subscreve {sub}, que ninguém publica")
            for peer in n.neighbors:
                if peer not in self.nodes:
                    raise PlanError(f"{self.name}: {n.id} tem o vizinho {peer}, que não está no plano")
        if self.bootstrap is not None and not self.nodes[self.bootstrap].serve:
            raise PlanError(f"{self.name}: o bootstrapper {self.bootstrap} tem de ter serve=true")

    # -- consultas -----------------------------------------------------

    def bootstrap_of(self, spec):
        return self.bootstrap if spec.bootstrap == "__plan__" else spec.bootstrap

    def edges(self):
        """Arestas do overlay (pares ordenados), com a simetria do bootstrapper."""
        seen = set()
        for a, info in self.overlay.items():
            for b in info["neighbors"]:
                seen.add(tuple(sorted((a, b))))
        return sorted(seen)

    def core_names(self):
        return sorted({n.core for n in self.nodes.values()} | set(self.free))

    def positions(self):
        """Posições (x, y) de cada nó overlay, do desenho do CORE; em círculo se não houver XML."""
        pos = {}
        if self.xml_path and os.path.exists(self.xml_path):
            root = ET.parse(self.xml_path).getroot()
            by_core = {d.get("name"): (float(d.find("position").get("x")), float(d.find("position").get("y")))
                       for d in root.find("devices")}
            pos = {n.id: by_core[n.core] for n in self.nodes.values() if n.core in by_core}
        if len(pos) < len(self.nodes):
            ids = sorted(self.nodes)
            for i, node_id in enumerate(ids):
                a = 2 * math.pi * i / max(1, len(ids))
                pos.setdefault(node_id, (500 + 300 * math.cos(a), 375 + 250 * math.sin(a)))
        return pos


def underlay_of(plan):
    """Nós e ligações do desenho do CORE: [{name, type, x, y}] e [{a, ia, b, ib}] (vazio se não houver XML)."""
    if not plan.xml_path or not os.path.exists(plan.xml_path):
        return {"nodes": [], "links": []}
    root = ET.parse(plan.xml_path).getroot()
    names, nodes = {}, []
    for d in root.find("devices"):
        p = d.find("position")
        names[d.get("id")] = d.get("name")
        nodes.append({"name": d.get("name"), "type": d.get("type"), "x": float(p.get("x")), "y": float(p.get("y"))})
    links = []
    for link in root.find("links"):
        i1, i2 = link.find("iface1"), link.find("iface2")
        if i1 is None or i2 is None:
            continue
        links.append({"a": names[link.get("node1")], "ia": i1.get("name"), "b": names[link.get("node2")],
                      "ib": i2.get("name")})
    return {"nodes": nodes, "links": links}


def available():
    """Nomes dos cenários com plano em ``topologies/``."""
    return sorted(f[:-len(".plano.json")] for f in os.listdir(TOPOLOGIES) if f.endswith(".plano.json"))


def load_plan(name_or_path):
    """Carrega ``topologies/<nome>.plano.json`` ou o plano dado por caminho."""
    if os.path.isfile(name_or_path):
        path = name_or_path
        name = os.path.basename(path)[:-len(".plano.json")] if path.endswith(".plano.json") else \
            os.path.splitext(os.path.basename(path))[0]
    else:
        name = name_or_path
        path = os.path.join(TOPOLOGIES, f"{name}.plano.json")
        if not os.path.isfile(path):
            raise PlanError(f"cenário {name!r} desconhecido; disponíveis: {', '.join(available())}")
    with open(path) as f:
        data = json.load(f)
    return Plan(name, data, os.path.dirname(os.path.abspath(path)))
