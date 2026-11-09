"""Encaminhamento do plano de dados UDP sobre a árvore overlay ativa."""
import logging
import socket
import threading
import time

from .streaming.rtp import PacketError, RtpPacket, unpack_data
from .streaming.server import DATA_PORT

log = logging.getLogger("ott.forwarding")


class OverlayForwarder:
    """Recebe pacotes UDP e replica-os pelos ramos ativos da rota.

    Cada ligação overlay continua a usar TCP para controlo. Os datagramas RTP
    viajam por UDP, um por vizinho a jusante, sem duplicar cópias para vários
    subscritores atrás do mesmo vizinho. Um datagrama só é aceite (entregue
    localmente e reencaminhado) se vier do vizinho de montante da rota (RPF).
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
            origin, flow, rtp = unpack_data(packet)
            RtpPacket.decode(rtp)
        except PacketError:
            self.dropped += 1
            return 0
        return self._forward(packet, origin, flow, incoming=None)

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
                origin, flow, rtp = unpack_data(packet)
                RtpPacket.decode(rtp)
            except PacketError as exc:
                self.dropped += 1
                log.debug("datagrama inválido de %s: %s", address[0], exc)
                continue

            incoming = self.node.neighbor_for_address(address[0])
            if incoming is None:
                self.dropped += 1
                log.debug("datagrama de origem não vizinha %s descartado", address[0])
                continue

            # RPF estrito: só se aceitam dados vindos do montante atual da rota.
            # Elimina cópias duplicadas durante uma troca de montante e quebra
            # ciclos de dados transitórios (aceita-se uma falha curta na troca).
            upstream = self.routing_table.upstream_of(origin, flow)
            if incoming != upstream:
                self.dropped += 1
                self.rpf_dropped += 1
                log.debug("RPF: %s:%s vindo de %s, montante %s: descartado",
                          origin, flow, incoming, upstream)
                continue

            if self.routing_table.has_local_subscription(origin, flow):
                with self._lock:
                    receiver = self._local_receivers.get((origin, flow))
                if receiver is not None:
                    receiver.handle(packet, time.monotonic())

            self._forward(packet, origin, flow, incoming=incoming)

    def _forward(self, packet, origin, flow, incoming):
        sent = 0
        destinations = self.routing_table.active_destinations(
            origin, flow, exclude=(() if incoming is None else (incoming,)))
        for neighbor in destinations:
            host = self.node.neighbor_address(neighbor)
            if host is None:
                continue
            try:
                self.sock.sendto(packet, (host, self.port))
                sent += 1
            except OSError as exc:
                log.debug("falha UDP para %s (%s)", neighbor, exc)
        if incoming is None and not destinations:
            # Sem subscritores ativos, a fonte não injeta tráfego no overlay.
            return 0
        self.forwarded += sent
        return sent
