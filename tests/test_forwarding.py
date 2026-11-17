# Plano de dados: RPF estrito no OverlayForwarder (UDP real em loopback).
# PYTHONPATH=src python3 -m unittest tests.test_forwarding -v
import socket
import unittest

from ott.forwarding import OverlayForwarder, TrafficStats
from ott.routing import ACTIVE, RouteEntry, RoutingTable
from ott.streaming.client import StreamReceiver
from ott.streaming.rtp import RtpPacket, pack_data, unpack_data, unpack_data_hop

from tests.test_integration import loopback_aliases_available, wait_for

PORT = 47620
HOSTS = {"A": "127.0.0.5", "U": "127.0.0.6", "X": "127.0.0.7", "D": "127.0.0.8"}


class FakeNode:
    """Nó A com três vizinhos: U (montante), X (outro vizinho) e D (destino)."""
    id = "A"

    def neighbor_ids(self):
        return [nid for nid in HOSTS if nid != "A"]

    def neighbor_address(self, node_id):
        return HOSTS.get(node_id) if node_id != "A" else None


def datagram(seq, origin="S1", flow="filme_a", hop=None):
    rtp = RtpPacket(seq, 0, b"frame%d" % seq, ssrc=1).encode()
    return pack_data(origin, flow, rtp, hop=hop)


@unittest.skipUnless(loopback_aliases_available(*HOSTS.values()),
                     "requer endereços de loopback 127.0.0.5-8")
class StrictRpfTest(unittest.TestCase):
    def setUp(self):
        self.table = RoutingTable()
        self.route = self.table.put(RouteEntry(
            flow="filme_a", origin="S1", metric=1, upstream="U", epoch=1,
            destinations={"D"}, local_subscribers={"A"}, state=ACTIVE))
        self.forwarder = OverlayForwarder(FakeNode(), self.table, port=PORT,
                                          bind=HOSTS["A"]).start()
        self.receiver = StreamReceiver("S1", "filme_a", listen=False)
        self.forwarder.set_local_receiver("S1", "filme_a", self.receiver)
        self.senders = {}
        for nid in ("U", "X"):
            sock = self.senders[nid] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind((HOSTS[nid], 0))
        self.downstream = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.downstream.bind((HOSTS["D"], PORT))
        self.downstream.settimeout(2.0)

    def tearDown(self):
        self.forwarder.stop()
        for sock in (*self.senders.values(), self.downstream):
            sock.close()

    def send_from(self, nid, seq, hop=None, **kw):
        """Envia a partir do IP de ``nid``, com o id ``hop`` (por omissão ``nid``)."""
        packet = datagram(seq, hop=hop or nid, **kw)
        self.senders[nid].sendto(packet, (HOSTS["A"], PORT))

    def received_at_downstream(self):
        packet = self.downstream.recv(65535)
        _, _, rtp = unpack_data(packet)
        return RtpPacket.decode(rtp).seq

    def test_only_upstream_traffic_is_delivered_and_forwarded(self):
        self.send_from("X", 1)   # outro vizinho: descartado
        self.send_from("U", 2)   # montante: aceite
        self.assertEqual(self.received_at_downstream(), 2)
        self.assertTrue(wait_for(lambda: self.forwarder.received == 2))
        self.assertEqual((self.forwarder.rpf_dropped, self.forwarder.dropped), (1, 1))
        self.assertEqual(self.forwarder.forwarded, 1)
        self.assertEqual(self.receiver.stats.received, 1)  # entrega local só do montante

    def test_upstream_change_switches_accepted_neighbor(self):
        self.route.upstream = "X"
        self.send_from("U", 1)   # antigo montante: descartado
        self.send_from("X", 2)
        self.assertEqual(self.received_at_downstream(), 2)
        self.assertTrue(wait_for(lambda: self.forwarder.received == 2))
        self.assertEqual(self.forwarder.rpf_dropped, 1)

    def test_traffic_without_route_or_upstream_is_dropped(self):
        self.send_from("U", 1, flow="desconhecido")
        self.route.upstream = None         # rota sem caminho (montante caiu)
        self.send_from("U", 2)
        self.assertTrue(wait_for(lambda: self.forwarder.received == 2))
        self.assertTrue(wait_for(lambda: self.forwarder.rpf_dropped == 2))
        self.assertEqual(self.forwarder.forwarded, 0)
        self.assertEqual(self.receiver.stats.received, 0)

    def test_upstream_identified_by_id_not_source_ip(self):
        # Regressão L7 (CORE, cenário 3): o montante U envia o UDP por outra
        # interface, com um IP de origem diferente do da ligação TCP. O pacote
        # leva o id de U e tem de ser aceite; reencaminhado leva o id do A.
        self.send_from("X", 1, hop="U")
        packet = self.downstream.recv(65535)
        origin, flow, hop, rtp = unpack_data_hop(packet)
        self.assertEqual((origin, flow, hop), ("S1", "filme_a", "A"))
        self.assertEqual(RtpPacket.decode(rtp).seq, 1)
        self.assertEqual((self.forwarder.rpf_dropped, self.forwarder.dropped), (0, 0))
        self.assertEqual(self.receiver.stats.received, 1)

    def test_sender_that_is_not_a_neighbor_is_dropped(self):
        self.send_from("U", 1, hop="Z")
        self.assertTrue(wait_for(lambda: self.forwarder.dropped == 1))
        self.assertEqual((self.forwarder.rpf_dropped, self.forwarder.forwarded), (0, 0))

    def test_own_flow_coming_back_is_not_reforwarded(self):
        # Um ciclo devolveria ao A um fluxo que ele próprio origina.
        own = self.table.publish_local("A", "filme_a", 1)
        own.destinations.add("D")
        self.table._refresh_state(own, "A")
        self.send_from("U", 1, origin="A")
        self.assertTrue(wait_for(lambda: self.forwarder.rpf_dropped == 1))
        self.assertEqual(self.forwarder.forwarded, 0)

    def test_traffic_counts_per_flow_and_neighbor(self):
        self.send_from("X", 1)   # descartado por RPF
        for seq in (1, 2, 5):    # 3 e 4 perdidos
            self.send_from("U", seq)
            self.assertEqual(self.received_at_downstream(), seq)
        entry = self.forwarder.traffic.flows[("S1", "filme_a")]
        self.assertEqual((entry.stats.received, entry.stats.lost), (3, 2))
        self.assertEqual(entry.from_neighbor, {"U": 3})
        self.assertEqual(entry.rpf_from, {"X": 1})
        self.assertEqual(entry.sent_to, {"D": 3})
        self.assertEqual(len(self.forwarder.traffic.summary_lines("D")), 1)


class TrafficStatsTest(unittest.TestCase):
    """Contadores de ``--log-traffic`` sem rede."""

    def rtp(self, seq):
        return RtpPacket(seq, 0, b"", ssrc=7)

    def test_loss_from_sequence_gaps_and_sources(self):
        traffic = TrafficStats()
        for seq, nb in ((10, "U"), (11, "U"), (14, "X")):   # troca U->X perde 12 e 13
            traffic.accepted("S1", "f", nb, self.rtp(seq), 0.0)
        traffic.sent("S1", "f", "D")
        traffic.rpf_dropped("S1", "f", "U")
        [line] = traffic.summary_lines()
        self.assertEqual(line, "S1:f recebidos=3 perdidos=2 (40.0%) | de: U=2, X=1 "
                               "| para: D=1 | descartados RPF: U=1")

    def test_summary_for_neighbor_only_lists_flows_through_it(self):
        traffic = TrafficStats()
        traffic.accepted("S1", "a", "U", self.rtp(1), 0.0)
        traffic.accepted("S2", "b", "X", self.rtp(1), 0.0)
        traffic.sent("S2", "b", "D")
        self.assertEqual([l.split()[0] for l in traffic.summary_lines("D")], ["S2:b"])
        self.assertEqual(traffic.summary_lines("Z"), [])


if __name__ == "__main__":
    unittest.main()
