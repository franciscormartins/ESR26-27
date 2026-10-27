"""Modelo de estado da tabela de rotas (Etapa 2).

Esta versão define e mantém o estado local por (servidor, fluxo). A troca de
ANNOUNCE/ACTIVATE/DEACTIVATE e o encaminhamento ainda não estão implementados.
"""
from dataclasses import dataclass, field
from typing import Optional, Set, Tuple

INACTIVE = "inativa"
ACTIVE = "ativa"


@dataclass
class RouteEntry:
    """Estado local de uma rota para um fluxo originado num servidor.

    ``upstream`` é o vizinho no melhor caminho conhecido em direção ao
    servidor; ``destinations`` são vizinhos a jusante que pediram o fluxo.
    ``seen_announcements`` evita reprocessar anúncios repetidos.
    """
    flow: str
    origin: str
    metric: float
    upstream: Optional[str] = None
    destinations: Set[str] = field(default_factory=set)
    state: str = INACTIVE
    seen_announcements: Set[str] = field(default_factory=set)

    @property
    def key(self):
        """Chave única no nó: servidor de origem e identificador do fluxo."""
        return self.origin, self.flow


class RoutingTable:
    """Tabela de rotas indexada por ``(origin, flow)``."""
    def __init__(self):
        self.routes: dict[Tuple[str, str], RouteEntry] = {}

    def get(self, origin, flow):
        return self.routes.get((origin, flow))

    def put(self, entry):
        if not isinstance(entry, RouteEntry):
            raise TypeError("entry tem de ser RouteEntry")
        if not entry.origin or not entry.flow:
            raise ValueError("origin e flow não podem ser vazios")
        if entry.metric < 0:
            raise ValueError("metric não pode ser negativa")
        self.routes[entry.key] = entry
        return entry

    def remove(self, origin, flow):
        return self.routes.pop((origin, flow), None)

    def attach(self, node):
        """Ponto de integração reservado para registar handlers no nó overlay."""
        raise NotImplementedError("handlers de rota serão implementados na fase seguinte")
