"""Nó da rede overlay: ligações TCP full-duplex com os vizinhos.

Cada nó:
  * escuta numa porta e aceita ligações de vizinhos;
  * liga-se aos vizinhos que lhe foram indicados (manualmente ou pelo
    bootstrapper) e volta a tentar enquanto estiverem em baixo;
  * troca HELLO ao ligar e PING/PONG periódicos para detetar vizinhos mortos
    e medir o RTT de cada vizinho;
  * avisa os outros módulos quando um vizinho liga ou cai (eventos up/down);
  * se ficar com menos de `min_neighbors` vizinhos, pede substitutos ao
    bootstrapper (mensagem REPLACE);
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
RTT_ALPHA = 0.3        # peso da última amostra na média móvel do RTT
RTT_MAX_SAMPLE = 60.0  # amostras acima disto (s) são descartadas
DUPLICATE_GRACE = 3.0  # tempo para a ligação vencedora chegar após o fecho de uma duplicada


class Neighbor:
    def __init__(self, node_id, sock, initiator):
        self.id = node_id
        self.sock = sock
        self.initiator = initiator  # id do nó que iniciou a ligação
        self.since = time.time()
        self.last_seen = self.since
        self.rtt = None             # média móvel do RTT (s); None até haver amostra
        self._send_lock = threading.Lock()

    def update_rtt(self, sample):
        self.rtt = sample if self.rtt is None else (
            RTT_ALPHA * sample + (1 - RTT_ALPHA) * self.rtt)

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
                 retry_interval=2.0, min_neighbors=0, replace_interval=5.0,
                 substitute_wait=10.0):
        self.id = node_id
        self.host = host
        self.port = port
        self.bootstrap_service = bootstrap_service
        self.keepalive = keepalive
        self.dead_after = dead_after
        self.retry_interval = retry_interval
        self.min_neighbors = min_neighbors        # X; 0 desativa os substitutos
        self.replace_interval = replace_interval  # espera antes/entre pedidos REPLACE
        self.substitute_wait = substitute_wait    # tempo para ligar a um substituto

        self.bootstrap_addr = None  # (host, port) do bootstrapper remoto
        self.neighbors = {}   # id -> Neighbor (ligações ativas)
        self._targets = {}    # (host, port) -> id do vizinho (None se ainda desconhecido)
        self._dynamic = {}    # (host, port) -> prazo: alvos propostos como substitutos
        self._connecting = set()  # alvos com uma tentativa de ligação em curso
        self._lost = set()    # vizinhos perdidos e ainda não recuperados
        self._pending_dup = {}  # id -> prazo: ligação duplicada fechada, à espera da vencedora
        self._deficit_since = None
        self._last_replace = 0.0
        self._replacing = False
        self._handlers = {}
        self._up_cbs = []
        self._down_cbs = []
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

    # -- eventos e métricas (interface para o módulo de rotas) ---------

    def on_neighbor_up(self, fn):
        """fn(node, neighbor_id) quando um vizinho passa a estar ligado."""
        self._up_cbs.append(fn)

    def on_neighbor_down(self, fn):
        """fn(node, neighbor_id) quando um vizinho deixa de estar ligado.

        Os callbacks correm nas threads do nó: devem ser curtos e não bloqueantes.
        """
        self._down_cbs.append(fn)

    def _fire(self, callbacks, neighbor_id):
        for fn in list(callbacks):
            try:
                fn(self, neighbor_id)
            except Exception:
                log.exception("erro num callback de vizinho (%s)", neighbor_id)

    def rtt(self, node_id):
        """RTT (segundos, média móvel) do vizinho, ou None se desconhecido."""
        with self._lock:
            nb = self.neighbors.get(node_id)
            return nb.rtt if nb else None

    def rtts(self):
        """{vizinho: RTT em segundos} para os vizinhos com RTT já medido."""
        with self._lock:
            return {i: nb.rtt for i, nb in self.neighbors.items() if nb.rtt is not None}

    def describe_neighbors(self):
        with self._lock:
            items = sorted(self.neighbors.items())
        if not items:
            return "sem vizinhos"
        return ", ".join(
            f"{i}({nb.rtt * 1000:.2f} ms)" if nb.rtt is not None else f"{i}(rtt ?)"
            for i, nb in items)

    # -- descoberta de vizinhos ---------------------------------------

    def add_targets(self, addrs):
        """Vizinhos a manter ligados: {(host, port): id ou None}."""
        with self._lock:
            for addr, nid in dict(addrs).items():
                self._targets.setdefault(addr, nid)

    def _ask_bootstrapper(self, host, port, msg):
        """Uma troca pedido/resposta com o bootstrapper; devolve a resposta."""
        with socket.create_connection((host, port), CONNECT_TIMEOUT) as s:
            s.settimeout(HANDSHAKE_TIMEOUT)
            proto.send_msg(s, msg)
            return proto.recv_msg(s)

    def bootstrap(self, host, port, attempts=10):
        """Estratégia 2: pede a lista de vizinhos ao bootstrapper."""
        self.bootstrap_addr = (host, port)
        for _ in range(attempts):
            try:
                reply = self._ask_bootstrapper(
                    host, port, make_msg(MsgType.REGISTER, self.id))
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

    # -- substitutos de vizinhos ----------------------------------------

    def _maybe_replace(self, now):
        """Pede substitutos se o défice de vizinhos persistir `replace_interval`."""
        if self.min_neighbors <= 0:
            return
        with self._lock:
            deficit = len(self.neighbors) < self.min_neighbors
            if not deficit:
                self._deficit_since = None
                return
            if self._deficit_since is None:
                self._deficit_since = now
            ready = (now - self._deficit_since >= self.replace_interval
                     and now - self._last_replace >= self.replace_interval
                     and not self._replacing
                     and (self.bootstrap_service or self.bootstrap_addr))
            if not ready:
                return
            self._replacing = True
            self._last_replace = now
        self._spawn(self._request_replacement)

    def _request_replacement(self):
        try:
            with self._lock:
                have = sorted(self.neighbors)
                lost = sorted(self._lost)
            need = self.min_neighbors - len(have)
            if need <= 0:
                return
            if self.bootstrap_service:
                nbs = self.bootstrap_service.replacements(
                    self.id, have=have, lost=lost, need=need)
            else:
                reply = self._ask_bootstrapper(
                    *self.bootstrap_addr,
                    make_msg(MsgType.REPLACE, self.id, have=have, lost=lost, need=need))
                nbs = reply["data"]["neighbors"] if reply and \
                    reply["type"] == MsgType.NEIGHBORS else None
            if nbs is None:
                log.warning("pedido de substitutos recusado")
                return
            if not nbs:
                log.info("sem substitutos disponíveis; a esquecer vizinhos perdidos")
                with self._lock:
                    self._lost.clear()  # na próxima volta voltam a ser candidatos
                return
            deadline = time.time() + self.substitute_wait
            with self._lock:
                for n in nbs:
                    addr = (n["host"], n["port"])
                    if addr not in self._targets:
                        self._targets[addr] = n["id"]
                        self._dynamic[addr] = deadline
            log.info("substitutos propostos: %s", [n["id"] for n in nbs])
            self._connect_missing()  # não esperar pelo próximo ciclo de manutenção
        except (OSError, ValueError, KeyError) as e:
            log.warning("pedido de substitutos falhou (%s)", e)
        finally:
            self._replacing = False

    def _expire_dynamic(self, now):
        """Promove os substitutos que ligaram e esquece os que não ligaram a tempo."""
        with self._lock:
            for addr, deadline in list(self._dynamic.items()):
                nid = self._targets.get(addr)
                if nid in self.neighbors:
                    del self._dynamic[addr]  # ligou: passa a alvo normal
                elif now > deadline:
                    del self._dynamic[addr]
                    self._targets.pop(addr, None)
                    if nid:
                        self._lost.add(nid)  # não voltar a propô-lo já
                    log.info("substituto %s não ligou a tempo; descartado", nid)

    # -- mensagens -----------------------------------------------------

    def register_handler(self, msg_type, fn):
        """fn(node, neighbor_id, msg) é chamado para mensagens do tipo dado.

        ``neighbor_id`` é o ID overlay (string) do vizinho que enviou a mensagem.
        """
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

    def neighbor_address(self, node_id):
        """IP remoto usado pela ligação TCP ativa com um vizinho, ou None."""
        with self._lock:
            nb = self.neighbors.get(node_id)
        if nb is None:
            return None
        try:
            return nb.sock.getpeername()[0]
        except OSError:
            return None

    def neighbor_for_address(self, host):
        """ID do vizinho cuja ligação usa ``host`` como endereço remoto."""
        with self._lock:
            neighbors = list(self.neighbors.items())
        for node_id, nb in neighbors:
            try:
                if nb.sock.getpeername()[0] == host:
                    return node_id
            except OSError:
                continue
        return None

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
            elif msg["type"] in (MsgType.REGISTER, MsgType.REPLACE):
                self._serve_bootstrap(sock, msg)
                sock.close()
            else:
                sock.close()
        except (OSError, ValueError, KeyError) as e:
            log.debug("ligação de %s rejeitada: %s", addr, e)
            sock.close()

    def _serve_bootstrap(self, sock, msg):
        if not self.bootstrap_service:
            proto.send_msg(sock, make_msg(MsgType.ERROR, self.id,
                                          reason="este nó não é bootstrapper"))
            return
        if msg["type"] == MsgType.REGISTER:
            nbs = self.bootstrap_service.register(msg["src"])
        else:
            d = msg["data"]
            nbs = self.bootstrap_service.replacements(
                msg["src"], have=d["have"], lost=d["lost"], need=d["need"])
        if nbs is None:
            reply = make_msg(MsgType.ERROR, self.id, reason="nó desconhecido")
        else:
            reply = make_msg(MsgType.NEIGHBORS, self.id, neighbors=nbs)
        proto.send_msg(sock, reply)

    def _connect(self, target):
        try:
            self._try_connect(target)
        finally:
            with self._lock:
                self._connecting.discard(target)

    def _try_connect(self, target):
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
        try:
            # mensagens pequenas e em sentidos cruzados: sem isto o Nagle retém-nas à espera
            # do ACK e o RTT medido sai inflacionado e instável
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        nb = Neighbor(peer_id, sock, initiator)
        with self._lock:
            old = self.neighbors.get(peer_id)
            if old is not None:
                same_side = old.initiator == nb.initiator
                if not same_side and old.initiator < nb.initiator:
                    self._close_duplicate(sock)
                    return
                self._close_duplicate(old.sock)  # antiga obsoleta ou menos prioritária
            self.neighbors[peer_id] = nb
            self._lost.discard(peer_id)
            if len(self.neighbors) >= self.min_neighbors:
                self._deficit_since = None  # o défice acabou, mesmo que entre dois ciclos
            resumed = self._pending_dup.pop(peer_id, None) is not None
        sock.settimeout(None)
        log.info("HELLO: vizinho %s ligado (%s)", peer_id,
                 "iniciada por nós" if initiator == self.id else "recebida")
        if old is None and not resumed:  # senão o vizinho já estava up (duplicada trocada)
            self._fire(self._up_cbs, peer_id)
        self._read_loop(nb)

    def _close_duplicate(self, sock):
        """Fecha a ligação duplicada avisando o outro lado de que não é uma queda."""
        try:
            sock.settimeout(1.0)
            proto.send_msg(sock, make_msg(MsgType.BYE, self.id, reason="duplicate"))
        except OSError:
            pass
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()

    def _read_loop(self, nb):
        duplicate = False
        try:
            while not self._stop.is_set():
                msg = proto.recv_msg(nb.sock)
                if msg is None:
                    break
                if msg["type"] == MsgType.BYE:
                    duplicate = msg["data"].get("reason") == "duplicate"
                    break
                nb.last_seen = time.time()
                self._dispatch(nb, msg)
        except proto.ProtocolError as e:
            log.warning("mensagem inválida de %s (%s): ligação fechada", nb.id, e)
        except (OSError, ValueError, KeyError):
            pass
        self._drop(nb, duplicate)

    def _dispatch(self, nb, msg):
        mtype = msg["type"]
        if mtype == MsgType.PING:
            self.send(nb.id, make_msg(MsgType.PONG, self.id, ts=msg["data"]["ts"]))
        elif mtype == MsgType.PONG:
            sample = time.monotonic() - msg["data"]["ts"]
            if 0 <= sample <= RTT_MAX_SAMPLE:
                nb.update_rtt(sample)
        elif mtype == MsgType.HELLO:
            pass
        elif mtype in self._handlers:
            # contrato (protocolo.md §4): o handler recebe o ID do vizinho, não o objeto
            try:
                self._handlers[mtype](self, nb.id, msg)
            except Exception:
                # um erro no handler de outro módulo (rotas, streaming) não pode derrubar a ligação
                log.exception("erro no handler de %s (mensagem de %s)", mtype, nb.id)
        else:
            log.debug("mensagem %s ignorada (de %s)", mtype, nb.id)

    def _drop(self, nb, duplicate=False):
        """Remove a ligação. Se o outro lado a fechou por ser duplicada, não há `down`:
        espera-se pela ligação vencedora (`DUPLICATE_GRACE`), ver `_expire_duplicates`."""
        removed = False
        with self._lock:
            if self.neighbors.get(nb.id) is nb:
                del self.neighbors[nb.id]
                removed = True
                if duplicate and not self._stop.is_set():
                    self._pending_dup[nb.id] = time.time() + DUPLICATE_GRACE
                elif not self._stop.is_set():
                    self._lost.add(nb.id)
        if removed and duplicate:
            log.debug("ligação duplicada a %s fechada pelo vizinho", nb.id)
        elif removed and not self._stop.is_set():
            log.warning("vizinho %s desligado", nb.id)
            self._fire(self._down_cbs, nb.id)
        nb.close()

    # -- manutenção ----------------------------------------------------

    def _maintain_loop(self):
        last_ping = 0.0
        while not self._stop.is_set():
            now = time.time()
            self._connect_missing()
            if now - last_ping >= self.keepalive:
                last_ping = now
                self.broadcast(make_msg(MsgType.PING, self.id, ts=time.monotonic()))
            with self._lock:
                dead = [n for n in self.neighbors.values()
                        if now - n.last_seen > self.dead_after]
            for nb in dead:
                log.warning("vizinho %s sem resposta", nb.id)
                self._drop(nb)
            self._expire_duplicates(now)
            self._expire_dynamic(now)
            self._maybe_replace(now)
            self._stop.wait(min(self.retry_interval, self.keepalive))

    def _expire_duplicates(self, now):
        """A ligação vencedora não chegou a tempo: afinal foi uma queda."""
        with self._lock:
            gone = [i for i, deadline in self._pending_dup.items() if now >= deadline]
            for i in gone:
                del self._pending_dup[i]
                if i not in self.neighbors:
                    self._lost.add(i)
        for i in gone:
            if i not in self.neighbors:
                log.warning("vizinho %s desligado", i)
                self._fire(self._down_cbs, i)

    def _connect_missing(self):
        with self._lock:
            missing = [t for t, nid in self._targets.items()
                       if (nid is None or nid not in self.neighbors)
                       and t not in self._connecting
                       and t != (self.host, self.port)]
            self._connecting.update(missing)  # evita tentativas em paralelo ao mesmo alvo
        for target in missing:
            self._spawn(self._connect, target)
