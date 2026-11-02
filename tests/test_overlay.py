import socket
import time
import unittest
from unittest import mock

from ott import node as node_mod
from ott import protocol as proto
from ott.bootstrapper import BootstrapService
from ott.node import OverlayNode
from ott.protocol import MsgType, make_msg

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
        announce = {"type": "ANNOUNCE", "src": "A", "data": {
            "server_id": "S1", "flow": "filme_a", "epoch": 1, "metric": 1}}
        a.send("B", announce)
        self.assertTrue(wait_for(lambda: got == [announce["data"]]))


    def test_neighbor_events_and_rtt(self):
        a = self.nodes["A"] = make_node("A")
        b = self.nodes["B"] = make_node("B")
        events = []
        a.on_neighbor_up(lambda n, nid: events.append(("up", nid)))
        a.on_neighbor_down(lambda n, nid: events.append(("down", nid)))
        a.start()
        b.start()
        a.add_targets({("127.0.0.1", BASE + 2): None})
        self.assertTrue(wait_for(lambda: events == [("up", "B")]))
        # RTT medido pelo PING/PONG (localhost: muito abaixo de 1 s)
        self.assertTrue(wait_for(lambda: a.rtt("B") is not None))
        self.assertTrue(0 <= a.rtt("B") < 1.0)
        self.assertEqual(list(a.rtts()), ["B"])
        self.assertIn("B(", a.describe_neighbors())
        self.assertIsNone(a.rtt("Z"))
        b.stop()
        self.assertTrue(wait_for(lambda: events == [("up", "B"), ("down", "B")]))
        self.assertIsNone(a.rtt("B"))

    def test_duplicate_connection_fires_single_up(self):
        a = self.nodes["A"] = make_node("A")
        b = self.nodes["B"] = make_node("B")
        ups = []
        a.on_neighbor_up(lambda n, nid: ups.append(nid))
        b.on_neighbor_up(lambda n, nid: ups.append(nid))
        a.start()
        b.start()
        a.add_targets({("127.0.0.1", BASE + 2): None})
        b.add_targets({("127.0.0.1", BASE + 1): None})  # ligam-se os dois em simultâneo
        self.assertTrue(wait_for(lambda: a.neighbor_ids() == ["B"] and b.neighbor_ids() == ["A"]))
        time.sleep(1.0)
        self.assertEqual(sorted(ups), ["A", "B"])  # um up por nó, sem repetições
        self.assertEqual(a.neighbor_ids(), ["B"])
        self.assertEqual(b.neighbor_ids(), ["A"])

    def test_silent_neighbor_detected_by_timeout(self):
        """Vizinho que deixa de responder (processo congelado) cai por timeout."""
        a = self.nodes["A"] = make_node("A")
        downs = []
        a.on_neighbor_down(lambda n, nid: downs.append(nid))
        a.start()
        sock = socket.create_connection(("127.0.0.1", BASE + 1), 3)
        try:
            proto.send_msg(sock, make_msg(MsgType.HELLO, "X"))
            self.assertEqual(proto.recv_msg(sock)["type"], MsgType.HELLO)
            self.assertTrue(wait_for(lambda: a.neighbor_ids() == ["X"]))
            t0 = time.time()
            # X fica calado (não responde aos PING) com a ligação aberta
            self.assertTrue(wait_for(lambda: downs == ["X"], timeout=5))
            self.assertLess(time.time() - t0, 3.0)  # dead_after=1.0 + folga do ciclo
        finally:
            sock.close()

    def test_invalid_message_closes_connection(self):
        a = self.nodes["A"] = make_node("A")
        a.start()
        sock = socket.create_connection(("127.0.0.1", BASE + 1), 3)
        try:
            proto.send_msg(sock, make_msg(MsgType.HELLO, "X"))
            proto.recv_msg(sock)
            self.assertTrue(wait_for(lambda: a.neighbor_ids() == ["X"]))
            proto_raw = b'{"type":"PING","src":"X","data":{}}'  # PING sem ts
            import struct
            sock.sendall(struct.pack("!I", len(proto_raw)) + proto_raw)
            self.assertTrue(wait_for(lambda: a.neighbor_ids() == []))
        finally:
            sock.close()

    def test_substitute_requested_when_neighbor_lost(self):
        topo = {"nodes": {
            "S1": {"host": "127.0.0.1", "port": BASE + 10, "neighbors": ["C"]},
            "A": {"host": "127.0.0.1", "port": BASE + 11, "neighbors": ["B"]},
            "B": {"host": "127.0.0.1", "port": BASE + 12, "neighbors": []},
            "C": {"host": "127.0.0.1", "port": BASE + 13, "neighbors": []},
        }}
        service = BootstrapService(topo, self_id="S1")

        def mk(nid, **kw):
            return OverlayNode(nid, "127.0.0.1", topo["nodes"][nid]["port"],
                               keepalive=0.2, dead_after=1.0, retry_interval=0.2, **kw)
        s1 = self.nodes["S1"] = OverlayNode(
            "S1", "127.0.0.1", BASE + 10, bootstrap_service=service,
            keepalive=0.2, dead_after=1.0, retry_interval=0.2)
        s1.start()
        for nid, kw in (("C", {}), ("B", {}), ("A", {"min_neighbors": 1, "replace_interval": 0.5})):
            n = self.nodes[nid] = mk(nid, **kw)
            n.start()
            self.assertTrue(n.bootstrap("127.0.0.1", BASE + 10, attempts=3))
        self.assertTrue(wait_for(lambda: self.nodes["A"].neighbor_ids() == ["B"]))
        self.nodes["B"].stop()  # A fica sem vizinhos -> pede substituto ao bootstrapper
        a = self.nodes["A"]
        self.assertTrue(wait_for(lambda: a.neighbor_ids() == ["C"], timeout=8),
                        a.neighbor_ids())

    def test_expired_substitute_target_is_forgotten(self):
        a = self.nodes["A"] = make_node("A")
        addr = ("127.0.0.1", BASE + 40)  # ninguém escuta aqui
        a._targets[addr] = "Z"
        a._dynamic[addr] = time.time() - 1
        a._expire_dynamic(time.time())
        self.assertNotIn(addr, a._targets)
        self.assertNotIn(addr, a._dynamic)
        self.assertIn("Z", a._lost)

    def test_no_parallel_connection_attempts_to_same_target(self):
        """Um alvo lento não pode acumular tentativas de ligação em paralelo."""
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", BASE + 50))
        srv.listen()
        accepted = []
        import threading

        def acceptor():
            srv.settimeout(0.2)
            end = time.time() + 1.5
            while time.time() < end:
                try:
                    accepted.append(srv.accept()[0])  # aceita e nunca responde
                except OSError:
                    pass
        t = threading.Thread(target=acceptor)
        t.start()
        a = self.nodes["A"] = make_node("A")
        a.add_targets({("127.0.0.1", BASE + 50): None})
        with mock.patch.object(node_mod, "HANDSHAKE_TIMEOUT", 1.0):
            for _ in range(10):
                a._connect_missing()
                time.sleep(0.1)
        t.join()
        for c in accepted:
            c.close()
        srv.close()
        self.assertEqual(len(accepted), 1)


class BootstrapServiceTest(unittest.TestCase):
    def setUp(self):
        self.svc = BootstrapService({"nodes": {
            "S": {"host": "h0", "neighbors": ["A"]},
            "A": {"host": "h1", "neighbors": ["B", "C"]},
            "B": {"host": "h2", "neighbors": []},
            "C": {"host": "h3", "neighbors": []},
            "D": {"host": "h4", "neighbors": []},
        }}, self_id="S")

    def test_unknown_node(self):
        self.assertIsNone(self.svc.replacements("X"))
        self.assertIsNone(self.svc.register("X"))

    def test_replacements_only_alive_and_not_excluded(self):
        for n in ("A", "B", "C"):
            self.svc.register(n)  # D nunca se registou: não é candidato
        got = [n["id"] for n in self.svc.replacements("B", have=[], lost=["A"], need=5)]
        self.assertEqual(got, ["C", "S"])
        self.assertNotIn("D", got)

    def test_replacements_prefer_neighbors_of_neighbors(self):
        for n in ("A", "B", "C"):
            self.svc.register(n)
        # B tem A como vizinho: C (vizinho de A) é preferido a S
        got = [n["id"] for n in self.svc.replacements("B", have=["A"], lost=[], need=1)]
        self.assertEqual(got, ["C"])

    def test_replacements_respect_need_and_entry_format(self):
        self.svc.register("A")
        got = self.svc.replacements("B", have=[], lost=[], need=1)
        self.assertEqual(len(got), 1)
        self.assertEqual(set(got[0]), {"id", "host", "port"})


if __name__ == "__main__":
    unittest.main()
