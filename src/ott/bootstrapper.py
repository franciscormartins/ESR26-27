"""Serviço de bootstrap: dá a cada nó a lista dos seus vizinhos overlay.

O conhecimento do overlay vem de um ficheiro de topologia construído à mão:

    {"nodes": {"O1": {"host": "10.0.0.10", "neighbors": ["O2"]},
               "O2": {"host": "10.0.0.11", "neighbors": ["O1", "O3"]}, ...}}

As ligações são tratadas como bidirecionais; "port" é opcional por nó.
"""
import json
import logging
import time

from .protocol import DEFAULT_PORT

log = logging.getLogger("ott.bootstrap")


class BootstrapService:
    def __init__(self, config, self_id=None):
        self.nodes = config["nodes"]
        self.self_id = self_id  # nó onde corre o serviço (conta como ativo)
        self.adjacency = {n: set() for n in self.nodes}
        for node, info in self.nodes.items():
            for peer in info.get("neighbors", []):
                if peer not in self.nodes:
                    raise ValueError(f"{node}: vizinho desconhecido {peer!r}")
                self.adjacency[node].add(peer)
                self.adjacency[peer].add(node)
        self.registered = {}  # id -> instante do último registo

    @classmethod
    def from_file(cls, path, self_id=None):
        with open(path) as f:
            return cls(json.load(f), self_id)

    def address_of(self, node_id):
        info = self.nodes[node_id]
        return info["host"], info.get("port", DEFAULT_PORT)

    def _entry(self, node_id):
        host, port = self.address_of(node_id)
        return {"id": node_id, "host": host, "port": port}

    def neighbors_of(self, node_id):
        """Lista de {id, host, port} ou None se o nó não existe no overlay."""
        if node_id not in self.nodes:
            return None
        return [self._entry(peer) for peer in sorted(self.adjacency[node_id])]

    def replacements(self, node_id, have=(), lost=(), need=1):
        """Até `need` vizinhos substitutos para `node_id`, ou None se o nó é desconhecido.

        Candidatos: nós que já se registaram (mais o nó do serviço), exceto o
        próprio, os vizinhos atuais (`have`) e os perdidos (`lost`). Preferem-se
        os que já são vizinhos dos vizinhos atuais (ficam perto na topologia).
        """
        if node_id not in self.nodes:
            return None
        self.registered[node_id] = time.time()  # o pedido prova que está vivo
        alive = set(self.registered)
        if self.self_id in self.nodes:
            alive.add(self.self_id)
        excluded = set(have) | set(lost) | {node_id}
        near = set()
        for h in have:
            near |= self.adjacency.get(h, set())
        candidates = sorted((c for c in alive if c not in excluded),
                            key=lambda c: (c not in near, c))
        result = [self._entry(c) for c in candidates[:need]]
        log.info("substitutos para %s (have=%s lost=%s) -> %s", node_id,
                 sorted(have), sorted(lost), [n["id"] for n in result])
        return result

    def register(self, node_id):
        neighbors = self.neighbors_of(node_id)
        if neighbors is not None:
            self.registered[node_id] = time.time()
            log.info("registo de %s -> vizinhos %s", node_id,
                     [n["id"] for n in neighbors])
        return neighbors
