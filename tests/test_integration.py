# Ensaios de integração da Etapa 2 com OverlayNode reais (TCP/UDP em localhost).
# PYTHONPATH=src python3 -m unittest tests.test_integration -v
#
# Ao contrário de tests/test_routing.py, que usa nós falsos, estes testes ligam a
# RoutingTable a nós reais e confirmam o contrato entre node.py e routing.py.
import socket
import time
import unittest

import os

from ott.forwarding import OverlayForwarder
from ott.node import OverlayNode
from ott.protocol import MsgType
from ott.routing import ACTIVE, INACTIVE, AnnouncementPublisher, RoutingTable
from ott.streaming.client import StreamReceiver
from ott.streaming.server import StreamSource

from tests.test_streaming import write_mjpeg

BASE = 47500
FLOW = "filme_a"


def wait_for(cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def make_node(nid, port, host="127.0.0.1"):
    return OverlayNode(nid, host, port, keepalive=0.2, dead_after=1.0,
                       retry_interval=0.2)


def loopback_aliases_available(*hosts):
    """Linux aceita qualquer 127.0.0.0/8; noutros sistemas pode não haver aliases."""
    for host in hosts:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((host, 0))
        except OSError:
            return False
        finally:
            sock.close()
    return True


class RoutingIntegrationTest(unittest.TestCase):
    """Cadeia S1 - A - C: anúncios, ativação e quedas com nós reais."""

    def setUp(self):
        self.nodes = {}
        self.tables = {}
        self.publisher = None
        for offset, nid in enumerate(("S1", "A", "C")):
            node = self.nodes[nid] = make_node(nid, BASE + offset)
            self.tables[nid] = RoutingTable().attach(node)
            node.start()
        # Contar os anúncios que chegam ao servidor, sem mudar o tratamento.
        self.announces_at_server = []
        table = self.tables["S1"]

        def counting_handler(node, neighbor, msg):
            self.announces_at_server.append(msg)
            return table.handle_announce(node, neighbor, msg)
        self.nodes["S1"].register_handler(MsgType.ANNOUNCE, counting_handler)

        self.nodes["A"].add_targets({("127.0.0.1", BASE): "S1",
                                     ("127.0.0.1", BASE + 2): "C"})
        expected = {"S1": ["A"], "A": ["C", "S1"], "C": ["A"]}
        for nid, neighbors in expected.items():
            self.assertTrue(wait_for(lambda: self.nodes[nid].neighbor_ids() == neighbors),
                            f"{nid}: {self.nodes[nid].neighbor_ids()}")
        self.publisher = AnnouncementPublisher(
            self.nodes["S1"], [FLOW], interval=0.3, routing_table=self.tables["S1"]).start()
        self.assertTrue(wait_for(lambda: self.tables["C"].get("S1", FLOW) is not None))

    def tearDown(self):
        if self.publisher is not None:
            self.publisher.stop()
        for node in self.nodes.values():
            node.stop()

    def test_routes_store_neighbor_ids_and_hop_metric(self):
        route_a = self.tables["A"].get("S1", FLOW)
        route_c = self.tables["C"].get("S1", FLOW)
        self.assertEqual((route_a.upstream, route_a.metric), ("S1", 1))
        self.assertEqual((route_c.upstream, route_c.metric), ("A", 2))
        self.assertIsInstance(route_a.upstream, str)
        self.assertEqual((route_a.state, route_c.state), (INACTIVE, INACTIVE))

    def test_flood_does_not_return_announcement_to_sender(self):
        time.sleep(1.0)  # várias rondas de anúncios
        senders = {msg["src"] for msg in self.announces_at_server}
        self.assertEqual(senders, set(), "A devolveu o anúncio ao servidor que o enviou")

    def test_activation_builds_branch_and_deactivation_removes_it(self):
        self.assertTrue(self.tables["C"].activate(self.nodes["C"], "S1", FLOW))
        self.assertTrue(wait_for(
            lambda: self.tables["S1"].active_destinations("S1", FLOW) == ("A",)))
        self.assertEqual(self.tables["A"].active_destinations("S1", FLOW), ("C",))
        self.assertEqual(self.tables["C"].get("S1", FLOW).state, ACTIVE)

        self.assertTrue(self.tables["C"].deactivate(self.nodes["C"], "S1", FLOW))
        self.assertTrue(wait_for(
            lambda: self.tables["S1"].active_destinations("S1", FLOW) == ()))
        self.assertEqual(self.tables["A"].active_destinations("S1", FLOW), ())

    def test_downstream_failure_removes_branch_up_to_server(self):
        self.assertTrue(self.tables["C"].activate(self.nodes["C"], "S1", FLOW))
        self.assertTrue(wait_for(
            lambda: self.tables["S1"].active_destinations("S1", FLOW) == ("A",)))
        self.nodes["C"].stop()
        self.assertTrue(wait_for(
            lambda: self.tables["S1"].active_destinations("S1", FLOW) == ()))
        self.assertEqual(self.tables["A"].active_destinations("S1", FLOW), ())

    def test_upstream_failure_invalidates_route(self):
        self.assertTrue(self.tables["C"].activate(self.nodes["C"], "S1", FLOW))
        self.nodes["A"].stop()
        route_c = self.tables["C"].get("S1", FLOW)
        self.assertTrue(wait_for(lambda: route_c.upstream is None))
        self.assertEqual(route_c.state, INACTIVE)
        self.assertEqual(route_c.local_subscribers, {"C"})  # pedido fica pendente


DATA_HOSTS = {"A": "127.0.0.1", "S1": "127.0.0.2", "C1": "127.0.0.3", "C2": "127.0.0.4"}
DATA_PORT = 47590
FRAMES = 20


@unittest.skipUnless(loopback_aliases_available(*DATA_HOSTS.values()),
                     "requer endereços de loopback 127.0.0.2-4")
class DataPlaneIntegrationTest(unittest.TestCase):
    """S1 - A - {C1, C2}: o vídeo segue pelos ramos ativos, uma cópia por troço.

    Todos os forwarders usam a mesma porta UDP (como no CORE), por isso cada nó
    tem o seu IP de loopback. O A tem 127.0.0.1 e inicia todas as ligações TCP:
    um socket de saída sem bind sai por 127.0.0.1, e assim o IP que cada nó vê
    na ligação TCP coincide com a origem dos datagramas UDP do vizinho.
    """

    def setUp(self):
        self.nodes, self.tables, self.forwarders, self.receivers = {}, {}, {}, {}
        self.publisher = None
        self.video = write_mjpeg([b"frame%d" % i for i in range(FRAMES)])
        for offset, nid in enumerate(("S1", "A", "C1", "C2")):
            host = DATA_HOSTS[nid]
            node = self.nodes[nid] = make_node(nid, BASE + 10 + offset, host)
            self.tables[nid] = RoutingTable().attach(node)
            node.start()
            self.forwarders[nid] = OverlayForwarder(
                node, self.tables[nid], port=DATA_PORT, bind=host).start()
        for nid in ("C1", "C2"):
            receiver = self.receivers[nid] = StreamReceiver("S1", FLOW, listen=False)
            self.forwarders[nid].set_local_receiver("S1", FLOW, receiver)

        self.nodes["A"].add_targets({
            (DATA_HOSTS[nid], BASE + 10 + offset): nid
            for offset, nid in enumerate(("S1", "A", "C1", "C2")) if nid != "A"})
        self.assertTrue(wait_for(
            lambda: self.nodes["A"].neighbor_ids() == ["C1", "C2", "S1"]))
        self.publisher = AnnouncementPublisher(
            self.nodes["S1"], [FLOW], interval=0.3, routing_table=self.tables["S1"]).start()
        for nid in ("C1", "C2"):
            self.assertTrue(wait_for(lambda: self.tables[nid].get("S1", FLOW) is not None))
            self.assertTrue(self.tables[nid].activate(self.nodes[nid], "S1", FLOW))
        self.assertTrue(wait_for(
            lambda: self.tables["A"].active_destinations("S1", FLOW) == ("C1", "C2")))
        self.assertTrue(wait_for(
            lambda: self.tables["S1"].active_destinations("S1", FLOW) == ("A",)))

    def tearDown(self):
        if self.publisher is not None:
            self.publisher.stop()
        for forwarder in self.forwarders.values():
            forwarder.stop()
        for node in self.nodes.values():
            node.stop()
        os.unlink(self.video)

    def stream(self):
        """Emite o vídeo de teste uma vez a partir de S1 e devolve a fonte."""
        source = StreamSource("S1", FLOW, self.video, self.forwarders["S1"].send,
                              fps=200, loop=False).start()
        source.join(5)
        self.assertFalse(source.is_alive())
        return source

    def received(self, nid):
        return self.receivers[nid].stats.received

    def test_two_clients_share_common_link_and_branches_close_independently(self):
        server, relay = self.forwarders["S1"], self.forwarders["A"]

        # 1) Dois clientes: uma cópia no troço S1-A, duas cópias a partir de A.
        source = self.stream()
        self.assertEqual(source.sent, FRAMES)
        self.assertTrue(wait_for(
            lambda: self.received("C1") == FRAMES and self.received("C2") == FRAMES))
        self.assertEqual(server.forwarded, FRAMES)       # uma cópia por pacote
        self.assertEqual(relay.received, FRAMES)
        self.assertEqual(relay.forwarded, 2 * FRAMES)    # replicação em A
        self.assertEqual((relay.dropped, self.receivers["C1"].stats.lost,
                          self.receivers["C2"].stats.lost), (0, 0, 0))

        # 2) C1 sai: C2 continua a receber, C1 deixa de receber.
        self.assertTrue(self.tables["C1"].deactivate(self.nodes["C1"], "S1", FLOW))
        self.assertTrue(wait_for(
            lambda: self.tables["A"].active_destinations("S1", FLOW) == ("C2",)))
        self.assertEqual(self.tables["S1"].active_destinations("S1", FLOW), ("A",))
        self.stream()
        self.assertTrue(wait_for(lambda: self.received("C2") == 2 * FRAMES))
        self.assertEqual(self.received("C1"), FRAMES)
        self.assertEqual(self.forwarders["C1"].received, FRAMES)
        self.assertEqual(relay.forwarded, 3 * FRAMES)

        # 3) C2 sai: a rota fica sem destinos e S1 não injeta tráfego.
        self.assertTrue(self.tables["C2"].deactivate(self.nodes["C2"], "S1", FLOW))
        self.assertTrue(wait_for(
            lambda: self.tables["S1"].active_destinations("S1", FLOW) == ()))
        self.assertEqual(self.tables["S1"].get("S1", FLOW).state, INACTIVE)
        source = self.stream()
        self.assertEqual(source.sent, 0)
        time.sleep(0.2)
        self.assertEqual(server.forwarded, 2 * FRAMES)
        self.assertEqual(relay.received, 2 * FRAMES)


if __name__ == "__main__":
    unittest.main()
