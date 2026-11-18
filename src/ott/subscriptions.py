"""Subscrições locais de um nó: com servidor fixo ou só pelo fluxo (D38).

Uma subscrição ``SERVIDOR:FLUXO`` ativa a rota desse servidor assim que ela é
conhecida, como sempre. Uma subscrição só ``FLUXO`` (o mesmo filme publicado por
mais do que um servidor, por exemplo) escolhe o servidor:

* o primeiro é o de menor latência entre os que têm caminho para o fluxo
  (``RoutingTable.sources_of``);
* se o servidor escolhido deixa de ter caminho (``WITHDRAW``, rota esquecida) e
  outro tem, passa para esse (*failover*): ativa a rota nova, muda o recetor
  local e desativa a antiga;
* com os dois servidores alcançáveis, só troca se o outro for melhor acima da
  histerese da seleção de rotas e se a última troca foi há mais de ``min_hold``
  segundos (evita oscilações);
* sem nenhum servidor alcançável, mantém o pedido ao último (a rota fica
  pendente na tabela e volta sozinha quando o caminho for reaprendido).

As rotas continuam por ``(servidor, fluxo)``: nada muda no protocolo; este
módulo só decide quais ativar, através da interface pública da ``RoutingTable``.
Na troca, o vídeo continua no mesmo recetor; como o novo servidor tem outro
SSRC, o recetor conta-a como um recomeço (``reinicios``) e não como perdas.
"""
import logging
import threading
import time

log = logging.getLogger("ott.subscriptions")

ANY = "*"   # servidor de uma subscrição sem servidor fixo ainda por escolher (só no estado)


def parse_subscription(value):
    """``"S1:filme_a"`` -> ``("S1", "filme_a")``; ``"filme_a"`` -> ``(None, "filme_a")``."""
    if not isinstance(value, str):
        raise ValueError("subscrição inválida")
    server, sep, flow = value.partition(":")
    if not sep:
        server, flow = None, value
    if not flow or (sep and not server) or ":" in flow or flow == ANY or server == ANY:
        raise ValueError(f"subscrição inválida: {value!r}; usa SERVIDOR:FLUXO ou FLUXO")
    return server, flow


class Subscription:
    def __init__(self, server, flow, receiver):
        self.fixed = server is not None
        self.flow = flow
        self.receiver = receiver
        self.current = server          # servidor em uso (None numa subscrição sem servidor ainda por escolher)
        self.active = False            # o pedido está registado na tabela de rotas
        self.switches = 0
        self.last_switch = None
        if not self.fixed:
            receiver.subscription = flow

    @property
    def name(self):
        return f"{self.current}:{self.flow}" if self.fixed else self.flow


class SubscriptionManager:
    """Mantém as subscrições locais de um nó; ``tick`` corre numa thread própria."""

    def __init__(self, node, routing_table, forwarder, subscriber_id=None, poll=0.25, min_hold=5.0,
                 clock=time.monotonic):
        self.node = node
        self.table = routing_table
        self.forwarder = forwarder
        self.subscriber_id = subscriber_id
        self.poll = poll
        self.min_hold = min_hold
        self.clock = clock
        self.subs = []
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None

    def add(self, server, flow, receiver):
        """Acrescenta uma subscrição (``server`` None: sem servidor fixo)."""
        with self._lock:
            for s in self.subs:
                if s.flow == flow and (not s.fixed or server is None or s.current == server):
                    raise ValueError(f"subscrição repetida ou sobreposta ao fluxo {flow}")
            sub = Subscription(server, flow, receiver)
            if sub.fixed:
                self.forwarder.set_local_receiver(server, flow, receiver)
            self.subs.append(sub)
            return sub

    # -- ciclo de vida ------------------------------------------------------

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name=f"ott-subs-{self.node.id}")
            self._thread.start()
        return self

    def _run(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("erro a avaliar as subscrições")
            self._stop.wait(self.poll)

    def stop(self):
        """Pára a thread e desativa todas as subscrições ativas."""
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        with self._lock:
            for sub in self.subs:
                if sub.active and sub.current is not None:
                    self.table.deactivate(self.node, sub.current, sub.flow, subscriber_id=self.subscriber_id)
                    sub.active = False

    # -- decisão ------------------------------------------------------------

    def tick(self):
        """Avalia as subscrições uma vez; devolve as trocas de servidor feitas."""
        changes = []
        with self._lock:
            for sub in self.subs:
                if sub.fixed:
                    self._tick_fixed(sub)
                else:
                    change = self._tick_any(sub)
                    if change:
                        changes.append(change)
        return changes

    def _activate(self, sub, server):
        return self.table.activate(self.node, server, sub.flow, subscriber_id=self.subscriber_id)

    def _tick_fixed(self, sub):
        if not sub.active and self.table.get(sub.current, sub.flow) is not None \
                and self._activate(sub, sub.current):
            sub.active = True
            log.info("subscrito a %s:%s", sub.current, sub.flow)

    def _tick_any(self, sub):
        sources = self.table.sources_of(sub.flow, node_id=self.node.id)
        if not sources:
            return None                    # ninguém alcançável: mantém o pedido ao último, se houver
        best, best_delay, _hops = sources[0]
        reachable = {origin: delay for origin, delay, _h in sources}
        if sub.current is None:
            return self._switch(sub, best, "primeira escolha")
        if sub.current not in reachable:
            return self._switch(sub, best, f"{sub.current} sem caminho")
        if best != sub.current and self.table.delay_better(best_delay, reachable[sub.current]) \
                and (sub.last_switch is None or self.clock() - sub.last_switch >= self.min_hold):
            return self._switch(sub, best, "mais perto")
        return None

    def _switch(self, sub, new, reason):
        """Ativa o servidor novo, passa o recetor para ele e desativa o antigo."""
        old = sub.current
        if not self._activate(sub, new):
            return None
        sub.receiver.origin = new
        self.forwarder.set_local_receiver(new, sub.flow, sub.receiver)
        if old is not None:
            self.forwarder.set_local_receiver(old, sub.flow, None)
            if sub.active:
                self.table.deactivate(self.node, old, sub.flow, subscriber_id=self.subscriber_id)
            sub.switches += 1
        sub.current, sub.active, sub.last_switch = new, True, self.clock()
        if old is None:
            log.info("subscrito a %s: escolhido o servidor %s", sub.flow, new)
        else:
            log.info("subscrição %s: passa para o servidor %s (antes %s; %s)", sub.flow, new, old, reason)
        return (sub.flow, old, new, reason)

    # -- consultas (estado, consola) ----------------------------------------

    def receivers(self):
        """``{(servidor, fluxo): recetor}`` com o servidor em uso de cada subscrição já escolhida."""
        with self._lock:
            return {(s.current, s.flow): s.receiver for s in self.subs if s.current is not None}

    def keys(self):
        """``(servidor, fluxo)`` em uso, para a consola ``--live``."""
        return sorted(self.receivers())

    def state(self):
        """``(pendentes, ativas)``: conjuntos de ``(servidor, fluxo)``; ``*`` = servidor por escolher."""
        with self._lock:
            pending = {(s.current or ANY, s.flow) for s in self.subs if not s.active}
            active = {(s.current, s.flow) for s in self.subs if s.active}
        return pending, active

    def is_active(self, server, flow):
        with self._lock:
            return any(s.active and s.current == server and s.flow == flow for s in self.subs)
