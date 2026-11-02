"""Linha de comandos do nó overlay.

  ott [-i ID] [-p PORTA] <vizinho1> <vizinho2> ...     (estratégia 1: manual)
  ott [-i ID] [-p PORTA] -b <bootstrapper>             (estratégia 2: bootstrapper)
  ott [-i ID] [-p PORTA] --serve topologia.json        (nó bootstrapper/servidor)

Opções úteis nos testes:
  --min-neighbors X   pede substitutos ao bootstrapper se ficar com menos de X vizinhos
  --publish-flow ID   anuncia periodicamente um fluxo originado neste nó (repetível)
  --announce-interval S intervalo entre rondas de anúncios (default: 5 s)
  --stats SEGUNDOS    escreve periodicamente os vizinhos e o RTT de cada um

Os endereços têm o formato HOST ou HOST:PORTA.
"""
import argparse
import logging
import socket
import time

from .bootstrapper import BootstrapService
from .node import OverlayNode
from .protocol import DEFAULT_PORT
from .routing import AnnouncementPublisher, RoutingTable


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
                   help="intervalo entre rondas de anúncios (default: 5 s)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.publish_flow and args.announce_interval <= 0:
        raise SystemExit("--announce-interval tem de ser maior que zero")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format=f"%(asctime)s [{args.id}] %(levelname)s %(message)s")

    service = BootstrapService.from_file(args.serve, args.id) if args.serve else None
    node = OverlayNode(args.id, args.bind, args.port, bootstrap_service=service,
                       min_neighbors=args.min_neighbors,
                       replace_interval=args.replace_after)
    routing_table = RoutingTable()
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
            node.stop()
            raise SystemExit("não foi possível obter vizinhos do bootstrapper")

    if publisher:
        publisher.start()

    log = logging.getLogger("ott.cli")
    try:
        while True:
            time.sleep(args.stats or 1)
            if args.stats:
                log.info("vizinhos: %s", node.describe_neighbors())
    except KeyboardInterrupt:
        if publisher:
            publisher.stop()
        node.stop()
