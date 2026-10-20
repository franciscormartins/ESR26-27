"""Formato das mensagens de controlo do overlay.

Cada mensagem é um objeto JSON precedido por 4 bytes (big-endian) com o
tamanho do JSON, de forma a delimitar mensagens sobre TCP:

    {"type": "<TIPO>", "src": "<id do nó emissor>", "data": {...}}
"""
import json
import struct

DEFAULT_PORT = 5000
MAX_MSG_SIZE = 1 << 20

_HEADER = struct.Struct("!I")


class MsgType:
    # Etapa 1 - topologia overlay
    HELLO = "HELLO"          # handshake entre vizinhos
    REGISTER = "REGISTER"    # nó -> bootstrapper
    NEIGHBORS = "NEIGHBORS"  # bootstrapper -> nó (lista de vizinhos)
    ERROR = "ERROR"
    PING = "PING"            # keepalive
    PONG = "PONG"
    BYE = "BYE"
    # Etapa 2 - rotas (a definir pelo respetivo elemento)
    ANNOUNCE = "ANNOUNCE"
    ACTIVATE = "ACTIVATE"
    DEACTIVATE = "DEACTIVATE"
    # Etapa 3 - teste de caminhos
    ROUTE_TEST = "ROUTE_TEST"


def make_msg(msg_type, src, **data):
    return {"type": msg_type, "src": src, "data": data}


def send_msg(sock, msg):
    raw = json.dumps(msg, separators=(",", ":")).encode()
    if len(raw) > MAX_MSG_SIZE:
        raise ValueError("mensagem demasiado grande")
    sock.sendall(_HEADER.pack(len(raw)) + raw)


def _recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def recv_msg(sock):
    """Lê uma mensagem; devolve None se a ligação foi fechada."""
    header = _recv_exact(sock, _HEADER.size)
    if header is None:
        return None
    (size,) = _HEADER.unpack(header)
    if size > MAX_MSG_SIZE:
        raise ValueError("mensagem demasiado grande")
    raw = _recv_exact(sock, size)
    if raw is None:
        return None
    return json.loads(raw)
