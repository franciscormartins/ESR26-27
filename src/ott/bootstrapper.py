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
    def __init__(self, config):
        self.nodes = config["nodes"]
        self.adjacency = {n: set() for n in self.nodes}
        for node, info in self.nodes.items():
            for peer in info.get("neighbors", []):
                if peer not in self.nodes:
                    raise ValueError(f"{node}: vizinho desconhecido {peer!r}")
                self.adjacency[node].add(peer)
                self.adjacency[peer].add(node)
        self.registered = {}  # id -> instante do último registo

    @classmethod
    def from_file(cls, path):
        with open(path) as f:
            return cls(json.load(f))

    def address_of(self, node_id):
        info = self.nodes[node_id]
        return info["host"], info.get("port", DEFAULT_PORT)

    def neighbors_of(self, node_id):
        """Lista de {id, host, port} ou None se o nó não existe no overlay."""
        if node_id not in self.nodes:
            return None
        result = []
        for peer in sorted(self.adjacency[node_id]):
            host, port = self.address_of(peer)
            result.append({"id": peer, "host": host, "port": port})
        return result

    def register(self, node_id):
        neighbors = self.neighbors_of(node_id)
        if neighbors is not None:
            self.registered[node_id] = time.time()
            log.info("registo de %s -> vizinhos %s", node_id,
                     [n["id"] for n in neighbors])
        return neighbors
