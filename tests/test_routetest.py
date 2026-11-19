# Etapa 3 (ROUTE_TEST) e 2 servidores x 2 fluxos distintos, com nós reais em localhost.
# PYTHONPATH=src python3 -m unittest tests.test_routetest -v
import os
import threading
import unittest

from ott.forwarding import OverlayForwarder
from ott.protocol import MsgType, make_msg
from ott.routetest import RouteTester
from ott.routing import AnnouncementPublisher, RoutingTable
from ott.streaming.client import StreamReceiver
from ott.streaming.server import StreamSource

from tests.test_integration import loopback_aliases_available, make_node, wait_for
from tests.test_streaming import write_mjpeg

BASE = 47960
FLOW = "filme_a"


class FakeNode:
    id = "B"

    def __init__(self):
        self.sent = []
        self.handlers = {}

    def register_handler(self, kind, fn):
        self.handlers[kind] = fn

    def send(self, dest, msg):
        self.sent.append((dest, msg))
        return True


def route_test_msg(path, seq=1, src="A", server="S1", flow=FLOW):
    return make_msg(MsgType.ROUTE_TEST, src, server_id=server, flow=flow, seq=seq, path=path)


class RouteTesterUnitTest(unittest.TestCase):
    """Regras de aceitação e reencaminhamento, sem rede."""

    def setUp(self):
        self.node = FakeNode()
        self.table = RoutingTable()
        self.tester = RouteTester(self.node, self.table).attach()
        from ott.routing import RouteEntry
        self.route = self.table.put(RouteEntry(flow=FLOW, origin="S1", metric=1, upstream="A"))
        self.route.destinations.add("C1")
        self.route.local_subscribers.add("B")
        self.table._refresh_state(self.route, "B")

    def test_handler_is_registered(self):
        self.assertIn(MsgType.ROUTE_TEST, self.node.handlers)

    def test_accepts_from_upstream_appends_self_and_forwards_to_destinations(self):
        msg = route_test_msg(["S1", "A"])
        self.node.handlers[MsgType.ROUTE_TEST](self.node, "A", msg)
        (dest, out), = self.node.sent
        self.assertEqual(dest, "C1")
        self.assertEqual(out["src"], "B")
        self.assertEqual(out["data"]["path"], ["S1", "A", "B"])
        self.assertEqual(msg["data"]["path"], ["S1", "A"])   # a original não é alterada
        result = self.tester.last("S1", FLOW)
        self.assertEqual((result.path, result.hops, result.delivered, result.forwarded_to),
                         (("S1", "A", "B"), 2, True, ("C1",)))

    def test_message_not_from_upstream_is_dropped(self):
        self.node.handlers[MsgType.ROUTE_TEST](self.node, "C1", route_test_msg(["S1", "X"]))
        self.assertEqual((self.node.sent, self.tester.dropped, self.tester.last("S1", FLOW)),
                         ([], 1, None))

    def test_unknown_route_is_dropped(self):
        self.node.handlers[MsgType.ROUTE_TEST](
            self.node, "A", route_test_msg(["S1", "A"], flow="outro"))
        self.assertEqual((self.node.sent, self.tester.dropped), ([], 1))

    def test_loop_is_dropped(self):
        self.node.handlers[MsgType.ROUTE_TEST](self.node, "A", route_test_msg(["S1", "B", "A"]))
        self.assertEqual((self.node.sent, self.tester.dropped, len(self.tester.results)),
                         ([], 1, 0))

    def test_does_not_send_back_to_the_sender(self):
        self.route.destinations.add("A")   # destino igual ao emissor: nunca devolve
        self.node.handlers[MsgType.ROUTE_TEST](self.node, "A", route_test_msg(["S1", "A"]))
        self.assertEqual([d for d, _ in self.node.sent], ["C1"])

    def test_leaf_without_destinations_ends_the_branch(self):
        self.route.destinations.clear()
        self.node.handlers[MsgType.ROUTE_TEST](self.node, "A", route_test_msg(["S1", "A"]))
        self.assertEqual(self.node.sent, [])
        self.assertTrue(self.tester.last("S1", FLOW).delivered)

    def test_send_test_only_for_flows_published_here(self):
        self.assertIsNone(self.tester.send_test(FLOW))   # B não é a origem
        self.assertIsNone(self.tester.send_test("desconhecido"))

    def test_server_numbers_tests_and_sends_to_active_destinations(self):
        node = FakeNode()
        node.id = "S1"
        table = RoutingTable()
        tester = RouteTester(node, table).attach()
        self.assertIsNone(tester.send_test(FLOW))
        route = table.publish_local("S1", FLOW, 1)
        self.assertEqual(tester.send_test(FLOW), 1)       # sem destinos: regista, não envia
        self.assertEqual(node.sent, [])
        route.destinations.add("A")
        table._refresh_state(route, "S1")
        self.assertEqual(tester.send_test(FLOW), 2)
        (dest, out), = node.sent
        self.assertEqual((dest, out["data"]["seq"], out["data"]["path"]), ("A", 2, ["S1"]))

    def test_periodic_start_and_stop(self):
        node = FakeNode()
        node.id = "S1"
        table = RoutingTable()
        tester = RouteTester(node, table).attach()
        route = table.publish_local("S1", FLOW, 1)
        route.destinations.add("A")
        table._refresh_state(route, "S1")
        tester.start([FLOW], 0.05)
        self.assertTrue(wait_for(lambda: len(node.sent) >= 3, 3))
        tester.stop()
        count = len(node.sent)
        threading.Event().wait(0.2)
        self.assertEqual(len(node.sent), count)
        with self.assertRaises(ValueError):
            tester.start([FLOW], 0)


class RouteTestIntegrationTest(unittest.TestCase):
    """S1 - A - {C1, C2}: o teste percorre só os ramos ativos e acumula o caminho."""

    def setUp(self):
        self.nodes, self.tables, self.testers = {}, {}, {}
        for offset, nid in enumerate(("S1", "A", "C1", "C2")):
            node = self.nodes[nid] = make_node(nid, BASE + offset)
            self.tables[nid] = RoutingTable().attach(node)
            self.testers[nid] = RouteTester(node, self.tables[nid]).attach()
            node.start()
        self.nodes["A"].add_targets({("127.0.0.1", BASE): "S1",
                                     ("127.0.0.1", BASE + 2): "C1",
                                     ("127.0.0.1", BASE + 3): "C2"})
        self.assertTrue(wait_for(lambda: self.nodes["A"].neighbor_ids() == ["C1", "C2", "S1"]))
        self.publisher = AnnouncementPublisher(
            self.nodes["S1"], [FLOW], interval=0.3, routing_table=self.tables["S1"]).start()
        for nid in ("C1", "C2"):
            self.assertTrue(wait_for(lambda: self.tables[nid].get("S1", FLOW) is not None))

    def tearDown(self):
        self.publisher.stop()
        for node in self.nodes.values():
            node.stop()

    def activate(self, *clients):
        for nid in clients:
            self.assertTrue(self.tables[nid].activate(self.nodes[nid], "S1", FLOW))
        self.assertTrue(wait_for(lambda: self.tables["S1"].active_destinations("S1", FLOW) == ("A",)))
        self.assertTrue(wait_for(
            lambda: self.tables["A"].active_destinations("S1", FLOW) == tuple(sorted(clients))))

    def test_follows_active_branches_and_accumulates_the_path(self):
        self.activate("C1", "C2")
        self.assertEqual(self.testers["S1"].send_test(FLOW), 1)
        self.assertTrue(wait_for(lambda: all(
            self.testers[n].last("S1", FLOW) for n in ("A", "C1", "C2"))))
        self.assertEqual(self.testers["A"].last("S1", FLOW).path, ("S1", "A"))
        for nid in ("C1", "C2"):
            result = self.testers[nid].last("S1", FLOW)
            self.assertEqual((result.path, result.hops, result.delivered, result.seq),
                             (("S1", "A", nid), 2, True, 1))
        self.assertEqual(self.testers["A"].last("S1", FLOW).forwarded_to, ("C1", "C2"))
        # cada nó recebeu exatamente um teste: sem duplicados no ramo comum
        self.assertEqual([len(self.testers[n].results) for n in ("A", "C1", "C2")], [1, 1, 1])

    def test_branch_closed_by_a_client_no_longer_receives_tests(self):
        self.activate("C1", "C2")
        self.assertTrue(self.tables["C1"].deactivate(self.nodes["C1"], "S1", FLOW))
        self.assertTrue(wait_for(
            lambda: self.tables["A"].active_destinations("S1", FLOW) == ("C2",)))
        self.testers["S1"].send_test(FLOW)
        self.assertTrue(wait_for(lambda: self.testers["C2"].last("S1", FLOW)))
        self.assertIsNone(self.testers["C1"].last("S1", FLOW))

    def test_without_clients_nothing_travels(self):
        self.assertEqual(self.testers["S1"].send_test(FLOW), 1)
        threading.Event().wait(0.3)
        self.assertEqual([len(self.testers[n].results) for n in ("A", "C1", "C2")], [0, 0, 0])


DATA_HOSTS = {"A": "127.0.0.1", "S1": "127.0.0.2", "S2": "127.0.0.3",
              "C1": "127.0.0.4", "C2": "127.0.0.5"}
DATA_PORT = 47990
FRAMES = 15
FLOWS = {"S1": ("filme_a", "filme_b"), "S2": ("filme_c", "filme_d")}


@unittest.skipUnless(loopback_aliases_available(*DATA_HOSTS.values()),
                     "requer endereços de loopback 127.0.0.2-5")
class TwoServersTwoFlowsTest(unittest.TestCase):
    """Requisito mínimo: 2 servidores, cada um com 2 fluxos distintos, em simultâneo.

    S1 e S2 ligam ao relay A; C1 subscreve os quatro fluxos e C2 só dois (um de
    cada servidor). Verifica-se a tabela de rotas, o ROUTE_TEST e o vídeo de cada
    fluxo, e que o troço comum A-C1 leva uma só cópia de cada pacote.
    """

    def setUp(self):
        ids = ("S1", "S2", "A", "C1", "C2")
        self.nodes, self.tables, self.forwarders, self.testers = {}, {}, {}, {}
        self.receivers, self.publishers, self.videos = {}, [], {}
        for offset, nid in enumerate(ids):
            host = DATA_HOSTS[nid]
            node = self.nodes[nid] = make_node(nid, BASE + 20 + offset, host)
            self.tables[nid] = RoutingTable().attach(node)
            self.testers[nid] = RouteTester(node, self.tables[nid]).attach()
            node.start()
            self.forwarders[nid] = OverlayForwarder(
                node, self.tables[nid], port=DATA_PORT, bind=host).start()
        self.nodes["A"].add_targets({
            (DATA_HOSTS[nid], BASE + 20 + offset): nid
            for offset, nid in enumerate(ids) if nid != "A"})
        self.assertTrue(wait_for(
            lambda: self.nodes["A"].neighbor_ids() == ["C1", "C2", "S1", "S2"]))
        for server, flows in FLOWS.items():
            self.publishers.append(AnnouncementPublisher(
                self.nodes[server], flows, interval=0.3,
                routing_table=self.tables[server]).start())
            for flow in flows:
                self.videos[(server, flow)] = write_mjpeg(
                    [b"%s-%s-%d" % (server.encode(), flow.encode(), i) for i in range(FRAMES)])
        self.wanted = {"C1": [(s, f) for s, fl in FLOWS.items() for f in fl],
                       "C2": [("S1", "filme_a"), ("S2", "filme_c")]}
        for nid, keys in self.wanted.items():
            for key in keys:
                self.assertTrue(wait_for(lambda: self.tables[nid].get(*key) is not None))
                receiver = self.receivers[(nid,) + key] = StreamReceiver(*key, listen=False)
                self.forwarders[nid].set_local_receiver(*key, receiver)
                self.assertTrue(self.tables[nid].activate(self.nodes[nid], *key))
        for server, flows in FLOWS.items():
            self.assertTrue(wait_for(
                lambda: all(self.tables[server].active_destinations(server, f) == ("A",)
                            for f in flows)))
        # os ramos de A até aos clientes também têm de estar abertos antes de gerar tráfego
        expected = {}
        for nid, keys in self.wanted.items():
            for key in keys:
                expected.setdefault(key, set()).add(nid)
        self.assertTrue(wait_for(lambda: all(
            set(self.tables["A"].active_destinations(*key)) == clients
            for key, clients in expected.items())))

    def tearDown(self):
        for publisher in self.publishers:
            publisher.stop()
        for forwarder in self.forwarders.values():
            forwarder.stop()
        for node in self.nodes.values():
            node.stop()
        for path in self.videos.values():
            os.unlink(path)

    def test_routes_are_kept_per_server_and_flow(self):
        for nid in ("A", "C1", "C2"):
            keys = {k for k in self.tables[nid].routes}
            self.assertEqual(keys, {(s, f) for s, fl in FLOWS.items() for f in fl}, nid)
        route = self.tables["A"].get("S2", "filme_d")
        self.assertEqual((route.upstream, route.destinations), ("S2", {"C1"}))
        route = self.tables["A"].get("S1", "filme_a")
        self.assertEqual((route.upstream, route.destinations), ("S1", {"C1", "C2"}))

    def test_route_test_reaches_only_the_subscribers_of_each_flow(self):
        for server, flows in FLOWS.items():
            for flow in flows:
                self.assertEqual(self.testers[server].send_test(flow), 1)
        self.assertTrue(wait_for(lambda: all(
            self.testers["C1"].last(s, f) for s, fl in FLOWS.items() for f in fl)))
        self.assertTrue(wait_for(lambda: all(
            self.testers["C2"].last(s, f) for s, f in self.wanted["C2"])))
        for (server, flow) in self.wanted["C1"]:
            self.assertEqual(self.testers["C1"].last(server, flow).path,
                             (server, "A", "C1"), (server, flow))
        self.assertIsNone(self.testers["C2"].last("S1", "filme_b"))
        self.assertIsNone(self.testers["C2"].last("S2", "filme_d"))

    def test_video_of_every_flow_arrives_with_one_copy_per_link(self):
        sources = [StreamSource(server, flow, self.videos[(server, flow)],
                                self.forwarders[server].send, fps=200, loop=False).start()
                   for server, flows in FLOWS.items() for flow in flows]
        for source in sources:
            source.join(5)
            self.assertEqual(source.sent, FRAMES)
        self.assertTrue(wait_for(lambda: all(
            r.stats.received == FRAMES for r in self.receivers.values())),
            {k: r.stats.received for k, r in self.receivers.items()})
        self.assertEqual(len(self.receivers), 6)
        self.assertEqual([r.stats.lost for r in self.receivers.values()], [0] * 6)
        relay = self.forwarders["A"]
        self.assertEqual(relay.received, 4 * FRAMES)                  # 4 fluxos, uma cópia cada
        self.assertEqual(relay.forwarded, 6 * FRAMES)                 # 4 para C1 + 2 para C2
        self.assertEqual((relay.dropped, relay.rpf_dropped), (0, 0))
        self.assertEqual([self.forwarders[s].forwarded for s in ("S1", "S2")],
                         [2 * FRAMES, 2 * FRAMES])


if __name__ == "__main__":
    unittest.main()
