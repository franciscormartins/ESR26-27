"""Linha de comandos do nó overlay.

  ott [-i ID] [-p PORTA] <vizinho1> <vizinho2> ...     (estratégia 1: manual)
  ott [-i ID] [-p PORTA] -b <bootstrapper>             (estratégia 2: bootstrapper)
  ott [-i ID] [-p PORTA] --serve topologia.json        (nó bootstrapper/servidor)

Os endereços têm o formato HOST ou HOST:PORTA.
"""
import argparse
import logging
import socket
import time

from .bootstrapper import BootstrapService
from .node import OverlayNode
from .protocol import DEFAULT_PORT


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
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format=f"%(asctime)s [{args.id}] %(levelname)s %(message)s")

    service = BootstrapService.from_file(args.serve) if args.serve else None
    node = OverlayNode(args.id, args.bind, args.port, bootstrap_service=service)
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

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        node.stop()
