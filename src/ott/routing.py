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
        destinations: IDs dos vizinhos a jusante com pelo menos um subscritor.
        subscriptions: Mapa ``vizinho_a_jusante -> conjunto de subscriber_id``.
            Permite remover um cliente sem apagar o ramo usado por outros.
        local_subscribers: Clientes/subscrições terminados neste nó localmente.
        state: ``ativa`` quando há subscritores e um caminho válido para a origem;
            caso contrário, ``inativa``.
        seen_announcements: Identidades ``servidor:fluxo:epoch`` processadas.
        epoch: Época mais recente do anúncio que atualizou esta rota.
    """
    flow: str
    origin: str
    metric: float
    upstream: Optional[str] = None
    destinations: Set[str] = field(default_factory=set)
    subscriptions: dict = field(default_factory=dict)
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
    def _subscriber_ids(route):
        return set(route.local_subscribers).union(
            *(set(subscribers) for subscribers in route.subscriptions.values()))

    @staticmethod
    def _has_subscriber(route, subscriber):
        return (subscriber in route.local_subscribers or
                any(subscriber in subscribers
                    for subscribers in route.subscriptions.values()))

    @staticmethod
    def _subscriber_count(route):
        return len(route.local_subscribers) + sum(
            len(subscribers) for subscribers in route.subscriptions.values())

    @staticmethod
    def _refresh_state(route, node_id=None):
        route.destinations = {neighbor for neighbor, subscribers
                              in route.subscriptions.items() if subscribers}
        path_valid = route.origin == node_id or route.upstream is not None
        route.state = (ACTIVE if path_valid and RoutingTable._subscriber_count(route)
                       else INACTIVE)

    @staticmethod
    def _route_message(node, msg_type, route, subscriber_id):
        return make_msg(msg_type, node.id, server_id=route.origin,
                        flow=route.flow, subscriber_id=subscriber_id)

    def activate(self, node, origin, flow, subscriber_id=None):
        """Ativa localmente um fluxo e envia o pedido pelo montante, se existir.

        ``subscriber_id`` identifica o cliente até ao servidor; por omissão usa-se
        o ID overlay do próprio nó.
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
            route.local_subscribers.add(subscriber_id)
            self._refresh_state(route, node.id)
            upstream = route.upstream
        if upstream is None:
            return True
        msg = self._route_message(node, MsgType.ACTIVATE, route, subscriber_id)
        if node.send(upstream, msg):
            return True
        with self._lock:
            route.local_subscribers.discard(subscriber_id)
            self._refresh_state(route, node.id)
        return False

    def deactivate(self, node, origin, flow, subscriber_id=None):
        """Remove a subscrição local e desativa o percurso se ficar sem clientes."""
        subscriber_id = node.id if subscriber_id is None else subscriber_id
        if not isinstance(subscriber_id, str) or not subscriber_id:
            raise ValueError("subscriber_id tem de ser uma string não vazia")
        with self._lock:
            route = self.routes.get((origin, flow))
            if route is None or subscriber_id not in route.local_subscribers:
                return False
            route.local_subscribers.remove(subscriber_id)
            self._refresh_state(route, node.id)
            upstream = route.upstream
        if upstream is None:
            return True
        return node.send(upstream, self._route_message(
            node, MsgType.DEACTIVATE, route, subscriber_id))

    def handle_activate(self, node, neighbor, msg):
        """Regista o cliente no ramo e propaga a subscrição uma só vez."""
        data = msg["data"]
        origin, flow, subscriber = (data["server_id"], data["flow"],
                                    data["subscriber_id"])
        with self._lock:
            route = self.routes.get((origin, flow))
            if route is None or neighbor == route.upstream:
                return False
            subscribers = route.subscriptions.setdefault(neighbor, set())
            if self._has_subscriber(route, subscriber):
                # Mesmo cliente vindo por outro ramo: guardar o ramo local,
                # mas não criar uma subscrição duplicada a montante.
                subscribers.add(subscriber)
                self._refresh_state(route, node.id)
                return True
            subscribers.add(subscriber)
            self._refresh_state(route, node.id)
            upstream = route.upstream
        if upstream is None:
            return True
        forwarded = self._route_message(node, MsgType.ACTIVATE, route, subscriber)
        if node.send(upstream, forwarded):
            return True
        with self._lock:
            route.subscriptions.get(neighbor, set()).discard(subscriber)
            if not route.subscriptions.get(neighbor):
                route.subscriptions.pop(neighbor, None)
            self._refresh_state(route, node.id)
        return False

    def handle_deactivate(self, node, neighbor, msg):
        """Remove o cliente do ramo e propaga a desativação se já não subsistir."""
        data = msg["data"]
        origin, flow, subscriber = (data["server_id"], data["flow"],
                                    data["subscriber_id"])
        with self._lock:
            route = self.routes.get((origin, flow))
            if route is None:
                return False
            subscribers = route.subscriptions.get(neighbor)
            if not subscribers or subscriber not in subscribers:
                return True  # pedido repetido: estado já está desativado
            subscribers.remove(subscriber)
            if not subscribers:
                route.subscriptions.pop(neighbor, None)
            still_subscribed = self._has_subscriber(route, subscriber)
            self._refresh_state(route, node.id)
            upstream = route.upstream if not still_subscribed else None
        if upstream is None:
            return True
        forwarded = self._route_message(node, MsgType.DEACTIVATE, route, subscriber)
        if node.send(upstream, forwarded):
            return True
        return False

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
            subscribers = self._subscriber_ids(route) if old_upstream != neighbor else set()
            changed_upstream = old_upstream != neighbor
            forwarded = make_msg(MsgType.ANNOUNCE, node.id, server_id=origin,
                                 flow=flow, epoch=epoch, metric=route.metric)
        if changed_upstream and subscribers:
            if old_upstream is not None:
                for subscriber in subscribers:
                    node.send(old_upstream, self._route_message(
                        node, MsgType.DEACTIVATE, route, subscriber))
            for subscriber in subscribers:
                node.send(neighbor, self._route_message(
                    node, MsgType.ACTIVATE, route, subscriber))
        node.broadcast(forwarded, exclude={neighbor})
        return True

    def handle_neighbor_down(self, node, neighbor):
        """Invalida montantes caídos e remove subscrições do ramo desligado."""
        deactivations = []
        with self._lock:
            for route in self.routes.values():
                removed = set(route.subscriptions.pop(neighbor, set()))
                upstream_lost = route.upstream == neighbor
                if upstream_lost:
                    route.upstream = None
                    route.metric = math.inf
                self._refresh_state(route, node.id)
                if not upstream_lost and route.upstream is not None:
                    for subscriber in removed:
                        if not self._has_subscriber(route, subscriber):
                            deactivations.append((route, route.upstream, subscriber))
        for route, upstream, subscriber in deactivations:
            node.send(upstream, self._route_message(
                node, MsgType.DEACTIVATE, route, subscriber))

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
                identity = f"{self.node.id}:{flow}:{epoch}"
                route = self.routing_table.get(self.node.id, flow)
                if route is None:
                    route = RouteEntry(flow=flow, origin=self.node.id, metric=0)
                    self.routing_table.put(route)
                route.metric, route.upstream, route.epoch = 0, None, epoch
                route.seen_announcements.add(identity)
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
