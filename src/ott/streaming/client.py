"""Cliente de streaming: recebe os pacotes de um fluxo e mostra o vídeo.

Baseado em ``progex/ClienteGUI.py`` (exemplo do livro), com estas diferenças:
  * nunca desiste por falta de pacotes: o timeout do socket serve só para
    poder parar a thread; o exemplo fechava o socket ao fim de 0,5 s sem dados
    (e por isso tinha de arrancar depois do servidor);
  * só aceita o fluxo pedido (origem, fluxo) e ignora outros pacotes;
  * mede o que acontece na rede: recebidos, perdidos (buracos na sequência),
    atrasados/duplicados descartados e jitter (RFC 3550);
  * se a emissão mudar (outro SSRC, p.ex. o servidor reiniciou), recomeça a
    contagem da sequência em vez de descartar os pacotes como "atrasados";
  * mostra a imagem diretamente da memória (o exemplo gravava cada imagem em
    ``cache-*.jpg`` no disco).

Uso isolado (sem overlay):

    PYTHONPATH=src python3 -m ott.streaming.client --port 6000 --origin S1 --flow filme_a
    (acrescentar --no-gui para só ver as estatísticas)
"""
import argparse
import logging
import socket
import threading
import time

from .rtp import RTP_CLOCK, PacketError, RtpPacket, seq_newer, unpack_data
from .server import DATA_PORT

log = logging.getLogger("ott.stream.client")


class StreamStats:
    """Contadores do fluxo recebido."""

    def __init__(self):
        self.received = 0
        self.lost = 0             # nº de sequência que nunca chegaram (estimativa)
        self.late = 0             # chegaram depois de um mais recente: descartados
        self.jitter = 0.0         # segundos, média exponencial do RFC 3550
        self.restarts = 0         # vezes que a emissão mudou (SSRC novo)
        self.ssrc = None
        self.last_seq = None
        self._last_transit = None

    def update(self, rtp, arrival):
        """Regista um pacote; devolve True se deve ser mostrado."""
        if rtp.ssrc != self.ssrc:
            if self.ssrc is not None:
                self.restarts += 1        # emissão nova: a sequência recomeça
            self.ssrc = rtp.ssrc
            self.last_seq = None
            self._last_transit = None
        if self.last_seq is not None and not seq_newer(rtp.seq, self.last_seq):
            self.late += 1
            return False
        if self.last_seq is not None:
            self.lost += ((rtp.seq - self.last_seq) & 0xFFFF) - 1
        self.last_seq = rtp.seq
        self.received += 1
        # jitter: variação do tempo de trânsito entre pacotes consecutivos
        transit = arrival - rtp.timestamp / RTP_CLOCK
        if self._last_transit is not None:
            d = abs(transit - self._last_transit)
            self.jitter += (d - self.jitter) / 16
        self._last_transit = transit
        return True

    def loss_ratio(self):
        total = self.received + self.lost
        return self.lost / total if total else 0.0

    def __str__(self):
        return (f"recebidos={self.received} perdidos={self.lost} "
                f"({self.loss_ratio():.1%}) descartados={self.late} "
                f"jitter={self.jitter * 1000:.2f} ms"
                + (f" reinicios={self.restarts}" if self.restarts else ""))


class StreamReceiver:
    """Escuta numa porta UDP e entrega as imagens do fluxo a ``on_frame``."""

    def __init__(self, origin, flow, port=DATA_PORT, bind="0.0.0.0", on_frame=None):
        self.origin = origin
        self.flow = flow
        self.on_frame = on_frame or (lambda frame, rtp: None)
        self.stats = StreamStats()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((bind, port))
        self.sock.settimeout(1.0)
        self.port = self.sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="stream-rx")

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._thread.join(2)
        self.sock.close()

    def _run(self):
        while not self._stop.is_set():
            try:
                data = self.sock.recv(65535)
            except socket.timeout:
                continue                    # sem vídeo por agora: continuar à espera
            except OSError:
                break                       # socket fechado em stop()
            self.handle(data, time.monotonic())

    def handle(self, data, arrival):
        try:
            origin, flow, rtp_bytes = unpack_data(data)
            rtp = RtpPacket.decode(rtp_bytes)
        except PacketError as e:
            log.debug("pacote ignorado: %s", e)
            return
        if (origin, flow) != (self.origin, self.flow):
            return                          # outro fluxo
        if self.stats.update(rtp, arrival):
            self.on_frame(rtp.payload, rtp)


def run_gui(receiver, title):
    """Janela Tkinter que mostra a imagem mais recente (como no exemplo)."""
    import io
    import tkinter as tk
    from PIL import Image, ImageTk

    root = tk.Tk()
    root.title(title)
    label = tk.Label(root, text="à espera do vídeo...", width=48, height=18)
    label.pack(padx=5, pady=5)
    status = tk.Label(root, text="", anchor="w")
    status.pack(fill="x", padx=5)

    latest = {"frame": None}
    receiver.on_frame = lambda frame, rtp: latest.update(frame=frame)

    def refresh():
        frame, latest["frame"] = latest["frame"], None
        if frame is not None:
            try:
                photo = ImageTk.PhotoImage(Image.open(io.BytesIO(frame)))
                label.configure(image=photo, width=photo.width(), height=photo.height())
                label.image = photo
            except Exception as e:          # imagem corrompida: salta-se
                log.debug("imagem inválida: %s", e)
        status.configure(text=str(receiver.stats))
        root.after(20, refresh)             # a GUI só pode ser mexida nesta thread

    refresh()
    root.mainloop()


def main(argv=None):
    p = argparse.ArgumentParser(description="cliente de streaming isolado (sem overlay)")
    p.add_argument("--origin", default="S1")
    p.add_argument("--flow", default="filme_a")
    p.add_argument("--port", type=int, default=DATA_PORT)
    p.add_argument("--no-gui", action="store_true", help="só escrever estatísticas")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    receiver = StreamReceiver(args.origin, args.flow, port=args.port).start()
    log.info("à escuta de %s:%s na porta UDP %d", args.origin, args.flow, receiver.port)
    try:
        if args.no_gui:
            while True:
                time.sleep(2)
                log.info("%s", receiver.stats)
        else:
            run_gui(receiver, f"OTT - {args.origin}:{args.flow}")
    except KeyboardInterrupt:
        pass
    finally:
        receiver.stop()
        log.info("final: %s", receiver.stats)


if __name__ == "__main__":
    main()
