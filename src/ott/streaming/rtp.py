"""Formato dos pacotes de vídeo (plano de dados, UDP).

Cada datagrama UDP leva um pacote RTP (como no exemplo do livro) precedido
por um pequeno cabeçalho do overlay que diz a que transmissão pertence:

    +------+-----+---------+----------+-------+--------+---------------------+
    | "OT" | ver | len(o)  | origem   | len(f)| fluxo  | pacote RTP          |
    | 2 B  | 1 B | 1 B     | len(o) B | 1 B   | len(f) | 12 B cabeçalho+JPEG |
    +------+-----+---------+----------+-------+--------+---------------------+

- (origem, fluxo) é a mesma chave da tabela de rotas (``RoutingTable``): é com
  ela que um nó intermédio sabe para que vizinhos replicar o pacote.
- O cabeçalho RTP segue o RFC 3550 (versão 2, PT 26 = JPEG). O número de
  sequência (16 bits) permite ao cliente detetar perdas e pacotes fora de
  ordem; o timestamp usa o relógio de 90 kHz habitual em vídeo, para se poder
  calcular o jitter (o exemplo do livro usava segundos inteiros).
"""
import struct
import time

MAGIC = b"OT"
VERSION = 1
RTP_HEADER_SIZE = 12
PT_JPEG = 26
RTP_CLOCK = 90000          # Hz
MAX_DATAGRAM = 65507       # maior payload UDP sobre IPv4

_RTP = struct.Struct("!BBHII")


class PacketError(ValueError):
    """Datagrama que não respeita o formato."""


def rtp_timestamp(now=None):
    """Instante atual no relógio RTP de 90 kHz (32 bits, dá a volta)."""
    if now is None:
        now = time.monotonic()
    return int(now * RTP_CLOCK) & 0xFFFFFFFF


class RtpPacket:
    def __init__(self, seq, timestamp, payload, pt=PT_JPEG, ssrc=0, marker=0):
        self.seq = seq & 0xFFFF
        self.timestamp = timestamp & 0xFFFFFFFF
        self.payload = payload
        self.pt = pt
        self.ssrc = ssrc & 0xFFFFFFFF
        self.marker = marker

    def encode(self):
        byte0 = 2 << 6                       # versão 2, sem padding/extensão/CSRC
        byte1 = (self.marker << 7) | (self.pt & 0x7F)
        return _RTP.pack(byte0, byte1, self.seq, self.timestamp, self.ssrc) + self.payload

    @classmethod
    def decode(cls, data):
        if len(data) < RTP_HEADER_SIZE:
            raise PacketError("pacote RTP demasiado curto")
        byte0, byte1, seq, ts, ssrc = _RTP.unpack_from(data)
        if byte0 >> 6 != 2:
            raise PacketError("versão RTP inválida")
        return cls(seq, ts, data[RTP_HEADER_SIZE:], pt=byte1 & 0x7F,
                   ssrc=ssrc, marker=byte1 >> 7)


def _name(text, what):
    raw = text.encode()
    if not 0 < len(raw) < 256:
        raise PacketError(f"{what} tem de ter entre 1 e 255 bytes")
    return bytes([len(raw)]) + raw


def pack_data(origin, flow, rtp_bytes):
    """Junta o cabeçalho do overlay a um pacote RTP já codificado."""
    data = MAGIC + bytes([VERSION]) + _name(origin, "origem") + _name(flow, "fluxo") + rtp_bytes
    if len(data) > MAX_DATAGRAM:
        raise PacketError("imagem demasiado grande para um datagrama UDP")
    return data


def unpack_data(data):
    """Devolve (origem, fluxo, bytes RTP); levanta PacketError se inválido."""
    if len(data) < 5 or data[:2] != MAGIC:
        raise PacketError("não é um pacote de dados do overlay")
    if data[2] != VERSION:
        raise PacketError(f"versão {data[2]} desconhecida")
    pos = 3
    names = []
    for _ in range(2):
        if pos >= len(data):
            raise PacketError("cabeçalho truncado")
        size = data[pos]
        end = pos + 1 + size
        if size == 0 or end > len(data):
            raise PacketError("cabeçalho truncado")
        names.append(data[pos + 1:end].decode())
        pos = end
    return names[0], names[1], data[pos:]


def seq_newer(a, b):
    """True se o nº de sequência ``a`` vem depois de ``b`` (com volta aos 65535)."""
    return a != b and ((a - b) & 0xFFFF) < 0x8000
