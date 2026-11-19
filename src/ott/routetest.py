"""Teste dos percursos no overlay (Etapa 3 do enunciado).

O servidor gera periodicamente uma mensagem ``ROUTE_TEST`` por fluxo que publica.
Cada nó que a recebe do seu vizinho de montante:

* junta o seu id ao caminho acumulado e regista uma linha de log (informação de
  debug: fluxo, número de ordem, caminho percorrido e saltos);
* se tem destinos ativos na rota, reenvia-a, só por eles, com o caminho atualizado
  (é a mesma árvore por onde segue o vídeo; um nó sem procura não a recebe);
* guarda o resultado em ``results``, onde os testes e as ferramentas o leem.

A mensagem segue a tabela de rotas (``RoutingTable``) mas nunca a altera: usa só
``upstream_of`` (o vizinho de montante), ``active_destinations`` e
``has_local_subscription``. Como o RPF do plano de dados, só aceita a mensagem do
vizinho de montante da rota; um caminho que já contém este nó indica um ciclo e é
descartado. Os relógios não são comparados: o teste verifica o caminho, não o
atraso (para isso há o RTT de cada ligação).
"""
import logging
import threading
import time
from collections import deque
from typing import NamedTuple, Tuple

from .protocol import MAX_PATH, MsgType, make_msg

log = logging.getLogger("ott.routetest")


class RouteTestResult(NamedTuple):
    """Uma passagem de um ``ROUTE_TEST`` por este nó."""
    server_id: str
    flow: str
    seq: int
    path: Tuple[str, ...]        # inclui este nó no fim
    delivered: bool              # há um cliente local subscrito ao fluxo
    forwarded_to: Tuple[str, ...]
    at: float                    # relógio de parede local (só informativo)

    @property
    def hops(self):
        return len(self.path) - 1


class RouteTester:
    """Gera (no servidor) e reencaminha (em todos os nós) os ``ROUTE_TEST``.

    ``attach(node)`` regista o handler. ``send_test(flow)`` gera um teste;
    ``start(flows, interval)`` repete-o periodicamente em background e ``stop()``
    termina essa thread.
    """
    def __init__(self, node, routing_table, history=200):
        self.node = node
        self.table = routing_table
        self.results = deque(maxlen=history)
        self.dropped = 0              # recebidos fora do caminho da rota ou em ciclo
        self._seq = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def attach(self):
        self.node.register_handler(
            MsgType.ROUTE_TEST,
            lambda node, neighbor, msg: self.handle(node, neighbor, msg))
        return self

    # -- servidor ------------------------------------------------------

    def send_test(self, flow):
        """Gera um ``ROUTE_TEST`` do fluxo, publicado por este nó, pelas rotas ativas.

        Devolve o número de ordem, ou ``None`` se este nó não é a origem do fluxo.
        Sem destinos ativos regista o teste na mesma (não há clientes: nada a
        percorrer) e não envia nada.
        """
        node_id = self.node.id
        route = self.table.get(node_id, flow)
        if route is None or route.upstream is not None:
            return None
        with self._lock:
            seq = self._seq[flow] = self._seq.get(flow, 0) + 1
        dests = self.table.active_destinations(node_id, flow)
        self._record(node_id, flow, seq, (node_id,), False, dests)
        self._forward(node_id, flow, seq, [node_id], dests)
        return seq

    def start(self, flows, interval):
        """Gera um teste por fluxo a cada ``interval`` segundos (o primeiro, passado esse tempo)."""
        if not interval or interval <= 0:
            raise ValueError("interval tem de ser maior que zero")
        if self._thread is not None and self._thread.is_alive():
            return self
        flows = tuple(flows)
        self._stop.clear()

        def run():
            while not self._stop.wait(interval):
                for flow in flows:
                    try:
                        self.send_test(flow)
                    except Exception:   # um erro não pára os testes seguintes
                        log.exception("falha ao gerar o ROUTE_TEST de %s", flow)
        self._thread = threading.Thread(target=run, name="ott-routetest", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)

    # -- qualquer nó ---------------------------------------------------

    def handle(self, node, neighbor, msg):
        data = msg["data"]
        origin, flow, seq = data["server_id"], data["flow"], data["seq"]
        path = list(data["path"])
        if self.table.upstream_of(origin, flow) != neighbor:
            self.dropped += 1
            log.debug("ROUTE_TEST %s:%s #%d de %s descartado: não é o montante da rota",
                      origin, flow, seq, neighbor)
            return
        if node.id in path or len(path) >= MAX_PATH:
            self.dropped += 1
            log.warning("ROUTE_TEST %s:%s #%d descartado: ciclo ou caminho longo demais (%s)",
                        origin, flow, seq, ">".join(path))
            return
        path.append(node.id)
        delivered = self.table.has_local_subscription(origin, flow)
        dests = self.table.active_destinations(origin, flow, exclude=(neighbor,))
        self._record(origin, flow, seq, tuple(path), delivered, dests)
        self._forward(origin, flow, seq, path, dests)

    # -- interno -------------------------------------------------------

    def _record(self, origin, flow, seq, path, delivered, dests):
        result = RouteTestResult(origin, flow, seq, tuple(path), delivered,
                                 tuple(dests), time.time())
        self.results.append(result)
        if not dests and not delivered and len(path) == 1:
            end = "sem destinos ativos"
        elif delivered:
            end = "entregue ao cliente local" + (
                f", segue para {','.join(dests)}" if dests else "")
        elif dests:
            end = f"segue para {','.join(dests)}"
        else:
            end = "sem destinos (fim do ramo)"
        log.info("ROUTE_TEST %s:%s #%d caminho %s (%d saltos): %s",
                 origin, flow, seq, ">".join(path), len(path) - 1, end)

    def _forward(self, origin, flow, seq, path, dests):
        for dest in dests:
            self.node.send(dest, make_msg(MsgType.ROUTE_TEST, self.node.id,
                                          server_id=origin, flow=flow, seq=seq,
                                          path=list(path)))

    def last(self, origin, flow):
        """Último resultado para ``(origin, flow)``, ou ``None``."""
        for result in reversed(self.results):
            if result.server_id == origin and result.flow == flow:
                return result
        return None
