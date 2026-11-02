"""Servidor de streaming: lê um vídeo MJPEG e emite pacotes numerados.

Baseado em ``progex/Servidor.py`` (exemplo do livro), com estas diferenças:
  * cada pacote leva (origem, fluxo) para o overlay o poder encaminhar;
  * o vídeo repete em ciclo (por omissão), em vez de o servidor ficar preso
    no fim do ficheiro como no exemplo;
  * o envio não está preso a um endereço: ``StreamSource`` chama uma função
    ``send(pacote)``. No overlay essa função entrega o pacote aos destinos da
    rota; em modo isolado envia por UDP para os endereços dados em ``--to``.

Uso isolado (sem overlay), por exemplo para testar o cliente:

    PYTHONPATH=src python3 -m ott.streaming.server --origin S1 --flow filme_a \\
        --video progex/movie.Mjpeg --to 127.0.0.1:6000
"""
import argparse
import logging
import random
import socket
import threading
import time

from .rtp import RtpPacket, pack_data, rtp_timestamp

DATA_PORT = 6000           # porta UDP do plano de dados em todos os nós
DEFAULT_FPS = 20           # o exemplo do livro envia uma imagem a cada 50 ms

log = logging.getLogger("ott.stream.server")


class MjpegFile:
    """Lê o formato do ``movie.Mjpeg``: cada imagem JPEG vem precedida de
    5 dígitos ASCII com o seu tamanho em bytes."""

    def __init__(self, path, loop=True):
        self.path = path
        self.loop = loop
        self.file = open(path, "rb")
        self.frame_number = 0

    def next_frame(self):
        """Devolve a próxima imagem, ou None no fim (se não estiver em ciclo)."""
        for _ in range(2):
            size = self.file.read(5)
            if len(size) == 5:
                frame = self.file.read(int(size))
                self.frame_number += 1
                return frame
            if not self.loop:
                return None
            self.file.seek(0)          # recomeça o vídeo
        raise ValueError(f"{self.path}: ficheiro vazio ou inválido")

    def close(self):
        self.file.close()


class StreamSource:
    """Emite um fluxo (origem, fluxo) a ``fps`` imagens por segundo."""

    def __init__(self, origin, flow, video, send, fps=DEFAULT_FPS, loop=True):
        self.origin = origin
        self.flow = flow
        self.video = MjpegFile(video, loop=loop)
        self.send = send
        self.interval = 1.0 / fps
        # SSRC aleatório (RFC 3550): identifica esta emissão. Se o servidor
        # reiniciar, o SSRC muda e o cliente sabe que a numeração recomeçou.
        self.ssrc = random.getrandbits(32)
        self.seq = 0
        self.sent = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"stream-{origin}:{flow}")

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def join(self, timeout=None):
        self._thread.join(timeout)

    def is_alive(self):
        return self._thread.is_alive()

    def _run(self):
        next_at = time.monotonic()
        while not self._stop.is_set():
            frame = self.video.next_frame()
            if frame is None:
                log.info("%s:%s fim do vídeo (%d pacotes)", self.origin, self.flow, self.sent)
                break
            self.seq = (self.seq + 1) & 0xFFFF
            rtp = RtpPacket(self.seq, rtp_timestamp(), frame, ssrc=self.ssrc).encode()
            try:
                self.send(pack_data(self.origin, self.flow, rtp))
                self.sent += 1
            except OSError as e:           # destino inalcançável não pára o fluxo
                log.debug("falha a enviar: %s", e)
            # ritmo constante: agenda pelo relógio, não por sleep fixo, para
            # o tempo de leitura/envio não se ir acumulando
            next_at += self.interval
            delay = next_at - time.monotonic()
            if delay > 0:
                self._stop.wait(delay)
            else:
                next_at = time.monotonic()
        self.video.close()


def udp_sender(targets):
    """Função send() que envia uma cópia para cada (host, porta) - unicast
    múltiplo, a solução "naïve" que o overlay pretende evitar."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(packet):
        for addr in targets:
            sock.sendto(packet, addr)
    send.sock = sock
    return send


def parse_target(text):
    host, _, port = text.partition(":")
    return host, int(port) if port else DATA_PORT


def main(argv=None):
    p = argparse.ArgumentParser(description="servidor de streaming isolado (sem overlay)")
    p.add_argument("--origin", default="S1", help="id do servidor (origem do fluxo)")
    p.add_argument("--flow", default="filme_a", help="nome do fluxo")
    p.add_argument("--video", default="progex/movie.Mjpeg")
    p.add_argument("--to", action="append", type=parse_target, required=True,
                   metavar="HOST[:PORTA]", help="destino UDP (repetir para vários)")
    p.add_argument("--fps", type=float, default=DEFAULT_FPS)
    p.add_argument("--once", action="store_true", help="não repetir o vídeo")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    source = StreamSource(args.origin, args.flow, args.video, udp_sender(args.to),
                          fps=args.fps, loop=not args.once).start()
    log.info("a enviar %s:%s para %s", args.origin, args.flow, args.to)
    try:
        while source.is_alive():
            source.join(5)
            log.info("%d pacotes enviados", source.sent)
    except KeyboardInterrupt:
        source.stop()


if __name__ == "__main__":
    main()
