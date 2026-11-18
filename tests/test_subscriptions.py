# Subscrições sem servidor fixo (D38): escolha do servidor, failover e histerese.
# PYTHONPATH=src python3 -m unittest tests.test_subscriptions -v
import os
import unittest

from ott.forwarding import OverlayForwarder
from ott.protocol import MsgType, make_msg
from ott.routing import AnnouncementPublisher, RoutingTable
from ott.streaming.client import StreamReceiver
from ott.streaming.server import StreamSource
from ott.subscriptions import ANY, SubscriptionManager, parse_subscription

from tests import test_routing
from tests.test_integration import loopback_aliases_available, make_node, wait_for
from tests.test_streaming import write_mjpeg

FLOW = "filme_c"


class FakeForwarder:
    def __init__(self):
        self.local = {}

    def set_local_receiver(self, origin, flow, receiver):
        if receiver is None:
            self.local.pop((origin, flow), None)
        else:
            self.local[(origin, flow)] = receiver


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def announce(src, server, epoch, metric, delay, flow=FLOW):
    return make_msg(MsgType.ANNOUNCE, src, server_id=server, flow=flow, epoch=epoch, metric=metric, delay=delay)


def withdraw(src, server, epoch, flow=FLOW):
    return make_msg(MsgType.WITHDRAW, src, server_id=server, flow=flow, epoch=epoch)


class ParseTest(unittest.TestCase):
    def test_server_and_flow_or_only_flow(self):
        self.assertEqual(parse_subscription("S1:filme_a"), ("S1", "filme_a"))
        self.assertEqual(parse_subscription("filme_c"), (None, "filme_c"))

    def test_invalid(self):
        for bad in ("", ":filme", "S1:", "S1:a:b", ANY, f"{ANY}:filme", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_subscription(bad)


class ManagerTest(unittest.TestCase):
    """RoutingTable real com nó falso: N1 leva ao S1 e N2 ao S2."""

    def setUp(self):
        self.table = RoutingTable()
        self.node = test_routing.RouteSelectionTest.Node()
        self.node.rtts = {"N1": 0.002, "N2": 0.002}
        self.fwd = FakeForwarder()
        self.clock = Clock()
        self.mgr = SubscriptionManager(self.node, self.table, self.fwd, clock=self.clock)
        self.rx = StreamReceiver(None, FLOW, listen=False)

    def learn(self, src, server, epoch=1, metric=1, delay=0.001):
        self.table.handle_announce(self.node, src, announce(src, server, epoch, metric, delay))

    def subscribers(self, server):
        route = self.table.get(server, FLOW)
        return set(route.local_subscribers) if route else set()

    def test_waits_for_a_server_then_picks_the_closest(self):
        sub = self.mgr.add(None, FLOW, self.rx)
        self.assertEqual(self.mgr.tick(), [])
        self.assertEqual(self.mgr.state(), ({(ANY, FLOW)}, set()))
        self.learn("N1", "S1", delay=0.004)
        self.learn("N2", "S2", delay=0.001)
        self.assertEqual(self.mgr.tick(), [(FLOW, None, "S2", "primeira escolha")])
        self.assertEqual((sub.current, sub.active, self.rx.origin, self.rx.subscription), ("S2", True, "S2", FLOW))
        self.assertEqual(self.fwd.local, {("S2", FLOW): self.rx})
        self.assertEqual((self.subscribers("S2"), self.subscribers("S1")), ({self.node.id}, set()))
        self.assertEqual(self.mgr.state(), (set(), {("S2", FLOW)}))
        self.assertEqual(self.mgr.receivers(), {("S2", FLOW): self.rx})

    def test_failover_when_the_chosen_server_loses_its_path(self):
        self.learn("N1", "S1", delay=0.001)
        self.learn("N2", "S2", delay=0.004)
        sub = self.mgr.add(None, FLOW, self.rx)
        self.mgr.tick()
        self.assertEqual(sub.current, "S1")
        self.table.handle_withdraw(self.node, "N1", withdraw("N1", "S1", 1))
        self.assertEqual(self.mgr.tick(), [(FLOW, "S1", "S2", "S1 sem caminho")])
        self.assertEqual((sub.current, sub.switches, self.rx.origin), ("S2", 1, "S2"))
        self.assertEqual(self.fwd.local, {("S2", FLOW): self.rx})
        self.assertEqual((self.subscribers("S1"), self.subscribers("S2")), (set(), {self.node.id}))
        self.assertEqual(self.mgr.tick(), [])       # estável

    def test_failover_ignores_min_hold(self):
        self.mgr.min_hold = 1000
        self.learn("N1", "S1", delay=0.001)
        self.learn("N2", "S2", delay=0.004)
        sub = self.mgr.add(None, FLOW, self.rx)
        self.mgr.tick()
        self.table.handle_withdraw(self.node, "N1", withdraw("N1", "S1", 1))
        self.mgr.tick()
        self.assertEqual(sub.current, "S2")

    def test_without_alternative_keeps_the_last_server_and_resumes(self):
        self.learn("N1", "S1")
        sub = self.mgr.add(None, FLOW, self.rx)
        self.mgr.tick()
        self.table.handle_withdraw(self.node, "N1", withdraw("N1", "S1", 1))
        self.assertEqual(self.mgr.tick(), [])
        self.assertEqual((sub.current, sub.active), ("S1", True))
        self.assertEqual(self.subscribers("S1"), {self.node.id})   # pedido pendente na tabela
        self.learn("N1", "S1", epoch=2)                            # caminho reaprendido
        self.assertTrue(self.table.has_local_subscription("S1", FLOW))
        self.assertEqual(self.mgr.tick(), [])

    def test_better_server_only_above_hysteresis_and_after_min_hold(self):
        self.learn("N2", "S2", delay=0.010)                        # S2 a 11 ms
        sub = self.mgr.add(None, FLOW, self.rx)
        self.mgr.tick()                                             # escolhido em t = 0
        self.assertEqual(sub.current, "S2")
        self.clock.now = 10
        self.learn("N1", "S1", delay=0.0095)                       # S1 a 10,5 ms: abaixo da histerese
        self.assertEqual(self.mgr.tick(), [])
        self.mgr.min_hold = 30                                      # troca anterior há 10 s < 30 s
        self.learn("N1", "S1", epoch=2, delay=0.001)               # S1 a 2 ms: muito melhor
        self.assertEqual(self.mgr.tick(), [])
        self.clock.now = 31
        self.assertEqual(self.mgr.tick(), [(FLOW, "S2", "S1", "mais perto")])
        self.assertEqual((self.subscribers("S2"), self.subscribers("S1")), (set(), {self.node.id}))

    def test_fixed_subscription_waits_for_its_route(self):
        rx = StreamReceiver("S1", FLOW, listen=False)
        sub = self.mgr.add("S1", FLOW, rx)
        self.assertEqual(self.fwd.local, {("S1", FLOW): rx})
        self.learn("N2", "S2")
        self.mgr.tick()
        self.assertFalse(sub.active)                                # o S2 não serve
        self.learn("N1", "S1")
        self.mgr.tick()
        self.assertTrue(sub.active)
        self.assertEqual(self.subscribers("S1"), {self.node.id})
        self.assertEqual(self.subscribers("S2"), set())

    def test_overlapping_subscriptions_are_rejected(self):
        self.mgr.add("S1", FLOW, StreamReceiver("S1", FLOW, listen=False))
        for server in (None, "S1"):
            with self.subTest(server=server), self.assertRaises(ValueError):
                self.mgr.add(server, FLOW, StreamReceiver(server, FLOW, listen=False))
        self.mgr.add("S2", FLOW, StreamReceiver("S2", FLOW, listen=False))   # outro servidor fixo serve

    def test_stop_deactivates(self):
        self.learn("N1", "S1")
        self.mgr.add(None, FLOW, self.rx)
        self.mgr.tick()
        self.mgr.stop()
        self.assertEqual(self.subscribers("S1"), set())
        self.assertEqual(self.mgr.state(), ({("S1", FLOW)}, set()))


HOSTS = {"S1": "127.0.0.21", "S2": "127.0.0.22", "C": "127.0.0.23"}
BASE = 47840
DATA_PORT = 47850


@unittest.skipUnless(loopback_aliases_available(*HOSTS.values()), "requer endereços de loopback 127.0.0.21-23")
class FailoverIntegrationTest(unittest.TestCase):
    """S1 e S2 publicam o mesmo fluxo; o cliente C (vizinho dos dois) subscreve só o fluxo."""

    def setUp(self):
        self.video = write_mjpeg([b"frame%d" % i for i in range(20)])
        self.nodes, self.tables, self.fwd, self.pubs, self.sources = {}, {}, {}, {}, {}
        for i, nid in enumerate(HOSTS):
            node = self.nodes[nid] = make_node(nid, BASE + i, HOSTS[nid])
            self.tables[nid] = RoutingTable().attach(node)
            node.start()
            self.fwd[nid] = OverlayForwarder(node, self.tables[nid], port=DATA_PORT, bind=HOSTS[nid]).start()
        self.nodes["C"].add_targets({(HOSTS["S1"], BASE): "S1", (HOSTS["S2"], BASE + 1): "S2"})
        self.assertTrue(wait_for(lambda: self.nodes["C"].neighbor_ids() == ["S1", "S2"]))
        for nid in ("S1", "S2"):
            self.pubs[nid] = AnnouncementPublisher(self.nodes[nid], [FLOW], interval=0.3,
                                                   routing_table=self.tables[nid]).start()
            self.sources[nid] = StreamSource(nid, FLOW, self.video, self.fwd[nid].send, fps=50, loop=True).start()
        self.rx = StreamReceiver(None, FLOW, listen=False)
        self.mgr = SubscriptionManager(self.nodes["C"], self.tables["C"], self.fwd["C"], poll=0.05).start()
        self.mgr.add(None, FLOW, self.rx)
        self.stopped = set()

    def stop_server(self, nid):
        self.stopped.add(nid)
        self.sources[nid].stop()
        self.pubs[nid].stop()
        self.fwd[nid].stop()
        self.nodes[nid].stop()

    def tearDown(self):
        self.mgr.stop()
        for nid in ("S1", "S2"):
            if nid not in self.stopped:
                self.stop_server(nid)
        self.fwd["C"].stop()
        self.nodes["C"].stop()
        os.unlink(self.video)

    def test_client_moves_to_the_other_server_when_its_server_stops(self):
        sub = self.mgr.subs[0]
        self.assertTrue(wait_for(lambda: sub.active and self.rx.stats.received >= 20))
        first = sub.current
        other = "S2" if first == "S1" else "S1"
        self.assertEqual(self.tables[first].active_destinations(first, FLOW), ("C",))
        self.assertEqual(self.tables[other].active_destinations(other, FLOW), ())   # só um servidor emite
        self.stop_server(first)
        self.assertTrue(wait_for(lambda: sub.current == other))
        before = self.rx.stats.received
        self.assertTrue(wait_for(lambda: self.rx.stats.received >= before + 20))
        self.assertEqual(self.tables[other].active_destinations(other, FLOW), ("C",))
        self.assertGreaterEqual(self.rx.stats.restarts, 1)       # outro servidor: outro SSRC
        self.assertEqual(sub.switches, 1)


if __name__ == "__main__":
    unittest.main()
