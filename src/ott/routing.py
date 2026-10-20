"""Etapa 2 - tabela de rotas por fluxo e anúncios (por implementar).

Uma entrada por fluxo: {Fluxo, Origem, Métrica, Destinos, Estado}.
O nó regista handlers para ANNOUNCE/ACTIVATE/DEACTIVATE através de
OverlayNode.register_handler e usa node.send/node.broadcast para os reenviar.
"""
from dataclasses import dataclass, field

INACTIVE = "inativa"
ACTIVE = "ativa"


@dataclass
class RouteEntry:
    flow: str
    origin: str
    metric: float
    destinations: set = field(default_factory=set)
    state: str = INACTIVE


class RoutingTable:
    def __init__(self):
        self.routes = {}  # flow -> RouteEntry

    def attach(self, node):
        raise NotImplementedError
