import time
import unittest

from ott.bootstrapper import BootstrapService
from ott.node import OverlayNode

BASE = 47100
TOPO = {"nodes": {
    "S1": {"host": "127.0.0.1", "port": BASE, "neighbors": ["A"]},
    "A": {"host": "127.0.0.1", "port": BASE + 1, "neighbors": ["B", "C"]},
    "B": {"host": "127.0.0.1", "port": BASE + 2, "neighbors": ["C"]},
    "C": {"host": "127.0.0.1", "port": BASE + 3, "neighbors": []},
}}


def wait_for(cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def make_node(nid, service=None):
    port = TOPO["nodes"][nid]["port"]
    return OverlayNode(nid, "127.0.0.1", port, bootstrap_service=service,
                       keepalive=0.2, dead_after=1.0, retry_interval=0.2)


class OverlayTest(unittest.TestCase):
    def setUp(self):
        self.nodes = {}

    def tearDown(self):
        for n in self.nodes.values():
            n.stop()

    def test_bootstrap_builds_overlay_and_recovers(self):
        service = BootstrapService(TOPO)
        server = self.nodes["S1"] = make_node("S1", service)
        server.start()
        server.add_targets({service.address_of(n["id"]): n["id"]
                            for n in service.neighbors_of("S1")})
        for nid in ("A", "B", "C"):
            node = self.nodes[nid] = make_node(nid)
            node.start()
            self.assertTrue(node.bootstrap("127.0.0.1", BASE, attempts=3))

        expected = {"S1": ["A"], "A": ["B", "C", "S1"],
                    "B": ["A", "C"], "C": ["A", "B"]}
        for nid, nbs in expected.items():
            self.assertTrue(
                wait_for(lambda: self.nodes[nid].neighbor_ids() == nbs),
                f"{nid}: {self.nodes[nid].neighbor_ids()} != {nbs}")

        # C abandona: os vizinhos detetam-no; ao regressar, volta a ligar-se
        self.nodes["C"].stop()
        self.assertTrue(wait_for(lambda: self.nodes["A"].neighbor_ids() == ["B", "S1"]))
        c = self.nodes["C"] = make_node("C")
        c.start()
        self.assertTrue(c.bootstrap("127.0.0.1", BASE, attempts=3))
        self.assertTrue(wait_for(lambda: c.neighbor_ids() == ["A", "B"]))

    def test_unknown_node_refused(self):
        service = BootstrapService(TOPO)
        server = self.nodes["S1"] = make_node("S1", service)
        server.start()
        x = OverlayNode("X", "127.0.0.1", BASE + 9)
        self.nodes["X"] = x
        self.assertFalse(x.bootstrap("127.0.0.1", BASE, attempts=1))

    def test_manual_neighbors_and_handler(self):
        a = self.nodes["A"] = make_node("A")
        b = self.nodes["B"] = make_node("B")
        got = []
        b.register_handler("ANNOUNCE", lambda n, nb, m: got.append(m["data"]))
        a.start()
        b.start()
        a.add_targets({("127.0.0.1", BASE + 2): None})
        self.assertTrue(wait_for(lambda: a.neighbor_ids() == ["B"]))
        self.assertTrue(wait_for(lambda: b.neighbor_ids() == ["A"]))
        a.send("B", {"type": "ANNOUNCE", "src": "A", "data": {"metric": 1}})
        self.assertTrue(wait_for(lambda: got == [{"metric": 1}]))


if __name__ == "__main__":
    unittest.main()
