"""O ficheiro de estado de um nó: conteúdo, eventos relevantes e escrita atómica."""
import json
import math
import os
import tempfile
import time
import unittest
from unittest import mock

from ott import status as status_mod
from ott.node import OverlayNode
from ott.routing import ACTIVE, INACTIVE, AnnouncementPublisher, RoutingTable, RouteEntry
from ott.status import StatusWriter
from ott.streaming.client import StreamStats

BASE = 47940


def wait_for(cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class FakeNode:
    id = "N"
    port = 5000
    bootstrap_service = None

    def __init__(self):
        self.up, self.down = [], []

    def on_neighbor_up(self, fn):
        self.up.append(fn)

    def on_neighbor_down(self, fn):
        self.down.append(fn)

    def rtts(self):
        return {"A": 0.0012}

    def neighbor_ids(self):
        return ["A", "B"]


class FakeReceiver:
    def __init__(self):
        self.stats = StreamStats()


def writer(table=None, receivers=None, active=(), **kw):
    path = os.path.join(tempfile.mkdtemp(prefix="ott-status-"), "N.json")
    return StatusWriter(path, FakeNode(), table or RoutingTable(), receivers=receivers or {},
                        subscriptions=lambda: (set(), set(active)), **kw)


class RouteEventsTest(unittest.TestCase):
    def kinds(self, w):
        return [e["kind"] for e in w.events]

    def test_snapshot_is_json_safe_and_has_the_routing_table(self):
        table = RoutingTable()
        table.put(RouteEntry(flow="f", origin="S", metric=math.inf, upstream=None, state=INACTIVE))
        table.put(RouteEntry(flow="g", origin="S", metric=2, upstream="A", state=ACTIVE, destinations={"B"}, delay=0.004))
        w = writer(table)
        snap = json.loads(json.dumps(w.snapshot(), allow_nan=False))   # sem inf nem NaN
        routes = {r["flow"]: r for r in snap["routes"]}
        self.assertIsNone(routes["f"]["metric"])
        self.assertEqual((routes["g"]["upstream"], routes["g"]["metric"], routes["g"]["delay_ms"]), ("A", 2, 4.0))
        self.assertEqual(routes["g"]["destinations"], ["B"])
        self.assertEqual(snap["neighbors"], [{"id": "A", "rtt_ms": 1.2}, {"id": "B", "rtt_ms": None}])

    def test_route_changes_become_events(self):
        table = RoutingTable()
        w = writer(table)
        w.snapshot()   # a primeira fotografia só serve de referência
        table.put(RouteEntry(flow="f", origin="S", metric=2, upstream="A", state=INACTIVE))
        w.snapshot()
        r = table.get("S", "f")
        r.upstream, r.state = "B", ACTIVE
        r.destinations = {"C"}
        w.snapshot()
        r.upstream, r.metric = None, math.inf
        w.snapshot()
        r.upstream = "B"
        w.snapshot()
        r.withdrawn_at = 1.0
        w.snapshot()
        table.remove("S", "f")
        w.snapshot()
        self.assertEqual(self.kinds(w), ["rota_nova", "montante", "rota_estado", "ramo", "rota_perdida", "rota_recuperada",
                                         "withdraw", "rota_esquecida"])
        self.assertIn("A → B", w.events[1]["text"])

    def test_neighbor_events_come_from_the_node_callbacks(self):
        w = writer()
        w.node.on_neighbor_up(w._on_up)
        w._on_up(w.node, "A")
        w._on_down(w.node, "A")
        self.assertEqual(self.kinds(w), ["vizinho_up", "vizinho_down"])

    def test_flow_stall_and_recovery(self):
        rx = FakeReceiver()
        with mock.patch.object(status_mod, "STALL_AFTER", 0.3):
            w = writer(receivers={("S", "f"): rx}, active=[("S", "f")])
            rx.stats.received = 10
            w.snapshot()
            rx.stats.received = 20
            w.snapshot()                       # chegaram imagens: o fluxo está vivo
            time.sleep(0.5)
            snap = w.snapshot()                # nada de novo há mais de STALL_AFTER
            self.assertTrue(snap["flows"][0]["stalled"])
            rx.stats.received = 30
            snap = w.snapshot()
            self.assertFalse(snap["flows"][0]["stalled"])
        self.assertEqual([e["kind"] for e in w.events], ["fluxo_parou", "fluxo_voltou"])

    def test_a_flow_that_never_started_is_not_reported_as_stalled(self):
        rx = FakeReceiver()
        with mock.patch.object(status_mod, "STALL_AFTER", 0.1):
            w = writer(receivers={("S", "f"): rx}, active=[("S", "f")])
            w.snapshot()
            time.sleep(0.3)
            w.snapshot()
        self.assertEqual(self.kinds(w), [])

    def test_write_is_atomic_and_stop_leaves_an_offline_state(self):
        w = writer(interval=0.05)
        w.start()
        self.assertTrue(wait_for(lambda: os.path.exists(w.path)))
        with open(w.path) as f:
            self.assertTrue(json.load(f)["online"])
        w.stop()
        with open(w.path) as f:
            final = json.load(f)
        self.assertFalse(final["online"])
        self.assertEqual(final["events"][-1]["kind"], "paragem")
        self.assertEqual([f for f in os.listdir(os.path.dirname(w.path)) if ".tmp" in f], [])


class RealNodesTest(unittest.TestCase):
    def test_status_of_a_real_chain(self):
        nodes, tables = {}, {}
        for i, name in enumerate("SA"):
            nodes[name] = OverlayNode(name, "127.0.0.1", BASE + i, keepalive=0.2, dead_after=1.0, retry_interval=0.2)
            tables[name] = RoutingTable().attach(nodes[name])
            nodes[name].start()
        nodes["A"].add_targets({("127.0.0.1", BASE): "S"})
        publisher = AnnouncementPublisher(nodes["S"], ["f"], 0.3, tables["S"])
        w = StatusWriter(os.path.join(tempfile.mkdtemp(prefix="ott-status-"), "A.json"), nodes["A"], tables["A"],
                         interval=0.1)
        try:
            publisher.start()
            w.start()
            self.assertTrue(wait_for(lambda: tables["A"].get("S", "f") is not None))
            time.sleep(0.4)
            with open(w.path) as f:
                snap = json.load(f)
            self.assertEqual(snap["node"], "A")
            self.assertEqual([n["id"] for n in snap["neighbors"]], ["S"])
            self.assertEqual((snap["routes"][0]["upstream"], snap["routes"][0]["metric"]), ("S", 1))
            self.assertIn("vizinho_up", [e["kind"] for e in snap["events"]])
            nodes["S"].stop()                                   # o servidor cai: o A regista-o
            self.assertTrue(wait_for(lambda: "vizinho_down" in [e["kind"] for e in w.events]))
        finally:
            publisher.stop()
            w.stop()
            for n in nodes.values():
                n.stop()
            for t in tables.values():
                t.close()


if __name__ == "__main__":
    unittest.main()
