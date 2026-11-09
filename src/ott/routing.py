"""Modelo e aprendizagem de rotas por anúncios (Etapa 2)."""
from dataclasses import dataclass, field
import logging
import math
import queue
import threading
import time
from typing import Optional, Set, Tuple

from .protocol import MsgType, make_msg

INACTIVE = "inativa"
ACTIVE = "ativa"
log = logging.getLogger("ott.routing")


@dataclass
class RouteEntry:
    """Estado local de uma rota para um fluxo originado num servidor.

    Attributes:
        flow: Identificador estável do conteúdo ou stream.
        origin: ID overlay do servidor que origina o fluxo.
        metric: Custo acumulado desde o servidor; ``math.inf`` indica que não
            existe atualmente um caminho válido conhecido.
        upstream: Vizinho no melhor caminho conhecido em direção ao servidor;
            ``None`` no próprio servidor ou quando a rota está invalidada.
        destinations: IDs dos vizinhos a jusante que pediram o fluxo. As
            subscrições são agregadas por ramo: cada vizinho conta uma vez,
            seja qual for o número de clientes atrás dele.
        local_subscribers: Clientes/subscrições terminados neste nó localmente.
        state: ``ativa`` quando há procura (subscritores locais ou destinos) e
            um caminho válido para a origem; caso contrário, ``inativa``.
        seen_announcements: Identidades ``servidor:fluxo:epoch`` processadas.
        epoch: Época mais recente do anúncio que atualizou esta rota.
    """
    flow: str
    origin: str
    metric: float
    upstream: Optional[str] = None
    destinations: Set[str] = field(default_factory=set)
    local_subscribers: Set[str] = field(default_factory=set)
    state: str = INACTIVE
    seen_announcements: Set[str] = field(default_factory=set)
    epoch: int = 0

    @property
    def key(self):
        """Chave única no nó: servidor de origem e identificador do fluxo."""
        return self.origin, self.flow


class RoutingTable:
    """Tabela de rotas local, indexada por ``(origin, flow)``.

    ``attach(node)`` liga anúncios, subscrições e eventos de vizinhança ao nó.
    ``activate``/``deactivate`` iniciam e terminam subscrições locais; os handlers
    mantêm os ramos dos vizinhos e propagam alterações pelo caminho de montante.

    As subscrições são agregadas: um nó só envia ``ACTIVATE`` ao montante quando
    a procura passa de zero para um (primeiro subscritor local ou primeiro ramo
    a jusante) e ``DEACTIVATE`` quando volta a zero.
    """
    def __init__(self):
        self.routes: dict[Tuple[str, str], RouteEntry] = {}
        self._lock = threading.RLock()
        self._attached_nodes = set()
        self._events = queue.Queue()
        self._event_thread = None
        self._event_lock = threading.Lock()

    def get(self, origin, flow):
        with self._lock:
            return self.routes.get((origin, flow))

    def put(self, entry):
        if not isinstance(entry, RouteEntry):
            raise TypeError("entry tem de ser RouteEntry")
        if not entry.origin or not entry.flow:
            raise ValueError("origin e flow não podem ser vazios")
        if entry.metric < 0:
            raise ValueError("metric não pode ser negativa")
        with self._lock:
            self.routes[entry.key] = entry
        return entry

    def remove(self, origin, flow):
        with self._lock:
            return self.routes.pop((origin, flow), None)

    def active_destinations(self, origin, flow, exclude=()):
        """Devolve uma cópia dos vizinhos a jusante que recebem o fluxo.

        O conjunto é deduplicado por vizinho: vários clientes no mesmo ramo
        provocam apenas uma cópia do pacote nesse troço. ``exclude`` permite
        ao encaminhador retirar a ligação de entrada.
        """
        with self._lock:
            route = self.routes.get((origin, flow))
            if route is None or route.state != ACTIVE:
                return ()
            return tuple(sorted(route.destinations.difference(exclude)))

    def upstream_of(self, origin, flow):
        """Vizinho de montante atual da rota, ou ``None`` (origem ou sem caminho)."""
        with self._lock:
            route = self.routes.get((origin, flow))
            return None if route is None else route.upstream

    def has_local_subscription(self, origin, flow):
        """Indica se este nó tem um cliente local subscrito ao fluxo."""
        with self._lock:
            route = self.routes.get((origin, flow))
            return bool(route and route.state == ACTIVE and route.local_subscribers)

    def publish_local(self, origin, flow, epoch):
        """Instala/atualiza a rota de origem sob o bloqueio da tabela."""
        with self._lock:
            route = self.routes.get((origin, flow))
            if route is None:
                route = RouteEntry(flow=flow, origin=origin, metric=0)
                self.routes[route.key] = route
            route.metric = 0
            route.upstream = None
            route.epoch = epoch
            route.seen_announcements.add(f"{origin}:{flow}:{epoch}")
            self._refresh_state(route, origin)
            return route

    def attach(self, node):
        """Regista handlers e callbacks de vizinhança sem os bloquear."""
        node_key = id(node)
        with self._lock:
            if node_key in self._attached_nodes:
                return self
            self._attached_nodes.add(node_key)
        node.register_handler(MsgType.ANNOUNCE,
                              lambda current, neighbor, msg: self.handle_announce(
                                  current, neighbor, msg))
        node.register_handler(MsgType.ACTIVATE,
                              lambda current, neighbor, msg: self.handle_activate(
                                  current, neighbor, msg))
        node.register_handler(MsgType.DEACTIVATE,
                              lambda current, neighbor, msg: self.handle_deactivate(
                                  current, neighbor, msg))
        node.on_neighbor_up(lambda current, neighbor: self._queue_neighbor_event(
            "up", current, neighbor))
        node.on_neighbor_down(lambda current, neighbor: self._queue_neighbor_event(
            "down", current, neighbor))
        return self

    def _queue_neighbor_event(self, event, node, neighbor):
        self._events.put((event, node, neighbor))
        with self._event_lock:
            if self._event_thread is None or not self._event_thread.is_alive():
                self._event_thread = threading.Thread(
                    target=self._event_loop, name="ott-routing-events", daemon=True)
                self._event_thread.start()

    def _event_loop(self):
        while True:
            event, node, neighbor = self._events.get()
            try:
                if event == "up":
                    self.handle_neighbor_up(node, neighbor)
                else:
                    self.handle_neighbor_down(node, neighbor)
            except Exception:
                log.exception("erro ao tratar alteração do vizinho %s", neighbor)

    @staticmethod
    def _has_demand(route):
        """Há procura quando existe um subscritor local ou um ramo a jusante."""
        return bool(route.local_subscribers or route.destinations)

    @staticmethod
    def _refresh_state(route, node_id=None):
        path_valid = route.origin == node_id or route.upstream is not None
        route.state = (ACTIVE if path_valid and RoutingTable._has_demand(route)
                       else INACTIVE)

    @staticmethod
    def _route_message(node, msg_type, route, subscriber_id=None):
        fields = {"server_id": route.origin, "flow": route.flow}
        if subscriber_id is not None:  # informativo: quem abriu/fechou o ramo
            fields["subscriber_id"] = subscriber_id
        return make_msg(msg_type, node.id, **fields)

    # Nota sobre envios falhados: se ``node.send`` falhar, o nó descarta a
    # ligação e dispara ``neighbor_down``; a rota fica sem caminho mas mantém a
    # procura, e o ``ACTIVATE`` é reenviado ao montante aprendido a seguir
    # (``handle_announce``). Por isso o pedido fica registado mesmo assim.

    def activate(self, node, origin, flow, subscriber_id=None):
        """Regista um subscritor local e abre o ramo a montante, se for o primeiro.

        Devolve ``False`` se a rota ainda não é conhecida. ``subscriber_id`` é
        por omissão o ID overlay do nó e segue no pedido só como informação.
        """
        subscriber_id = node.id if subscriber_id is None else subscriber_id
        if not isinstance(subscriber_id, str) or not subscriber_id:
            raise ValueError("subscriber_id tem de ser uma string não vazia")
        with self._lock:
            route = self.routes.get((origin, flow))
            if route is None:
                return False
            if subscriber_id in route.local_subscribers:
                return True
            first = not self._has_demand(route)
            route.local_subscribers.add(subscriber_id)
            self._refresh_state(route, node.id)
            upstream = route.upstream if first else None
        if upstream is not None:
            node.send(upstream, self._route_message(
                node, MsgType.ACTIVATE, route, subscriber_id))
        return True

    def deactivate(self, node, origin, flow, subscriber_id=None):
        """Remove o subscritor local e fecha o ramo a montante se ficar sem procura."""
        subscriber_id = node.id if subscriber_id is None else subscriber_id
        if not isinstance(subscriber_id, str) or not subscriber_id:
            raise ValueError("subscriber_id tem de ser uma string não vazia")
        with self._lock:
            route = self.routes.get((origin, flow))
            if route is None or subscriber_id not in route.local_subscribers:
                return False
            route.local_subscribers.remove(subscriber_id)
            self._refresh_state(route, node.id)
            upstream = route.upstream if not self._has_demand(route) else None
        if upstream is not None:
            node.send(upstream, self._route_message(
                node, MsgType.DEACTIVATE, route, subscriber_id))
        return True

    def handle_activate(self, node, neighbor, msg):
        """Acrescenta o vizinho aos destinos e propaga só a primeira procura."""
        data = msg["data"]
        origin, flow = data["server_id"], data["flow"]
        with self._lock:
            route = self.routes.get((origin, flow))
            if route is None or neighbor == route.upstream:
                return False
            if neighbor in route.destinations:
                return True  # ramo já aberto: pedido repetido
            first = not self._has_demand(route)
            route.destinations.add(neighbor)
            self._refresh_state(route, node.id)
            upstream = route.upstream if first else None
        if upstream is not None:
            node.send(upstream, self._route_message(
                node, MsgType.ACTIVATE, route, data.get("subscriber_id")))
        return True

    def handle_deactivate(self, node, neighbor, msg):
        """Retira o vizinho dos destinos e fecha o ramo a montante se ficar vazio."""
        data = msg["data"]
        origin, flow = data["server_id"], data["flow"]
        with self._lock:
            route = self.routes.get((origin, flow))
            if route is None:
                return False
            if neighbor not in route.destinations:
                return True  # pedido repetido: o ramo já está fechado
            route.destinations.discard(neighbor)
            self._refresh_state(route, node.id)
            upstream = route.upstream if not self._has_demand(route) else None
        if upstream is not None:
            node.send(upstream, self._route_message(
                node, MsgType.DEACTIVATE, route, data.get("subscriber_id")))
        return True

    def handle_announce(self, node, neighbor, msg):
        """Aprende um caminho e propaga apenas rotas novas ou melhoradas."""
        data = msg["data"]  # OverlayNode já validou a mensagem recebida.
        origin, flow, epoch = data["server_id"], data["flow"], data["epoch"]
        if origin == node.id:  # não aprender o próprio anúncio ao fechar um ciclo
            return False
        candidate = data["metric"] + 1
        identity = f"{origin}:{flow}:{epoch}"
        with self._lock:
            route = self.routes.get((origin, flow))
            if route is not None and neighbor in route.destinations:
                return False  # não escolher um ramo a jusante como montante
            if route is not None and epoch < route.epoch:
                return False
            old_upstream = route.upstream if route is not None else None
            if route is None or epoch > route.epoch:
                if route is None:
                    route = RouteEntry(flow=flow, origin=origin, metric=candidate,
                                       upstream=neighbor, epoch=epoch)
                    self.routes[route.key] = route
                else:
                    route.metric = candidate
                    route.upstream = neighbor
                    route.epoch = epoch
                route.seen_announcements.add(identity)
            elif candidate < route.metric:
                route.metric = candidate
                route.upstream = neighbor
                route.seen_announcements.add(identity)
            else:
                route.seen_announcements.add(identity)
                return False
            self._refresh_state(route, node.id)
            # O ramo agregado muda de montante: um só DEACTIVATE/ACTIVATE.
            move_branch = old_upstream != neighbor and self._has_demand(route)
            forwarded = make_msg(MsgType.ANNOUNCE, node.id, server_id=origin,
                                 flow=flow, epoch=epoch, metric=route.metric)
        if move_branch:
            if old_upstream is not None:
                node.send(old_upstream, self._route_message(
                    node, MsgType.DEACTIVATE, route))
            node.send(neighbor, self._route_message(node, MsgType.ACTIVATE, route))
        node.broadcast(forwarded, exclude={neighbor})
        return True

    def handle_neighbor_down(self, node, neighbor):
        """Invalida montantes caídos e remove o ramo do vizinho desligado."""
        deactivations = []
        with self._lock:
            for route in self.routes.values():
                removed = neighbor in route.destinations
                route.destinations.discard(neighbor)
                upstream_lost = route.upstream == neighbor
                if upstream_lost:
                    route.upstream = None
                    route.metric = math.inf
                self._refresh_state(route, node.id)
                if (removed and not upstream_lost and route.upstream is not None
                        and not self._has_demand(route)):
                    deactivations.append((route, route.upstream))
        for route, upstream in deactivations:
            node.send(upstream, self._route_message(node, MsgType.DEACTIVATE, route))

    def handle_neighbor_up(self, node, neighbor):
        """Envia ao novo vizinho anúncios das rotas válidas já conhecidas."""
        announcements = []
        with self._lock:
            for route in self.routes.values():
                if route.origin == node.id:
                    valid = True
                else:
                    valid = route.upstream is not None
                if valid and route.epoch > 0 and math.isfinite(route.metric) \
                        and neighbor != route.upstream:
                    announcements.append(make_msg(
                        MsgType.ANNOUNCE, node.id, server_id=route.origin,
                        flow=route.flow, epoch=route.epoch, metric=route.metric))
        for announcement in announcements:
            node.send(neighbor, announcement)


class AnnouncementPublisher:
    """Publica periodicamente anúncios dos fluxos originados neste nó.

    A publicação não depende de os vizinhos já estarem ligados: os anúncios são
    repetidos periodicamente. Os nós recetores aprendem e propagam os anúncios
    através de ``RoutingTable.attach``.
    """
    def __init__(self, node, flows, interval=5.0, routing_table=None):
        self.node = node
        if isinstance(flows, str):
            raise ValueError("flows tem de ser uma coleção de identificadores, não uma string")
        self.flows = tuple(flows)
        if not self.flows or any(not isinstance(flow, str) or not flow for flow in self.flows):
            raise ValueError("flows tem de conter pelo menos um identificador não vazio")
        if len(set(self.flows)) != len(self.flows):
            raise ValueError("flows não pode conter identificadores duplicados")
        if not isinstance(interval, (int, float)) or isinstance(interval, bool) \
                or not math.isfinite(interval) or interval <= 0:
            raise ValueError("interval tem de ser um número finito maior que zero")
        self.interval = float(interval)
        self.routing_table = routing_table
        self._epochs = {flow: 0 for flow in self.flows}
        self._stop = threading.Event()
        self._thread = None

    def publish_once(self):
        """Anuncia uma vez cada fluxo e devolve as mensagens publicadas."""
        messages = []
        for flow in self.flows:
            self._epochs[flow] += 1
            epoch = self._epochs[flow]
            if self.routing_table is not None:
                self.routing_table.publish_local(self.node.id, flow, epoch)
            msg = make_msg(MsgType.ANNOUNCE, self.node.id,
                           server_id=self.node.id, flow=flow,
                           epoch=epoch, metric=0)
            self.node.broadcast(msg)
            messages.append(msg)
            log.debug("anúncio do fluxo %s, época %d", flow, self._epochs[flow])
        return messages

    def start(self):
        """Publica imediatamente e inicia a publicação periódica em background."""
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop.clear()
        self.publish_once()
        self._thread = threading.Thread(target=self._run, name="ott-announcer", daemon=True)
        self._thread.start()
        return self

    def _run(self):
        while not self._stop.wait(self.interval):
            self.publish_once()

    def stop(self):
        """Pára a publicação e aguarda o thread terminar."""
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=self.interval + 1.0)
        self._thread = None
