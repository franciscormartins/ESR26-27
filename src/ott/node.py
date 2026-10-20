"""Nó da rede overlay: ligações TCP full-duplex com os vizinhos.

Cada nó:
  * escuta numa porta e aceita ligações de vizinhos;
  * liga-se aos vizinhos que lhe foram indicados (manualmente ou pelo
    bootstrapper) e volta a tentar enquanto estiverem em baixo;
  * troca HELLO ao ligar e PING/PONG periódicos para detetar vizinhos mortos;
  * entrega as restantes mensagens aos handlers registados (rotas, streaming).

Entre dois nós só existe uma ligação; se ambos se ligarem em simultâneo
mantém-se a iniciada pelo nó com menor id.
"""
import logging
import socket
import threading
import time

from . import protocol as proto
from .protocol import MsgType, make_msg

log = logging.getLogger("ott.node")

CONNECT_TIMEOUT = 3.0
HANDSHAKE_TIMEOUT = 3.0


class Neighbor:
    def __init__(self, node_id, sock, initiator):
        self.id = node_id
        self.sock = sock
        self.initiator = initiator  # id do nó que iniciou a ligação
        self.last_seen = time.time()
        self._send_lock = threading.Lock()

    def send(self, msg):
        with self._send_lock:
            proto.send_msg(self.sock, msg)

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


class OverlayNode:
    def __init__(self, node_id, host="0.0.0.0", port=proto.DEFAULT_PORT,
                 bootstrap_service=None, keepalive=2.0, dead_after=6.0,
                 retry_interval=2.0):
        self.id = node_id
        self.host = host
        self.port = port
        self.bootstrap_service = bootstrap_service
        self.keepalive = keepalive
        self.dead_after = dead_after
        self.retry_interval = retry_interval

        self.neighbors = {}   # id -> Neighbor (ligações ativas)
        self._targets = {}    # (host, port) -> id do vizinho (None se ainda desconhecido)
        self._handlers = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._server = None

    # -- ciclo de vida -------------------------------------------------

    def start(self):
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((self.host, self.port))
        self._server.listen()
        self._spawn(self._accept_loop)
        self._spawn(self._maintain_loop)
        log.info("nó %s a escutar em %s:%d", self.id, self.host, self.port)

    def stop(self):
        self._stop.set()
        with self._lock:
            neighbors = list(self.neighbors.values())
        for nb in neighbors:
            try:
                nb.send(make_msg(MsgType.BYE, self.id))
            except OSError:
                pass
            nb.close()
        if self._server:
            try:
                self._server.shutdown(socket.SHUT_RDWR)  # acorda o accept()
            except OSError:
                pass
            self._server.close()

    def _spawn(self, fn, *args):
        threading.Thread(target=fn, args=args, daemon=True).start()

    # -- descoberta de vizinhos ---------------------------------------

    def add_targets(self, addrs):
        """Vizinhos a manter ligados: {(host, port): id ou None}."""
        with self._lock:
            for addr, nid in dict(addrs).items():
                self._targets.setdefault(addr, nid)

    def bootstrap(self, host, port, attempts=10):
        """Estratégia 2: pede a lista de vizinhos ao bootstrapper."""
        for _ in range(attempts):
            try:
                with socket.create_connection((host, port), CONNECT_TIMEOUT) as s:
                    s.settimeout(HANDSHAKE_TIMEOUT)
                    proto.send_msg(s, make_msg(MsgType.REGISTER, self.id))
                    reply = proto.recv_msg(s)
                if reply and reply["type"] == MsgType.NEIGHBORS:
                    nbs = reply["data"]["neighbors"]
                    self.add_targets({(n["host"], n["port"]): n["id"] for n in nbs})
                    log.info("bootstrap: vizinhos %s", [n["id"] for n in nbs])
                    return True
                log.error("bootstrap recusado: %s", reply and reply["data"])
                return False
            except (OSError, ValueError) as e:
                log.warning("bootstrapper %s:%d indisponível (%s)", host, port, e)
                if self._stop.wait(self.retry_interval):
                    break
        return False

    # -- mensagens -----------------------------------------------------

    def register_handler(self, msg_type, fn):
        """fn(node, neighbor, msg) é chamado para mensagens do tipo dado."""
        self._handlers[msg_type] = fn

    def send(self, node_id, msg):
        with self._lock:
            nb = self.neighbors.get(node_id)
        if nb is None:
            return False
        try:
            nb.send(msg)
            return True
        except OSError:
            self._drop(nb)
            return False

    def broadcast(self, msg, exclude=()):
        """Envia a todos os vizinhos exceto os ids em `exclude`."""
        with self._lock:
            ids = [i for i in self.neighbors if i not in exclude]
        for i in ids:
            self.send(i, msg)

    def neighbor_ids(self):
        with self._lock:
            return sorted(self.neighbors)

    # -- ligações ------------------------------------------------------

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                sock, addr = self._server.accept()
            except OSError:
                return
            self._spawn(self._handle_inbound, sock, addr)

    def _handle_inbound(self, sock, addr):
        try:
            sock.settimeout(HANDSHAKE_TIMEOUT)
            msg = proto.recv_msg(sock)
            if msg is None:
                sock.close()
            elif msg["type"] == MsgType.HELLO:
                proto.send_msg(sock, make_msg(MsgType.HELLO, self.id))
                self._adopt(msg["src"], sock, initiator=msg["src"])
            elif msg["type"] == MsgType.REGISTER:
                self._serve_register(sock, msg)
                sock.close()
            else:
                sock.close()
        except (OSError, ValueError, KeyError) as e:
            log.debug("ligação de %s rejeitada: %s", addr, e)
            sock.close()

    def _serve_register(self, sock, msg):
        nbs = None
        if self.bootstrap_service:
            nbs = self.bootstrap_service.register(msg["src"])
        if nbs is None:
            reply = make_msg(MsgType.ERROR, self.id, reason="nó desconhecido")
        else:
            reply = make_msg(MsgType.NEIGHBORS, self.id, neighbors=nbs)
        proto.send_msg(sock, reply)

    def _connect(self, target):
        try:
            sock = socket.create_connection(target, CONNECT_TIMEOUT)
        except OSError:
            return
        try:
            sock.settimeout(HANDSHAKE_TIMEOUT)
            proto.send_msg(sock, make_msg(MsgType.HELLO, self.id))
            reply = proto.recv_msg(sock)
            if not reply or reply["type"] != MsgType.HELLO:
                raise ValueError("handshake inválido")
            with self._lock:
                self._targets[target] = reply["src"]
            self._adopt(reply["src"], sock, initiator=self.id)
        except (OSError, ValueError, KeyError) as e:
            log.debug("falha ao ligar a %s: %s", target, e)
            sock.close()

    def _adopt(self, peer_id, sock, initiator):
        """Regista a ligação como vizinho (resolvendo ligações duplicadas)."""
        if peer_id == self.id:
            sock.close()
            return
        nb = Neighbor(peer_id, sock, initiator)
        with self._lock:
            old = self.neighbors.get(peer_id)
            if old is not None:
                same_side = old.initiator == nb.initiator
                if not same_side and old.initiator < nb.initiator:
                    sock.close()
                    return
                old.close()  # ligação antiga obsoleta ou menos prioritária
            self.neighbors[peer_id] = nb
        sock.settimeout(None)
        log.info("HELLO: vizinho %s ligado (%s)", peer_id,
                 "iniciada por nós" if initiator == self.id else "recebida")
        self._read_loop(nb)

    def _read_loop(self, nb):
        try:
            while not self._stop.is_set():
                msg = proto.recv_msg(nb.sock)
                if msg is None or msg["type"] == MsgType.BYE:
                    break
                nb.last_seen = time.time()
                self._dispatch(nb, msg)
        except (OSError, ValueError, KeyError):
            pass
        self._drop(nb)

    def _dispatch(self, nb, msg):
        mtype = msg["type"]
        if mtype == MsgType.PING:
            self.send(nb.id, make_msg(MsgType.PONG, self.id))
        elif mtype in (MsgType.PONG, MsgType.HELLO):
            pass
        elif mtype in self._handlers:
            self._handlers[mtype](self, nb, msg)
        else:
            log.debug("mensagem %s ignorada (de %s)", mtype, nb.id)

    def _drop(self, nb):
        with self._lock:
            if self.neighbors.get(nb.id) is nb:
                del self.neighbors[nb.id]
                log.warning("vizinho %s desligado", nb.id)
        nb.close()

    # -- manutenção ----------------------------------------------------

    def _maintain_loop(self):
        last_ping = 0.0
        while not self._stop.is_set():
            now = time.time()
            self._connect_missing()
            if now - last_ping >= self.keepalive:
                last_ping = now
                self.broadcast(make_msg(MsgType.PING, self.id))
            with self._lock:
                dead = [n for n in self.neighbors.values()
                        if now - n.last_seen > self.dead_after]
            for nb in dead:
                log.warning("vizinho %s sem resposta", nb.id)
                self._drop(nb)
            self._stop.wait(min(self.retry_interval, self.keepalive))

    def _connect_missing(self):
        with self._lock:
            missing = [t for t, nid in self._targets.items()
                       if nid is None or nid not in self.neighbors]
        for target in missing:
            if target == (self.host, self.port):
                continue
            self._spawn(self._connect, target)
