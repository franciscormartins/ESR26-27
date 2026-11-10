"""Modelo e aprendizagem de rotas por anúncios (Etapa 2)."""
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
import logging
import math
import queue
import threading
import time
from typing import Dict, NamedTuple, Optional, Set, Tuple

from .protocol import MsgType, make_msg

INACTIVE = "inativa"
ACTIVE = "ativa"
log = logging.getLogger("ott.routing")


class Cost(NamedTuple):
    """Custo de um caminho até ao servidor: saltos e atraso acumulado (s)."""
    hops: float
    delay: float


@dataclass(frozen=True)
class Candidate:
    """Último anúncio recebido de um vizinho para uma rota.

    ``metric`` e ``delay`` são os valores anunciados pelo vizinho (antes de
    somar o salto e o atraso da ligação até ele); ``delay`` é infinito se o
    vizinho não o conhecia. ``seen_at`` (relógio monotónico) é a hora da última
    receção, usada pelo tempo de vida; não conta para a igualdade.
    """
    epoch: int
    metric: float
    delay: float = math.inf
    seen_at: float = field(default_factory=time.monotonic, compare=False)


@dataclass
class RouteEntry:
    """Estado local de uma rota para um fluxo originado num servidor.

    Attributes:
        flow: Identificador estável do conteúdo ou stream.
        origin: ID overlay do servidor que origina o fluxo.
        metric: Número de saltos até ao servidor (desempate e condição de
            viabilidade; a métrica principal é ``delay``); ``math.inf`` indica
            que não existe atualmente um caminho válido conhecido.
        upstream: Vizinho no melhor caminho conhecido em direção ao servidor;
            ``None`` no próprio servidor ou quando a rota está invalidada.
        destinations: IDs dos vizinhos a jusante que pediram o fluxo. As
            subscrições são agregadas por ramo: cada vizinho conta uma vez,
            seja qual for o número de clientes atrás dele.
        local_subscribers: Clientes/subscrições terminados neste nó localmente.
        state: ``ativa`` quando há procura (subscritores locais ou destinos) e
            um caminho válido para a origem; caso contrário, ``inativa``.
        epoch: Época mais recente conhecida para esta rota.
        delay: Atraso acumulado estimado até ao servidor (s), pela soma do
            custo de cada ligação (metade do RTT); métrica principal da
            escolha do montante. ``math.inf`` se desconhecido.
        candidates: Mapa ``vizinho -> Candidate`` com o último anúncio de cada
            vizinho; o montante é escolhido entre eles.
        withdrawn_at: Hora (relógio monotónico) do último ``WITHDRAW`` deste nó
            para a rota, ou ``None``. Enquanto a rota está retirada, só aceita
            épocas mais recentes do que ``epoch``; ao fim de ``route_ttl``
            volta a aceitar qualquer época.
        feasibility: Melhor anúncio feito por este nó na época mais recente
            que anunciou, ``(época, atraso, saltos)``, ou ``None`` se ainda não
            anunciou (ou se retirou a rota). É a referência da condição de
            viabilidade (D34): só servem candidatos de uma época mais recente
            ou da mesma época com ``(atraso, saltos)`` estritamente menor.
    """
    flow: str
    origin: str
    metric: float
    upstream: Optional[str] = None
    destinations: Set[str] = field(default_factory=set)
    local_subscribers: Set[str] = field(default_factory=set)
    state: str = INACTIVE
    epoch: int = 0
    delay: float = math.inf
    candidates: Dict[str, Candidate] = field(default_factory=dict)
    withdrawn_at: Optional[float] = None
    feasibility: Optional[Tuple[int, float, float]] = None

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

    O montante é escolhido entre os candidatos viáveis (último anúncio de cada
    vizinho) pela menor latência acumulada (D34); latências praticamente iguais
    desempatam por menos saltos e depois pelo ID do vizinho. Só se troca de
    montante por latência se a melhoria passar a histerese (``delay_hysteresis``
    relativo e ``delay_min_gain`` absoluto, em s); ver ``_select``.

    O custo de uma ligação é metade do RTT medido, com o mínimo
    ``min_link_delay`` (s); sem RTT medido vale ``unknown_link_delay`` (s), um
    valor alto mas finito, para que caminhos com ligações por medir continuem
    comparáveis (no arranque a escolha comporta-se como a contagem de saltos).

    ``route_ttl`` (s) é a rede de segurança: um candidato sem anúncios há mais
    do que esse tempo é esquecido e, se era o montante, a rota é reparada ou
    retirada como numa queda. ``None`` desliga a expiração.

    Envios (D28): as mensagens são decididas sob o bloqueio e entram nessa
    altura numa fila única; são enviadas fora do bloqueio, por ordem, por uma
    thread de cada vez. Assim, decisões de threads diferentes (por exemplo, um
    ``ACTIVATE`` e o ``DEACTIVATE`` de uma troca de montante logo a seguir)
    chegam ao vizinho pela ordem em que foram tomadas.
    """
    def __init__(self, delay_hysteresis=0.2, delay_min_gain=0.001, route_ttl=None,
                 unknown_link_delay=1.0, min_link_delay=0.0001):
        def positive(value):
            return (isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(value) and value > 0)

        if route_ttl is not None and not positive(route_ttl):
            raise ValueError("route_ttl tem de ser um número finito maior que zero")
        for name, value in (("unknown_link_delay", unknown_link_delay),
                            ("min_link_delay", min_link_delay)):
            if not positive(value):
                raise ValueError(f"{name} tem de ser um número finito maior que zero")
        self.delay_hysteresis = delay_hysteresis
        self.delay_min_gain = delay_min_gain
        self.route_ttl = route_ttl
        self.unknown_link_delay = float(unknown_link_delay)
        self.min_link_delay = float(min_link_delay)
        self._expiry_stop = threading.Event()
        self._expiry_thread = None
        self.routes: dict[Tuple[str, str], RouteEntry] = {}
        self._lock = threading.RLock()
        self._attached_nodes = set()
        self._events = queue.Queue()
        self._event_thread = None
        self._event_lock = threading.Lock()
        self._outbox = deque()             # (nó, ação), pela ordem das decisões
        self._send_lock = threading.Lock()  # um só a enviar de cada vez
        self._depth = threading.local()     # transações aninhadas na mesma thread

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
            route.delay = 0.0
            route.upstream = None
            route.epoch = epoch
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
        node.register_handler(MsgType.WITHDRAW,
                              lambda current, neighbor, msg: self.handle_withdraw(
                                  current, neighbor, msg))
        node.on_neighbor_up(lambda current, neighbor: self._queue_neighbor_event(
            "up", current, neighbor))
        node.on_neighbor_down(lambda current, neighbor: self._queue_neighbor_event(
            "down", current, neighbor))
        if self.route_ttl is not None and self._expiry_thread is None:
            self._expiry_thread = threading.Thread(
                target=self._expiry_loop, args=(node,), name="ott-routing-ttl",
                daemon=True)
            self._expiry_thread.start()
        return self

    def close(self):
        """Pára a verificação periódica do tempo de vida."""
        self._expiry_stop.set()
        if self._expiry_thread is not None:
            self._expiry_thread.join(timeout=2.0)

    def _expiry_loop(self, node):
        # Verifica com uma granularidade de 1/4 do tempo de vida (máx. 1 s).
        period = min(self.route_ttl / 4, 1.0)
        while not self._expiry_stop.wait(period):
            try:
                self.expire(node)
            except Exception:
                log.exception("erro ao expirar rotas")

    def expire(self, node, now=None):
        """Esquece candidatos sem anúncios há mais de ``route_ttl`` segundos.

        Se o candidato esquecido era o montante, trata-se como uma perda do
        montante (reparação rápida ou ``WITHDRAW``). Devolve o número de
        candidatos esquecidos. Sem ``route_ttl`` não faz nada.
        """
        if self.route_ttl is None:
            return 0
        now = time.monotonic() if now is None else now
        expired = 0
        with self._transaction(node) as actions:
            for route in self.routes.values():
                if route.origin == node.id:
                    continue
                stale = [nb for nb, c in route.candidates.items()
                         if now - c.seen_at > self.route_ttl]
                if not stale:
                    continue
                expired += len(stale)
                upstream_stale = route.upstream in stale
                for neighbor in stale:
                    if neighbor != route.upstream:
                        route.candidates.pop(neighbor)
                if upstream_stale:
                    log.info("rota %s:%s: montante %s sem anúncios há mais de %.1f s",
                             route.origin, route.flow, route.upstream, self.route_ttl)
                    # o candidato do montante é retirado em _lose_upstream; a
                    # reparação só usa candidatos ainda dentro do tempo de vida
                    actions += self._lose_upstream(node, route, still_connected=True)
        return expired

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
        with self._transaction(node) as actions:
            route = self.routes.get((origin, flow))
            if route is None:
                return False
            if subscriber_id in route.local_subscribers:
                return True
            first = not self._has_demand(route)
            route.local_subscribers.add(subscriber_id)
            self._refresh_state(route, node.id)
            if first and route.upstream is not None:
                actions.append(("send", route.upstream, self._route_message(
                    node, MsgType.ACTIVATE, route, subscriber_id)))
        return True

    def deactivate(self, node, origin, flow, subscriber_id=None):
        """Remove o subscritor local e fecha o ramo a montante se ficar sem procura."""
        subscriber_id = node.id if subscriber_id is None else subscriber_id
        if not isinstance(subscriber_id, str) or not subscriber_id:
            raise ValueError("subscriber_id tem de ser uma string não vazia")
        with self._transaction(node) as actions:
            route = self.routes.get((origin, flow))
            if route is None or subscriber_id not in route.local_subscribers:
                return False
            route.local_subscribers.remove(subscriber_id)
            self._refresh_state(route, node.id)
            if not self._has_demand(route) and route.upstream is not None:
                actions.append(("send", route.upstream, self._route_message(
                    node, MsgType.DEACTIVATE, route, subscriber_id)))
        return True

    def handle_activate(self, node, neighbor, msg):
        """Acrescenta o vizinho aos destinos e propaga só a primeira procura."""
        data = msg["data"]
        origin, flow = data["server_id"], data["flow"]
        with self._transaction(node) as actions:
            route = self.routes.get((origin, flow))
            if route is None or neighbor == route.upstream:
                return False
            if neighbor in route.destinations:
                return True  # ramo já aberto: pedido repetido
            first = not self._has_demand(route)
            route.destinations.add(neighbor)
            self._refresh_state(route, node.id)
            if first and route.upstream is not None:
                actions.append(("send", route.upstream, self._route_message(
                    node, MsgType.ACTIVATE, route, data.get("subscriber_id"))))
        return True

    def handle_deactivate(self, node, neighbor, msg):
        """Retira o vizinho dos destinos e fecha o ramo a montante se ficar vazio."""
        data = msg["data"]
        origin, flow = data["server_id"], data["flow"]
        with self._transaction(node) as actions:
            route = self.routes.get((origin, flow))
            if route is None:
                return False
            if neighbor not in route.destinations:
                return True  # pedido repetido: o ramo já está fechado
            route.destinations.discard(neighbor)
            self._refresh_state(route, node.id)
            if not self._has_demand(route) and route.upstream is not None:
                actions.append(("send", route.upstream, self._route_message(
                    node, MsgType.DEACTIVATE, route, data.get("subscriber_id"))))
        return True

    # -- seleção do montante (métrica: latência; desempate: saltos, ID) -------

    def _link_delay(self, node, neighbor):
        """Custo da ligação ao vizinho (s): metade do RTT medido, nunca abaixo de
        ``min_link_delay``; sem amostra de RTT, ``unknown_link_delay``.

        É o único sítio que traduz medições da ligação em custo: outra métrica
        de ligação (por exemplo, com perdas) muda só esta função.
        """
        rtt = getattr(node, "rtt", None)
        sample = rtt(neighbor) if callable(rtt) else None
        if sample is None:
            return self.unknown_link_delay
        return max(sample / 2, self.min_link_delay)

    def _cost(self, node, neighbor, candidate):
        """Custo do caminho via ``neighbor``: ``Cost(saltos, atraso acumulado)``.

        Um anúncio sem ``delay`` (vizinho que não o conhece) dá atraso infinito.
        """
        return Cost(candidate.metric + 1,
                    candidate.delay + self._link_delay(node, neighbor))

    def _hold_down_over(self, route, now=None):
        """A retenção depois de um ``WITHDRAW`` dura ``route_ttl`` (sem fim se ``None``)."""
        if self.route_ttl is None:
            return False
        now = time.monotonic() if now is None else now
        return now - route.withdrawn_at > self.route_ttl

    @staticmethod
    def _valid_candidate(route, neighbor, candidate):
        """Candidatos da época atual; o montante atual vale também com a anterior.

        Assim, numa época nova a primeira cópia a chegar não derruba o montante
        atual antes de este ter tido tempo de enviar a sua cópia.
        """
        if neighbor in route.destinations:
            return False  # um ramo a jusante nunca é montante
        if candidate.epoch == route.epoch:
            return True
        return neighbor == route.upstream and candidate.epoch == route.epoch - 1

    @staticmethod
    def _feasible(route, candidate):
        """Condição de viabilidade (D34), como no Babel.

        Serve um candidato de uma época mais recente do que a última anunciada
        por este nó, ou da mesma época com ``(atraso, saltos)`` anunciados
        estritamente menores do que o melhor que este nó anunciou nela. Ao
        longo de um caminho ``(atraso, saltos)`` cresce sempre (cada ligação
        soma pelo menos um salto), por isso um vizinho cujo caminho passa por
        este nó nunca é viável: não se formam ciclos, mesmo que o RTT mude sem
        chegar nenhum anúncio. Usa os valores anunciados, não o custo local.
        """
        reference = route.feasibility
        if reference is None or candidate.epoch > reference[0]:
            return True
        return (candidate.epoch == reference[0]
                and (candidate.delay, candidate.metric) < reference[1:])

    def _delay_better(self, new, current):
        """Atraso ``new`` melhor do que ``current`` acima da histerese."""
        if math.isinf(new):
            return False
        if math.isinf(current):
            return True
        gain = current - new
        return gain > max(self.delay_min_gain, self.delay_hysteresis * current)

    def _tie_band(self, delay):
        """Diferença de atraso que conta como empate: metade da histerese.

        Entre metade e a histerese completa nenhum dos lados troca; sem esta
        folga, um RTT a oscilar no limiar alternava o montante.
        """
        return max(self.delay_min_gain, self.delay_hysteresis * delay) / 2

    def _select(self, node, route):
        """Escolhe o montante entre os candidatos válidos e viáveis (D34).

        1. ``D*`` é o menor atraso; contam como empatados os candidatos a menos
           de ``_tie_band(D*)`` dele (com ``D*`` infinito, todos). Entre estes,
           o preferido é o de menos saltos, depois menor atraso, depois menor ID.
        2. Sem montante atual (ou se ele deixou de servir), fica o preferido.
        3. Com montante atual, troca-se para o preferido só se ``D*`` for melhor
           do que o atraso atual acima da histerese, se o preferido tiver menos
           saltos (latência praticamente igual), ou em empate total (mesmos
           saltos e o mesmo atraso, por exemplo ambos desconhecidos) se tiver
           menor ID. Caso contrário mantém-se o atual.

        Devolve ``(vizinho, Cost)`` ou ``(None, None)``.
        """
        options = {}
        for neighbor, candidate in route.candidates.items():
            if self._valid_candidate(route, neighbor, candidate) \
                    and self._feasible(route, candidate):
                options[neighbor] = self._cost(node, neighbor, candidate)
        if not options:
            return None, None
        best_delay = min(cost.delay for cost in options.values())
        if math.isinf(best_delay):
            tied = options
        else:
            band = self._tie_band(best_delay)
            tied = {nb: cost for nb, cost in options.items()
                    if cost.delay - best_delay <= band}
        best = min(tied, key=lambda nb: (tied[nb].hops, tied[nb].delay, nb))
        best_cost = options[best]
        current = route.upstream
        current_cost = options.get(current)
        if current_cost is None or best == current:
            return best, best_cost
        if self._delay_better(best_delay, current_cost.delay):
            return best, best_cost      # latência claramente melhor
        if best_cost.hops < current_cost.hops:
            return best, best_cost      # latência praticamente igual, menos saltos
        if best_cost == current_cost and best < current:
            return best, best_cost      # empate total: desempate determinista pelo ID
        return current, current_cost

    @staticmethod
    def _advertised_epoch(route):
        """Época que este nó anuncia: a do anúncio do montante escolhido.

        Enquanto o montante atual só tem o anúncio da época anterior, o nó
        continua a anunciar essa época e só propaga a nova quando o caminho
        escolhido a trouxer (D34). Anunciar a época nova com o caminho antigo
        tornava esse montante inviável (``_feasible``) e obrigava a trocar
        para a primeira cópia a chegar, a oscilação que a D21 eliminou.
        """
        candidate = route.candidates.get(route.upstream) if route.upstream else None
        return route.epoch if candidate is None else candidate.epoch

    def _advertises_new_epoch(self, route):
        """O montante escolhido traz uma época que este nó ainda não anunciou."""
        return (route.feasibility is None
                or self._advertised_epoch(route) > route.feasibility[0])

    def _announce_msg(self, node, route):
        """Anúncio da rota; atualiza a referência da condição de viabilidade."""
        epoch = self._advertised_epoch(route)
        if route.origin != node.id:
            key = (route.delay, route.metric)
            reference = route.feasibility
            if reference is None or epoch > reference[0] \
                    or (epoch == reference[0] and key < reference[1:]):
                route.feasibility = (epoch, *key)
        fields = {"server_id": route.origin, "flow": route.flow,
                  "epoch": epoch, "metric": route.metric}
        if math.isfinite(route.delay):
            fields["delay"] = route.delay
        return make_msg(MsgType.ANNOUNCE, node.id, **fields)

    @contextmanager
    def _transaction(self, node):
        """Bloqueio da tabela + envios pela ordem das decisões (D28).

        Dá uma lista onde o chamador junta as ações ``("send", vizinho, msg)``
        e ``("broadcast", msg, excluídos)``. Ainda sob o bloqueio, as ações
        entram na fila global; depois de o libertar, a fila é esvaziada por
        ordem. Também corre quando o bloco termina com ``return``.
        """
        actions = []
        depth = getattr(self._depth, "value", 0)
        self._depth.value = depth + 1
        try:
            with self._lock:
                yield actions
                self._outbox.extend((node, action) for action in actions)
        finally:
            self._depth.value = depth
        if depth == 0:  # nunca enviar com o bloqueio da tabela na mão
            self._flush()

    def _flush(self):
        """Envia a fila por ordem; só uma thread envia de cada vez.

        Uma thread que chega enquanto outra está a enviar espera e encontra a
        fila já vazia ou com as suas ações atrás das anteriores.
        """
        with self._send_lock:
            while True:
                with self._lock:
                    if not self._outbox:
                        return
                    node, action = self._outbox.popleft()
                try:
                    if action[0] == "send":
                        node.send(action[1], action[2])
                    else:
                        node.broadcast(action[1], exclude=action[2])
                except Exception:
                    log.exception("falha ao enviar %s", action[0])

    # -- anúncios ------------------------------------------------------------

    def handle_announce(self, node, neighbor, msg):
        """Regista o candidato, reavalia o montante e propaga se algo mudou.

        Propaga quando o montante escolhido traz uma época que este nó ainda
        não anunciou (uma vez por ronda), quando o montante muda ou quando muda
        o número de saltos. Uma atualização do próprio montante que deixe de
        ser viável é tratada como perda do montante. Devolve ``True`` se
        enviou um anúncio ou um ``WITHDRAW``.
        """
        data = msg["data"]  # OverlayNode já validou a mensagem recebida.
        origin, flow, epoch = data["server_id"], data["flow"], data["epoch"]
        if origin == node.id:  # não aprender o próprio anúncio ao fechar um ciclo
            return False
        candidate = Candidate(epoch, data["metric"], data.get("delay", math.inf))
        with self._transaction(node) as actions:
            route = self.routes.get((origin, flow))
            if route is not None and neighbor in route.destinations:
                return False  # não escolher um ramo a jusante como montante
            if route is None:
                route = RouteEntry(flow=flow, origin=origin, metric=math.inf)
                self.routes[route.key] = route
            if route.upstream is None and route.withdrawn_at is not None \
                    and epoch <= route.epoch:
                if not self._hold_down_over(route):
                    # Anúncio da época retirada ou anterior, ainda em trânsito:
                    # aceitá-lo podia formar um ciclo fantasma (L9).
                    return False
                # Fim da retenção: aceita qualquer época (ex.: servidor
                # reiniciado com o relógio atrasado).
                route.epoch = 0
                route.candidates.clear()
            if epoch < route.epoch:
                return False  # anúncio antigo
            if route.candidates.get(neighbor) == candidate:
                route.candidates[neighbor] = candidate  # renova o tempo de vida
                return False  # cópia repetida
            route.candidates[neighbor] = candidate
            if epoch > route.epoch:
                route.epoch = epoch
                route.candidates = {nb: c for nb, c in route.candidates.items()
                                    if c.epoch >= epoch - 1}
            if neighbor == route.upstream and not self._feasible(route, candidate):
                # O montante anuncia agora um caminho que pode passar por este
                # nó (não melhor do que o que já anunciámos nesta época).
                log.info("rota %s:%s: anúncio do montante %s deixou de ser viável",
                         route.origin, route.flow, neighbor)
                actions += self._lose_upstream(node, route, still_connected=True)
                return True
            old_upstream, old_metric = route.upstream, route.metric
            new_upstream, cost = self._select(node, route)
            if new_upstream is None:
                return False
            route.upstream = new_upstream
            route.metric, route.delay = cost
            route.withdrawn_at = None
            self._refresh_state(route, node.id)
            moved = new_upstream != old_upstream
            if moved and self._has_demand(route):
                # O ramo agregado muda de montante: um só DEACTIVATE/ACTIVATE.
                if old_upstream is not None:
                    actions.append(("send", old_upstream, self._route_message(
                        node, MsgType.DEACTIVATE, route)))
                actions.append(("send", new_upstream, self._route_message(
                    node, MsgType.ACTIVATE, route)))
            propagate = (self._advertises_new_epoch(route) or moved
                         or route.metric != old_metric)
            if propagate:
                actions.append(("broadcast", self._announce_msg(node, route),
                                {neighbor, new_upstream}))
        return propagate

    # -- perda do montante: reparação rápida ou retirada ---------------------

    def _lose_upstream(self, node, route, still_connected):
        """Trata a perda do montante (sob o bloqueio); devolve os envios a fazer.

        Tenta primeiro uma alternativa viável (``_feasible``): um candidato que
        anunciou ``(atraso, saltos)`` menores do que o melhor anúncio deste nó
        na época não pode estar a usar este nó como caminho; entre as viáveis
        escolhe-se como em ``_select`` (menor latência). Sem alternativa, a rota
        fica sem caminho, os ramos a jusante são desfeitos e envia-se
        ``WITHDRAW`` a todos os vizinhos.
        """
        actions = []
        lost = route.upstream
        route.candidates.pop(lost, None)
        route.upstream = None
        had_demand = self._has_demand(route)
        if still_connected and had_demand:
            # o antigo montante já limpou os destinos; isto só evita restos
            actions.append(("send", lost, self._route_message(
                node, MsgType.DEACTIVATE, route)))
        new_upstream, cost = self._select(node, route)
        if new_upstream is not None:
            route.upstream = new_upstream
            route.metric, route.delay = cost
            self._refresh_state(route, node.id)
            if had_demand:
                actions.append(("send", new_upstream, self._route_message(
                    node, MsgType.ACTIVATE, route)))
            actions.append(("broadcast", self._announce_msg(node, route), {new_upstream}))
            log.info("rota %s:%s reparada via %s (perdido %s)",
                     route.origin, route.flow, new_upstream, lost)
            return actions
        route.metric = route.delay = math.inf
        route.candidates.clear()
        # A retenção (só épocas > route.epoch) substitui a referência de
        # viabilidade: qualquer época aceite é mais recente do que a anunciada.
        route.feasibility = None
        route.withdrawn_at = time.monotonic()  # retenção: só épocas > route.epoch
        route.destinations.clear()  # a jusante volta a pedir quando reaprender
        self._refresh_state(route, node.id)
        actions.append(("broadcast", make_msg(
            MsgType.WITHDRAW, node.id, server_id=route.origin, flow=route.flow,
            epoch=max(route.epoch, 1)), set()))
        log.info("rota %s:%s sem caminho (perdido %s): WITHDRAW",
                 route.origin, route.flow, lost)
        return actions

    def handle_withdraw(self, node, neighbor, msg):
        """O vizinho perdeu o caminho: deixa de ser candidato; se era o montante,
        repara-se a rota ou retira-se também."""
        data = msg["data"]
        with self._transaction(node) as actions:
            route = self.routes.get((data["server_id"], data["flow"]))
            if route is None:
                return False
            route.candidates.pop(neighbor, None)
            if neighbor != route.upstream:
                return False
            actions += self._lose_upstream(node, route, still_connected=True)
        return True

    def handle_neighbor_down(self, node, neighbor):
        """Invalida montantes caídos e remove o ramo e o candidato do vizinho."""
        with self._transaction(node) as actions:
            for route in self.routes.values():
                removed = neighbor in route.destinations
                route.destinations.discard(neighbor)
                if route.upstream == neighbor:
                    actions += self._lose_upstream(node, route, still_connected=False)
                    continue
                route.candidates.pop(neighbor, None)
                self._refresh_state(route, node.id)
                if removed and route.upstream is not None and not self._has_demand(route):
                    actions.append(("send", route.upstream, self._route_message(
                        node, MsgType.DEACTIVATE, route)))

    def handle_neighbor_up(self, node, neighbor):
        """Envia ao novo vizinho anúncios das rotas válidas já conhecidas."""
        with self._transaction(node) as actions:
            for route in self.routes.values():
                valid = route.origin == node.id or route.upstream is not None
                if valid and route.epoch > 0 and math.isfinite(route.metric) \
                        and neighbor != route.upstream:
                    actions.append(("send", neighbor, self._announce_msg(node, route)))


class AnnouncementPublisher:
    """Publica periodicamente anúncios dos fluxos originados neste nó.

    A publicação não depende de os vizinhos já estarem ligados: os anúncios são
    repetidos periodicamente. Os nós recetores aprendem e propagam os anúncios
    através de ``RoutingTable.attach``.

    A época de cada fluxo começa em ``first_epoch`` e avança uma unidade por
    ronda. Por omissão é a hora do arranque em milissegundos, para que um
    servidor reiniciado anuncie épocas maiores do que as anteriores (D27).
    """
    def __init__(self, node, flows, interval=5.0, routing_table=None, first_epoch=None):
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
        if first_epoch is None:
            # Épocas a partir da hora do arranque (ms): um servidor reiniciado
            # anuncia épocas maiores do que as que deixou na rede (D27).
            first_epoch = int(time.time() * 1000)
        if not isinstance(first_epoch, int) or isinstance(first_epoch, bool) \
                or first_epoch < 1:
            raise ValueError("first_epoch tem de ser um inteiro maior que zero")
        self.first_epoch = first_epoch
        self._epochs = {flow: first_epoch - 1 for flow in self.flows}
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
                           epoch=epoch, metric=0, delay=0.0)
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
