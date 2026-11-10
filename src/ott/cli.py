"""Linha de comandos do nó overlay.

  ott [-i ID] [-p PORTA] <vizinho1> <vizinho2> ...     (estratégia 1: manual)
  ott [-i ID] [-p PORTA] -b <bootstrapper>             (estratégia 2: bootstrapper)
  ott [-i ID] [-p PORTA] --serve topologia.json        (nó bootstrapper/servidor)

Opções úteis nos testes:
  --min-neighbors X   pede substitutos ao bootstrapper se ficar com menos de X vizinhos
  --publish-flow ID   anuncia periodicamente um fluxo originado neste nó (repetível)
  --announce-interval S intervalo entre rondas de anúncios (default: 5 s)
  --route-ttl S       esquecer rotas sem anúncios há S segundos (default: 6 x intervalo)
  --stats SEGUNDOS    escreve periodicamente os vizinhos e o RTT de cada um
  --subscribe S:F     subscreve o fluxo F do servidor S (repetível)
  --gui               mostra o vídeo da primeira subscrição numa janela
  --status-file F     escreve o estado do nó (vizinhos, rotas, fluxos, eventos) em JSON, para o ottctl

Os endereços têm o formato HOST ou HOST:PORTA.
"""
import argparse
import logging
import math
import os
import signal
import socket
import threading
import time

from .bootstrapper import BootstrapService
from .forwarding import OverlayForwarder
from .node import OverlayNode
from .protocol import DEFAULT_PORT
from .routing import AnnouncementPublisher, RoutingTable
from .status import StatusWriter
from .streaming.client import StreamReceiver, run_gui
from .streaming.server import DATA_PORT, StreamSource


def parse_addr(text):
    host, _, port = text.partition(":")
    return host, int(port) if port else DEFAULT_PORT


def build_parser():
    p = argparse.ArgumentParser(prog="ott", description=__doc__,
                                formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("neighbors", nargs="*", metavar="vizinho",
                   help="vizinhos overlay (estratégia 1)")
    p.add_argument("-i", "--id", default=socket.gethostname(),
                   help="identificador do nó (default: hostname)")
    p.add_argument("-p", "--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--bind", default="0.0.0.0")
    p.add_argument("-b", "--bootstrapper", metavar="HOST[:PORTA]")
    p.add_argument("--serve", metavar="TOPOLOGIA",
                   help="atuar como bootstrapper com este ficheiro de topologia")
    p.add_argument("--min-neighbors", type=int, default=0, metavar="X",
                   help="mínimo de vizinhos a manter (0 = não pedir substitutos)")
    p.add_argument("--replace-after", type=float, default=5.0, metavar="S",
                   help="segundos com vizinhos a menos antes de pedir substitutos")
    p.add_argument("--stats", type=float, default=0, metavar="S",
                   help="escrever vizinhos e RTT a cada S segundos (0 = nunca)")
    p.add_argument("--publish-flow", action="append", default=[], metavar="ID",
                   help="anunciar este fluxo periodicamente (opção repetível)")
    p.add_argument("--announce-interval", type=float, default=5.0, metavar="S",
                   help="intervalo entre rondas de anúncios (default: 5 s); "
                        "usar o mesmo valor em todos os nós")
    p.add_argument("--route-ttl", type=float, default=None, metavar="S",
                   help="tempo sem anúncios ao fim do qual um caminho é esquecido "
                        "(default: 6 x --announce-interval)")
    p.add_argument("--subscribe", action="append", default=[], metavar="SERVIDOR:FLUXO",
                   help="subscrever um fluxo anunciado por um servidor (opção repetível)")
    p.add_argument("--subscriber-id", help="ID do cliente (por omissão usa o ID deste nó)")
    p.add_argument("--video", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "progex", "movie.Mjpeg"), help="vídeo MJPEG publicado (default: progex/movie.Mjpeg)")
    p.add_argument("--fps", type=float, default=20.0,
                   help="imagens por segundo para cada fluxo publicado")
    p.add_argument("--data-port", type=int, default=DATA_PORT,
                   help="porta UDP do plano de dados (default: 6000)")
    p.add_argument("--gui", action="store_true",
                   help="mostrar o vídeo da primeira subscrição numa janela (precisa de Tkinter e Pillow)")
    p.add_argument("--status-file", metavar="FICHEIRO",
                   help="escrever o estado do nó em JSON neste ficheiro (usado pelo ottctl e pelo painel web)")
    p.add_argument("--status-interval", type=float, default=1.0, metavar="S",
                   help="intervalo entre escritas do estado (default: 1 s)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def _install_signal_handlers():
    """SIGTERM (``kill``, ottctl) sai como o Ctrl+C: com BYE aos vizinhos e o estado final escrito.

    Também repõe o SIGINT, que um processo lançado em segundo plano por uma shell não interativa herda ignorado.
    """
    def stop(_signum, _frame):
        raise KeyboardInterrupt
    try:
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, signal.default_int_handler)
    except ValueError:   # fora da thread principal (por exemplo nos testes)
        pass


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not math.isfinite(args.announce_interval) or args.announce_interval <= 0:
        raise SystemExit("--announce-interval tem de ser finito e maior que zero")
    route_ttl = (6 * args.announce_interval if args.route_ttl is None
                 else args.route_ttl)
    if not math.isfinite(route_ttl) or route_ttl <= args.announce_interval:
        raise SystemExit("--route-ttl tem de ser finito e maior que --announce-interval")
    if args.publish_flow and (not math.isfinite(args.fps) or args.fps <= 0):
        raise SystemExit("--fps tem de ser finito e maior que zero")
    if not 1 <= args.data_port <= 65535:
        raise SystemExit("--data-port tem de estar entre 1 e 65535")
    subscriptions = []
    for value in args.subscribe:
        server_id, separator, flow = value.partition(":")
        if not separator or not server_id or not flow:
            raise SystemExit(f"--subscribe inválido: {value!r}; usa SERVIDOR:FLUXO")
        subscriptions.append((server_id, flow))
    if args.gui and not subscriptions:
        raise SystemExit("--gui precisa de pelo menos uma --subscribe")
    if len(set(subscriptions)) != len(subscriptions):
        raise SystemExit("--subscribe não pode repetir o mesmo servidor e fluxo")
    if not math.isfinite(args.status_interval) or args.status_interval <= 0:
        raise SystemExit("--status-interval tem de ser finito e maior que zero")
    _install_signal_handlers()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format=f"%(asctime)s [{args.id}] %(levelname)s %(message)s")

    service = BootstrapService.from_file(args.serve, args.id) if args.serve else None
    node = OverlayNode(args.id, args.bind, args.port, bootstrap_service=service,
                       min_neighbors=args.min_neighbors,
                       replace_interval=args.replace_after)
    routing_table = RoutingTable(route_ttl=route_ttl)
    routing_table.attach(node)
    publisher = (AnnouncementPublisher(node, args.publish_flow, args.announce_interval,
                                       routing_table=routing_table)
                 if args.publish_flow else None)
    node.start()

    if service and args.id in service.nodes:
        node.add_targets({service.address_of(n["id"]): n["id"]
                          for n in service.neighbors_of(args.id)})
    if args.neighbors:
        node.add_targets({parse_addr(a): None for a in args.neighbors})
    if args.bootstrapper:
        host, port = parse_addr(args.bootstrapper)
        if not node.bootstrap(host, port):
            routing_table.close()
            node.stop()
            raise SystemExit("não foi possível obter vizinhos do bootstrapper")

    forwarder = OverlayForwarder(node, routing_table, port=args.data_port, bind=args.bind).start()
    receivers = {}
    for origin, flow in subscriptions:
        receiver = StreamReceiver(origin, flow, port=args.data_port, listen=False)
        receivers[(origin, flow)] = receiver
        forwarder.set_local_receiver(origin, flow, receiver)

    if publisher:
        publisher.start()

    sources = [StreamSource(node.id, flow, args.video, forwarder.send,
                            fps=args.fps, loop=True).start()
               for flow in args.publish_flow]
    pending_subscriptions = set(subscriptions)
    active_subscriptions = set()
    status = None
    if args.status_file:
        status = StatusWriter(args.status_file, node, routing_table, forwarder, receivers,
                              published=args.publish_flow,
                              subscriptions=lambda: (set(pending_subscriptions), set(active_subscriptions)),
                              interval=args.status_interval).start()

    log = logging.getLogger("ott.cli")
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            stop.wait(args.stats or 1)
            for origin, flow in tuple(pending_subscriptions):
                if routing_table.get(origin, flow) is not None and routing_table.activate(
                        node, origin, flow, subscriber_id=args.subscriber_id):
                    pending_subscriptions.discard((origin, flow))
                    active_subscriptions.add((origin, flow))
                    log.info("subscrito a %s:%s", origin, flow)
            if args.stats:
                log.info("vizinhos: %s", node.describe_neighbors())
                for key, receiver in receivers.items():
                    if key in active_subscriptions:
                        log.info("fluxo %s:%s: %s", key[0], key[1], receiver.stats)
                log.info("dados UDP: recebidos=%d encaminhados=%d descartados=%d (RPF=%d)",
                         forwarder.received, forwarder.forwarded, forwarder.dropped,
                         forwarder.rpf_dropped)
    try:
        if args.gui:
            worker = threading.Thread(target=loop, daemon=True, name="ott-cli-loop")
            worker.start()
            origin, flow = subscriptions[0]
            run_gui(receivers[(origin, flow)], f"OTT {args.id} - {origin}:{flow}")
        else:
            loop()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        if status is not None:
            status.stop()
        for source in sources:
            source.stop()
        for source in sources:
            source.join(timeout=2.0)
        for origin, flow in active_subscriptions:
            routing_table.deactivate(node, origin, flow,
                                     subscriber_id=args.subscriber_id)
        if publisher:
            publisher.stop()
        forwarder.stop()
        for receiver in receivers.values():
            receiver.stop()
        routing_table.close()
        node.stop()
