"""Formato das mensagens de controlo do overlay.

Cada mensagem é um objeto JSON precedido por 4 bytes (big-endian) com o
tamanho do JSON, de forma a delimitar mensagens sobre TCP:

    {"type": "<TIPO>", "src": "<id do nó emissor>", "data": {...}}

Os campos obrigatórios de cada tipo estão em REQUIRED e são validados tanto
ao enviar como ao receber (ver docs/protocolo.md).
"""
import json
import math
import struct

DEFAULT_PORT = 5000
MAX_MSG_SIZE = 1 << 20

_HEADER = struct.Struct("!I")


class ProtocolError(ValueError):
    """Mensagem malformada ou com campos em falta/inválidos."""


class MsgType:
    # Etapa 1 - topologia overlay
    HELLO = "HELLO"          # handshake entre vizinhos
    REGISTER = "REGISTER"    # nó -> bootstrapper
    REPLACE = "REPLACE"      # nó -> bootstrapper: pede vizinhos substitutos
    NEIGHBORS = "NEIGHBORS"  # bootstrapper -> nó (lista de vizinhos)
    ERROR = "ERROR"
    PING = "PING"            # keepalive (leva ts; serve para medir o RTT)
    PONG = "PONG"            # resposta ao PING (devolve o mesmo ts)
    BYE = "BYE"
    # Etapa 2 - anúncios e ativação de rotas
    ANNOUNCE = "ANNOUNCE"
    ACTIVATE = "ACTIVATE"
    DEACTIVATE = "DEACTIVATE"
    # Etapa 3 - teste de caminhos
    ROUTE_TEST = "ROUTE_TEST"


ALL_TYPES = frozenset(v for k, v in vars(MsgType).items() if not k.startswith("_"))


def _is_str(v):
    return isinstance(v, str) and v != ""


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_nonnegative_num(v):
    return _is_num(v) and math.isfinite(v) and v >= 0


def _is_pos_int(v):
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def _is_id_list(v):
    return isinstance(v, list) and all(_is_str(i) for i in v)


def _is_neighbor_list(v):
    return isinstance(v, list) and all(
        isinstance(n, dict) and _is_str(n.get("id")) and _is_str(n.get("host"))
        and isinstance(n.get("port"), int) and not isinstance(n.get("port"), bool)
        for n in v)


# campos obrigatórios em "data" por tipo: {campo: predicado}
REQUIRED = {
    MsgType.PING: {"ts": _is_num},
    MsgType.PONG: {"ts": _is_num},
    MsgType.NEIGHBORS: {"neighbors": _is_neighbor_list},
    MsgType.ERROR: {"reason": _is_str},
    MsgType.REPLACE: {"have": _is_id_list, "lost": _is_id_list, "need": _is_pos_int},
    MsgType.ANNOUNCE: {
        "server_id": _is_str, "flow": _is_str,
        "epoch": _is_pos_int, "metric": _is_nonnegative_num,
    },
    MsgType.ACTIVATE: {
        "server_id": _is_str, "flow": _is_str, "subscriber_id": _is_str,
    },
    MsgType.DEACTIVATE: {
        "server_id": _is_str, "flow": _is_str, "subscriber_id": _is_str,
    },
}


def make_msg(msg_type, src, **data):
    return {"type": msg_type, "src": src, "data": data}


def validate(msg):
    """Devolve a mensagem se for válida; levanta ProtocolError caso contrário."""
    if not isinstance(msg, dict):
        raise ProtocolError("mensagem não é um objeto JSON")
    mtype = msg.get("type")
    if mtype not in ALL_TYPES:
        raise ProtocolError(f"tipo desconhecido: {mtype!r}")
    if not _is_str(msg.get("src")):
        raise ProtocolError(f"{mtype}: campo 'src' em falta ou inválido")
    data = msg.get("data")
    if not isinstance(data, dict):
        raise ProtocolError(f"{mtype}: campo 'data' em falta ou inválido")
    for field, ok in REQUIRED.get(mtype, {}).items():
        if field not in data:
            raise ProtocolError(f"{mtype}: falta o campo '{field}'")
        if not ok(data[field]):
            raise ProtocolError(f"{mtype}: campo '{field}' inválido: {data[field]!r}")
    return msg


def send_msg(sock, msg):
    validate(msg)
    raw = json.dumps(msg, separators=(",", ":")).encode()
    if len(raw) > MAX_MSG_SIZE:
        raise ProtocolError("mensagem demasiado grande")
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
    """Lê uma mensagem; devolve None se a ligação foi fechada.

    Levanta ProtocolError se o conteúdo não for uma mensagem válida.
    """
    header = _recv_exact(sock, _HEADER.size)
    if header is None:
        return None
    (size,) = _HEADER.unpack(header)
    if size > MAX_MSG_SIZE:
        raise ProtocolError("mensagem demasiado grande")
    raw = _recv_exact(sock, size)
    if raw is None:
        return None
    try:
        msg = json.loads(raw)
    except ValueError as e:  # inclui JSONDecodeError e UnicodeDecodeError
        raise ProtocolError(f"JSON inválido: {e}") from e
    return validate(msg)
