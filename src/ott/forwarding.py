"""Encaminhamento do plano de dados UDP sobre a árvore overlay ativa."""
import logging
import socket
import threading
import time

from .streaming.client import StreamStats
from .streaming.rtp import PacketError, RtpPacket, pack_data, unpack_data_hop
from .streaming.server import DATA_PORT

log = logging.getLogger("ott.forwarding")


class FlowTraffic:
    """Tráfego de um fluxo (origem, fluxo) visto por este nó."""

    def __init__(self):
        self.stats = StreamStats()   # pacotes aceites (RPF): recebidos, perdidos, fora de ordem
        self.from_neighbor = {}      # vizinho de entrada -> pacotes aceites
        self.rpf_from = {}           # vizinho de entrada -> pacotes descartados por RPF
        self.sent_to = {}            # vizinho a jusante -> cópias enviadas

    def summary(self, origin, flow):
        def fmt(counts):
            return ", ".join(f"{nb}={n}" for nb, n in sorted(counts.items())) or "-"
        st = self.stats
        text = (f"{origin}:{flow} recebidos={st.received} perdidos={st.lost} "
                f"({st.loss_ratio():.1%}) | de: {fmt(self.from_neighbor)} "
                f"| para: {fmt(self.sent_to)}")
        if self.rpf_from:
            text += f" | descartados RPF: {fmt(self.rpf_from)}"
        return text


class TrafficStats:
    """Contadores do plano de dados por fluxo e por vizinho (para ``--log-traffic``).

    As perdas contam-se pelos buracos na sequência RTP dos pacotes aceites pelo
    RPF, por isso também funcionam num nó que só reencaminha (sem subscritor
    local); uma troca de montante que perca pacotes aparece como perda.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.flows = {}

    def _flow(self, origin, flow):
        entry = self.flows.get((origin, flow))
        if entry is None:
            entry = self.flows[(origin, flow)] = FlowTraffic()
        return entry

    def accepted(self, origin, flow, incoming, rtp, arrival):
        with self._lock:
            entry = self._flow(origin, flow)
            entry.from_neighbor[incoming] = entry.from_neighbor.get(incoming, 0) + 1
            entry.stats.update(rtp, arrival)

    def rpf_dropped(self, origin, flow, incoming):
        with self._lock:
            entry = self._flow(origin, flow)
            entry.rpf_from[incoming] = entry.rpf_from.get(incoming, 0) + 1

    def sent(self, origin, flow, neighbor):
        with self._lock:
            entry = self._flow(origin, flow)
            entry.sent_to[neighbor] = entry.sent_to.get(neighbor, 0) + 1

    def counts(self):
        """Cópia dos totais por fluxo: ``{(origem, fluxo): {received, lost, sent}}``."""
        with self._lock:
            return {key: {"received": e.stats.received, "lost": e.stats.lost,
                          "sent": sum(e.sent_to.values())}
                    for key, e in self.flows.items()}

    def summary_lines(self, neighbor=None):
        """Uma linha por fluxo; com ``neighbor``, só os fluxos que passaram por ele."""
        with self._lock:
            lines = []
            for (origin, flow), entry in sorted(self.flows.items()):
                if neighbor is not None and not (
                        neighbor in entry.from_neighbor or neighbor in entry.sent_to
                        or neighbor in entry.rpf_from):
                    continue
                lines.append(entry.summary(origin, flow))
            return lines


class OverlayForwarder:
    """Recebe pacotes UDP e replica-os pelos ramos ativos da rota.

    Cada ligação overlay continua a usar TCP para controlo. Os datagramas RTP
    viajam por UDP, um por vizinho a jusante, sem duplicar cópias para vários
    subscritores atrás do mesmo vizinho. Um datagrama só é aceite (entregue
    localmente e reencaminhado) se vier do vizinho de montante da rota (RPF).
    O vizinho de entrada é o id no campo "salto" do pacote, não o IP de origem
    (que num nó com várias interfaces pode diferir do da ligação TCP); cada
    cópia reencaminhada leva o id deste nó.
    """

    def __init__(self, node, routing_table, port=DATA_PORT, bind="0.0.0.0"):
        self.node = node
        self.routing_table = routing_table
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((bind, port))
        self.sock.settimeout(0.5)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._receive_loop,
                                        name=f"ott-data-{node.id}", daemon=True)
        self._lock = threading.RLock()
        self._local_receivers = {}
        self.received = 0
        self.forwarded = 0
        self.dropped = 0       # todos os descartes (inválidos, não vizinhos, RPF)
        self.rpf_dropped = 0   # descartados por não virem do montante (RPF)
        self.traffic = TrafficStats()

    def set_local_receiver(self, origin, flow, receiver):
        """Associa um consumidor local, por exemplo o descodificador RTP."""
        with self._lock:
            self._local_receivers[(origin, flow)] = receiver

    def start(self):
        if not self._thread.is_alive():
            self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        self.sock.close()

    def send(self, packet):
        """Envia um pacote originado neste nó pelos destinos ativos."""
        try:
            origin, flow, _, rtp = unpack_data_hop(packet)
            RtpPacket.decode(rtp)
        except PacketError:
            self.dropped += 1
            return 0
        return self._forward(origin, flow, rtp, incoming=None)

    def _receive_loop(self):
        while not self._stop.is_set():
            try:
                packet, address = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            self.received += 1
            try:
                origin, flow, incoming, rtp = unpack_data_hop(packet)
                header = RtpPacket.decode(rtp)
            except PacketError as exc:
                self.dropped += 1
                log.debug("datagrama inválido de %s: %s", address[0], exc)
                continue

            if incoming not in self.node.neighbor_ids():
                self.dropped += 1
                log.debug("datagrama de %s (%s), que não é vizinho, descartado",
                          incoming, address[0])
                continue

            # RPF estrito: só se aceitam dados vindos do montante atual da rota.
            # Elimina cópias duplicadas durante uma troca de montante e quebra
            # ciclos de dados transitórios (aceita-se uma falha curta na troca).
            upstream = self.routing_table.upstream_of(origin, flow)
            if incoming != upstream:
                self.dropped += 1
                self.rpf_dropped += 1
                self.traffic.rpf_dropped(origin, flow, incoming)
                log.debug("RPF: %s:%s vindo de %s, montante %s: descartado",
                          origin, flow, incoming, upstream)
                continue

            self.traffic.accepted(origin, flow, incoming, header, time.monotonic())
            if self.routing_table.has_local_subscription(origin, flow):
                with self._lock:
                    receiver = self._local_receivers.get((origin, flow))
                if receiver is not None:
                    receiver.handle(packet, time.monotonic())

            self._forward(origin, flow, rtp, incoming=incoming)

    def _forward(self, origin, flow, rtp, incoming):
        sent = 0
        destinations = self.routing_table.active_destinations(
            origin, flow, exclude=(() if incoming is None else (incoming,)))
        if destinations:
            try:
                packet = pack_data(origin, flow, rtp, hop=self.node.id)
            except PacketError as exc:   # ex.: o id deste nó não cabe no datagrama
                self.dropped += 1
                log.debug("pacote %s:%s não reencaminhado: %s", origin, flow, exc)
                return 0
        for neighbor in destinations:
            host = self.node.neighbor_address(neighbor)
            if host is None:
                continue
            try:
                self.sock.sendto(packet, (host, self.port))
                sent += 1
                self.traffic.sent(origin, flow, neighbor)
            except OSError as exc:
                log.debug("falha UDP para %s (%s)", neighbor, exc)
        if incoming is None and not destinations:
            # Sem subscritores ativos, a fonte não injeta tráfego no overlay.
            return 0
        self.forwarded += sent
        return sent
