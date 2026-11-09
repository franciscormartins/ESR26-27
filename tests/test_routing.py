# Executar a partir da raiz do projeto:
# PYTHONPATH=src python3 -m unittest tests.test_routing -v

import unittest
import time
import threading
from dataclasses import replace

from ott.routing import (ACTIVE, INACTIVE, AnnouncementPublisher, Candidate,
                         RouteEntry, RoutingTable)
from ott.protocol import MsgType, make_msg


class FakeNode:
    id = "S1"

    def __init__(self):
        self.broadcasts = []

    def broadcast(self, msg):
        self.broadcasts.append(msg)


class RouteEntryTest(unittest.TestCase):
    def test_defaults_and_key(self):
        route = RouteEntry(flow="filme_a", origin="S1", metric=0)

        self.assertEqual(route.key, ("S1", "filme_a"))
        self.assertIsNone(route.upstream)
        self.assertEqual(route.destinations, set())
        self.assertEqual(route.local_subscribers, set())
        self.assertEqual(route.candidates, {})
        self.assertEqual((route.epoch, route.delay), (0, float("inf")))
        self.assertEqual(route.state, INACTIVE)

    def test_mutable_defaults_are_independent(self):
        first = RouteEntry(flow="filme_a", origin="S1", metric=0)
        second = RouteEntry(flow="filme_b", origin="S1", metric=0)

        first.destinations.add("O2")
        first.local_subscribers.add("C1")
        first.candidates["O1"] = Candidate(1, 0)

        self.assertEqual(second.destinations, set())
        self.assertEqual(second.local_subscribers, set())
        self.assertEqual(second.candidates, {})

    def test_active_state_can_be_set(self):
        route = RouteEntry(flow="filme_a", origin="S1", metric=1, state=ACTIVE)
        self.assertEqual(route.state, ACTIVE)


class RoutingTableTest(unittest.TestCase):
    def test_put_get_and_remove(self):
        table = RoutingTable()
        route = RouteEntry(flow="filme_a", origin="S1", metric=2, upstream="O2")

        self.assertIs(table.put(route), route)
        self.assertIs(table.get("S1", "filme_a"), route)
        self.assertIs(table.remove("S1", "filme_a"), route)
        self.assertIsNone(table.get("S1", "filme_a"))
        self.assertIsNone(table.remove("S1", "filme_a"))

    def test_origin_and_flow_both_participate_in_key(self):
        table = RoutingTable()
        routes = [
            RouteEntry(flow="filme_a", origin="S1", metric=1),
            RouteEntry(flow="filme_b", origin="S1", metric=1),
            RouteEntry(flow="filme_a", origin="S2", metric=1),
        ]
        for route in routes:
            table.put(route)

        self.assertIs(table.get("S1", "filme_a"), routes[0])
        self.assertIs(table.get("S1", "filme_b"), routes[1])
        self.assertIs(table.get("S2", "filme_a"), routes[2])

    def test_put_replaces_entry_with_same_key(self):
        table = RoutingTable()
        old = RouteEntry(flow="filme_a", origin="S1", metric=3)
        new = RouteEntry(flow="filme_a", origin="S1", metric=1, upstream="O2")

        table.put(old)
        table.put(new)

        self.assertIs(table.get("S1", "filme_a"), new)
        self.assertEqual(len(table.routes), 1)

    def test_get_missing_route_returns_none(self):
        self.assertIsNone(RoutingTable().get("S1", "filme_a"))

    def test_put_validates_entry_and_fields(self):
        table = RoutingTable()

        with self.assertRaises(TypeError):
            table.put(object())
        with self.assertRaises(ValueError):
            table.put(RouteEntry(flow="", origin="S1", metric=0))
        with self.assertRaises(ValueError):
            table.put(RouteEntry(flow="filme_a", origin="", metric=0))
        with self.assertRaises(ValueError):
            table.put(RouteEntry(flow="filme_a", origin="S1", metric=-1))


class AnnouncementPublisherTest(unittest.TestCase):
    def test_publish_once_announces_each_flow_with_independent_epoch(self):
        node = FakeNode()
        publisher = AnnouncementPublisher(node, ["filme_a", "filme_b"])

        first_round = publisher.publish_once()
        second_round = publisher.publish_once()

        self.assertEqual([m["data"]["flow"] for m in first_round], ["filme_a", "filme_b"])
        self.assertEqual([m["data"]["epoch"] for m in first_round], [1, 1])
        self.assertEqual([m["data"]["epoch"] for m in second_round], [2, 2])
        for msg in node.broadcasts:
            self.assertEqual(msg["type"], "ANNOUNCE")
            self.assertEqual(msg["src"], "S1")
            self.assertEqual(msg["data"]["server_id"], "S1")
            self.assertEqual(msg["data"]["metric"], 0)

    def test_start_publishes_periodically_and_stop_stops_it(self):
        node = FakeNode()
        publisher = AnnouncementPublisher(node, ["filme_a"], interval=0.02)
        publisher.start()
        deadline = time.monotonic() + 1
        while len(node.broadcasts) < 3 and time.monotonic() < deadline:
            time.sleep(0.005)
        publisher.stop()
        stopped_count = len(node.broadcasts)
        time.sleep(0.05)

        self.assertGreaterEqual(stopped_count, 3)
        self.assertEqual(len(node.broadcasts), stopped_count)

    def test_invalid_publisher_configuration(self):
        node = FakeNode()
        for flows, interval in (([], 1), ([""], 1), (["x", "x"], 1), (["x"], 0)):
            with self.subTest(flows=flows, interval=interval):
                with self.assertRaises(ValueError):
                    AnnouncementPublisher(node, flows, interval)

        with self.assertRaises(ValueError):
            AnnouncementPublisher(node, "filme_a")

    def test_publish_installs_local_zero_cost_route(self):
        table = RoutingTable()
        publisher = AnnouncementPublisher(FakeNode(), ["filme_a"], routing_table=table)

        publisher.publish_once()

        route = table.get("S1", "filme_a")
        self.assertEqual((route.metric, route.upstream, route.epoch), (0, None, 1))


class AnnouncementLearningTest(unittest.TestCase):
    class Node(FakeNode):
        id = "O2"

        def __init__(self):
            super().__init__()
            self.received_broadcasts = []
            self.handlers = {}
            self.sent = []
            self.send_ok = True
            self.up_callbacks = []
            self.down_callbacks = []

        def register_handler(self, msg_type, handler):
            self.handlers[msg_type] = handler

        def send(self, neighbor, msg):
            self.sent.append((neighbor, msg))
            return self.send_ok

        def on_neighbor_up(self, callback):
            self.up_callbacks.append(callback)

        def on_neighbor_down(self, callback):
            self.down_callbacks.append(callback)

        def broadcast(self, msg, exclude=()):
            self.received_broadcasts.append((msg, set(exclude)))

    def announce(self, epoch, metric):
        return make_msg(MsgType.ANNOUNCE, "O1", server_id="S1",
                        flow="filme_a", epoch=epoch, metric=metric)

    def test_learns_and_propagates_with_incremented_cost_excluding_sender(self):
        table, node = RoutingTable(), self.Node()
        table.attach(node)

        self.assertTrue(table.handle_announce(node, "O1", self.announce(1, 1)))

        route = table.get("S1", "filme_a")
        self.assertEqual((route.metric, route.upstream, route.epoch), (2, "O1", 1))
        forwarded, exclude = node.received_broadcasts[0]
        self.assertEqual(forwarded["src"], "O2")
        self.assertEqual(forwarded["data"]["metric"], 2)
        self.assertEqual(exclude, {"O1"})

    def test_duplicate_old_and_non_improving_paths_are_not_forwarded(self):
        table, node = RoutingTable(), self.Node()
        table.handle_announce(node, "O1", self.announce(2, 1))
        self.assertFalse(table.handle_announce(node, "O1", self.announce(2, 1)))
        self.assertFalse(table.handle_announce(node, "O3", self.announce(1, 0)))
        self.assertEqual(len(node.received_broadcasts), 1)

    def test_better_path_and_new_epoch_are_forwarded(self):
        table, node = RoutingTable(), self.Node()
        table.handle_announce(node, "O1", self.announce(1, 3))
        self.assertTrue(table.handle_announce(node, "O3", self.announce(1, 0)))
        self.assertEqual(table.get("S1", "filme_a").upstream, "O3")
        # Época nova vinda primeiro por um caminho pior: o montante atual (O3)
        # mantém-se, mas a época nova é propagada uma vez.
        self.assertTrue(table.handle_announce(node, "O4", self.announce(2, 4)))
        route = table.get("S1", "filme_a")
        self.assertEqual((route.epoch, route.metric, route.upstream), (2, 1, "O3"))
        self.assertEqual(len(node.received_broadcasts), 3)

    def test_own_announcement_is_ignored(self):
        table, node = RoutingTable(), self.Node()
        msg = make_msg(MsgType.ANNOUNCE, "O1", server_id="O2",
                       flow="filme_a", epoch=1, metric=0)
        self.assertFalse(table.handle_announce(node, "O1", msg))
        self.assertIsNone(table.get("O2", "filme_a"))


class RouteActivationTest(unittest.TestCase):
    class Node(AnnouncementLearningTest.Node):
        pass

    def make_route(self, table, *, upstream="O1"):
        return table.put(RouteEntry(flow="filme_a", origin="S1", metric=1,
                                   upstream=upstream, epoch=1))

    def request(self, msg_type, subscriber):
        return make_msg(msg_type, "O1", server_id="S1", flow="filme_a",
                        subscriber_id=subscriber)

    def test_local_activate_and_deactivate_follow_upstream(self):
        table, node = RoutingTable(), self.Node()
        route = self.make_route(table)

        self.assertTrue(table.activate(node, "S1", "filme_a"))
        self.assertTrue(table.activate(node, "S1", "filme_a"))
        self.assertEqual(len(node.sent), 1)
        self.assertEqual(node.sent[0][0], "O1")
        self.assertEqual(node.sent[0][1]["type"], "ACTIVATE")
        self.assertEqual(node.sent[0][1]["src"], node.id)
        self.assertEqual(route.state, ACTIVE)

        self.assertTrue(table.deactivate(node, "S1", "filme_a"))
        self.assertEqual([m[1]["type"] for m in node.sent], ["ACTIVATE", "DEACTIVATE"])
        self.assertEqual(route.state, INACTIVE)

    def types_sent(self, node):
        return [(peer, msg["type"]) for peer, msg in node.sent]

    def test_branches_are_aggregated_only_first_and_last_go_upstream(self):
        table, node = RoutingTable(), self.Node()
        route = self.make_route(table)

        table.handle_activate(node, "C1", self.request(MsgType.ACTIVATE, "client-1"))
        table.handle_activate(node, "C2", self.request(MsgType.ACTIVATE, "client-2"))
        self.assertTrue(table.activate(node, "S1", "filme_a"))  # subscritor local
        self.assertEqual(route.destinations, {"C1", "C2"})
        self.assertEqual(self.types_sent(node), [("O1", "ACTIVATE")])
        self.assertEqual(node.sent[0][1]["data"]["subscriber_id"], "client-1")

        table.handle_deactivate(node, "C1", self.request(MsgType.DEACTIVATE, "client-1"))
        self.assertTrue(table.deactivate(node, "S1", "filme_a"))
        self.assertEqual(route.state, ACTIVE)
        self.assertEqual(len(node.sent), 1)  # ainda há procura (C2)

        table.handle_deactivate(node, "C2", self.request(MsgType.DEACTIVATE, "client-2"))
        self.assertEqual(route.destinations, set())
        self.assertEqual(route.state, INACTIVE)
        self.assertEqual(self.types_sent(node), [("O1", "ACTIVATE"), ("O1", "DEACTIVATE")])

    def test_repeated_requests_from_same_branch_are_idempotent(self):
        table, node = RoutingTable(), self.Node()
        route = self.make_route(table)

        self.assertTrue(table.handle_activate(node, "C1", self.request(MsgType.ACTIVATE, "a")))
        self.assertTrue(table.handle_activate(node, "C1", self.request(MsgType.ACTIVATE, "b")))
        self.assertEqual(route.destinations, {"C1"})
        self.assertTrue(table.handle_deactivate(node, "C1", self.request(MsgType.DEACTIVATE, "a")))
        self.assertTrue(table.handle_deactivate(node, "C1", self.request(MsgType.DEACTIVATE, "b")))
        self.assertEqual(self.types_sent(node), [("O1", "ACTIVATE"), ("O1", "DEACTIVATE")])

    def test_requests_without_subscriber_id_are_accepted_and_forwarded(self):
        table, node = RoutingTable(), self.Node()
        self.make_route(table)
        request = make_msg(MsgType.ACTIVATE, "C1", server_id="S1", flow="filme_a")

        self.assertTrue(table.handle_activate(node, "C1", request))
        self.assertEqual(self.types_sent(node), [("O1", "ACTIVATE")])
        self.assertNotIn("subscriber_id", node.sent[0][1]["data"])

    def test_activation_from_upstream_is_rejected(self):
        table, node = RoutingTable(), self.Node()
        route = self.make_route(table)
        self.assertFalse(table.handle_activate(node, "O1", self.request(MsgType.ACTIVATE, "x")))
        self.assertEqual(route.destinations, set())

    def test_server_registers_and_removes_downstream_subscription(self):
        table, node = RoutingTable(), self.Node()
        node.id = "S1"
        route = self.make_route(table, upstream=None)

        self.assertTrue(table.handle_activate(node, "O2",
                                              self.request(MsgType.ACTIVATE, "C1")))
        self.assertEqual(route.destinations, {"O2"})
        self.assertEqual(route.state, ACTIVE)
        self.assertEqual(node.sent, [])
        self.assertTrue(table.handle_deactivate(node, "O2",
                                                self.request(MsgType.DEACTIVATE, "C1")))
        self.assertEqual(route.state, INACTIVE)

    def test_attach_registers_all_three_route_handlers(self):
        table, node = RoutingTable(), self.Node()
        table.attach(node)
        self.assertEqual(set(node.handlers),
                         {"ANNOUNCE", "ACTIVATE", "DEACTIVATE", "WITHDRAW"})
        self.assertEqual(len(node.up_callbacks), 1)
        self.assertEqual(len(node.down_callbacks), 1)

    def test_upstream_failure_without_alternative_withdraws_until_relearned(self):
        table, node = RoutingTable(), self.Node()
        route = self.make_route(table)
        table.handle_activate(node, "C1", self.request(MsgType.ACTIVATE, "C1"))
        table.activate(node, "S1", "filme_a")
        node.sent.clear()

        table.handle_neighbor_down(node, "O1")

        self.assertIsNone(route.upstream)
        self.assertEqual(route.metric, float("inf"))
        self.assertEqual(route.state, INACTIVE)
        self.assertEqual(route.destinations, set())          # ramos desfeitos
        self.assertEqual(route.local_subscribers, {"O2"})    # procura local pendente
        withdraw, exclude = node.received_broadcasts[-1]
        self.assertEqual((withdraw["type"], exclude), ("WITHDRAW", set()))
        self.assertEqual(node.sent, [])

        announcement = make_msg(MsgType.ANNOUNCE, "O5", server_id="S1",
                                flow="filme_a", epoch=2, metric=2)
        self.assertTrue(table.handle_announce(node, "O5", announcement))
        self.assertEqual(route.upstream, "O5")
        self.assertEqual(route.metric, 3)
        self.assertEqual(route.state, ACTIVE)
        self.assertEqual([(peer, msg["type"]) for peer, msg in node.sent],
                         [("O5", "ACTIVATE")])

    def test_downstream_failure_removes_branch_and_deactivates_upstream(self):
        table, node = RoutingTable(), self.Node()
        route = self.make_route(table)
        table.handle_activate(node, "C1", self.request(MsgType.ACTIVATE, "C1"))
        node.sent.clear()

        table.handle_neighbor_down(node, "C1")

        self.assertEqual(route.destinations, set())
        self.assertEqual(route.state, INACTIVE)
        self.assertEqual([(peer, msg["type"]) for peer, msg in node.sent],
                         [("O1", "DEACTIVATE")])

    def test_downstream_failure_keeps_branch_while_other_destinations_remain(self):
        table, node = RoutingTable(), self.Node()
        route = self.make_route(table)
        table.handle_activate(node, "C1", self.request(MsgType.ACTIVATE, "C1"))
        table.handle_activate(node, "C2", self.request(MsgType.ACTIVATE, "C2"))
        node.sent.clear()

        table.handle_neighbor_down(node, "C1")

        self.assertEqual(route.destinations, {"C2"})
        self.assertEqual(route.state, ACTIVE)
        self.assertEqual(node.sent, [])

    def test_new_neighbor_receives_announcements_for_known_routes(self):
        table, node = RoutingTable(), self.Node()
        route = self.make_route(table)

        table.handle_neighbor_up(node, "O9")

        self.assertEqual(len(node.sent), 1)
        peer, announcement = node.sent[0]
        self.assertEqual(peer, "O9")
        self.assertEqual(announcement["type"], "ANNOUNCE")
        self.assertEqual(announcement["data"]["metric"], route.metric)

    def test_better_announcement_moves_branch_to_new_upstream_once(self):
        table, node = RoutingTable(), self.Node()
        route = self.make_route(table)
        table.handle_activate(node, "C1", self.request(MsgType.ACTIVATE, "C1"))
        table.handle_activate(node, "C2", self.request(MsgType.ACTIVATE, "C2"))
        table.activate(node, "S1", "filme_a")
        node.sent.clear()

        message = make_msg(MsgType.ANNOUNCE, "O5", server_id="S1",
                           flow="filme_a", epoch=2, metric=1)
        self.assertTrue(table.handle_announce(node, "O5", message))

        self.assertEqual(route.upstream, "O5")
        self.assertEqual(route.state, ACTIVE)
        self.assertEqual([(peer, msg["type"]) for peer, msg in node.sent],
                         [("O1", "DEACTIVATE"), ("O5", "ACTIVATE")])

    def test_neighbor_callbacks_enqueue_route_updates_off_callback_thread(self):
        table, node = RoutingTable(), self.Node()
        route = self.make_route(table)
        sent = threading.Event()
        original_send = node.send

        def recording_send(neighbor, msg):
            result = original_send(neighbor, msg)
            sent.set()
            return result

        node.send = recording_send
        table.attach(node)
        node.up_callbacks[0](node, "O9")
        self.assertTrue(sent.wait(1))
        self.assertEqual(node.sent[-1][0], "O9")

        node.down_callbacks[0](node, "O1")
        deadline = time.monotonic() + 1
        while route.upstream is not None and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertIsNone(route.upstream)


class RouteSelectionTest(unittest.TestCase):
    """Passo 5: tabela de candidatos, desempate por RTT e histerese."""

    class Node(AnnouncementLearningTest.Node):
        def __init__(self):
            super().__init__()
            self.rtts = {}

        def rtt(self, neighbor):
            return self.rtts.get(neighbor)

    def announce(self, src, epoch, metric, delay=None):
        fields = dict(server_id="S1", flow="filme_a", epoch=epoch, metric=metric)
        if delay is not None:
            fields["delay"] = delay
        return make_msg(MsgType.ANNOUNCE, src, **fields)

    def feed(self, table, node, *announcements):
        for src, epoch, metric, *delay in announcements:
            table.handle_announce(node, src, self.announce(src, epoch, metric, *delay))
        return table.get("S1", "filme_a")

    def test_new_epoch_arriving_first_by_worse_path_causes_no_churn(self):
        table, node = RoutingTable(), self.Node()
        self.feed(table, node, ("O1", 1, 1), ("O5", 1, 2))
        table.activate(node, "S1", "filme_a")
        node.sent.clear()
        node.received_broadcasts.clear()

        self.assertTrue(table.handle_announce(node, "O5", self.announce("O5", 2, 2)))
        self.assertFalse(table.handle_announce(node, "O1", self.announce("O1", 2, 1)))

        route = table.get("S1", "filme_a")
        self.assertEqual((route.upstream, route.metric, route.epoch), ("O1", 2, 2))
        self.assertEqual(node.sent, [])                    # sem DEACTIVATE/ACTIVATE
        self.assertEqual(len(node.received_broadcasts), 1)  # época propagada uma vez

    def test_equal_hops_without_rtt_prefer_lowest_id_whatever_the_order(self):
        for order in (("O7", "O3"), ("O3", "O7")):
            with self.subTest(order=order):
                table, node = RoutingTable(), self.Node()
                route = self.feed(table, node, *((src, 1, 1) for src in order))
                self.assertEqual(route.upstream, "O3")
                self.feed(table, node, ("O7", 2, 1), ("O3", 2, 1))
                self.assertEqual(route.upstream, "O3")

    def test_equal_hops_tie_broken_by_delay_whatever_the_order(self):
        for order in (("O3", "O7"), ("O7", "O3")):
            with self.subTest(order=order):
                table, node = RoutingTable(), self.Node()
                node.rtts = {"O3": 0.020, "O7": 0.004}
                route = self.feed(table, node, *((src, 1, 1, 0.0) for src in order))
                self.assertEqual(route.upstream, "O7")
                self.assertAlmostEqual(route.delay, 0.002)

    def test_small_delay_gain_does_not_switch_but_large_gain_does(self):
        table, node = RoutingTable(), self.Node()
        node.rtts = {"O3": 0.0044, "O7": 0.0040}          # 2,2 ms vs 2,0 ms
        route = self.feed(table, node, ("O3", 1, 1, 0.0), ("O7", 1, 1, 0.0))
        self.assertEqual(route.upstream, "O3")              # ganho abaixo da histerese
        node.rtts["O3"] = 0.040                             # ligação O3 degradada (netem)
        self.feed(table, node, ("O3", 2, 1, 0.0), ("O7", 2, 1, 0.0))
        self.assertEqual(route.upstream, "O7")
        self.feed(table, node, ("O7", 3, 1, 0.0), ("O3", 3, 1, 0.0))
        self.assertEqual(route.upstream, "O7")              # estável nas rondas seguintes

    def test_fewer_hops_win_even_with_worse_delay(self):
        table, node = RoutingTable(), self.Node()
        node.rtts = {"O3": 0.002, "O7": 0.200}
        route = self.feed(table, node, ("O3", 1, 2, 0.0), ("O7", 1, 1, 0.0))
        self.assertEqual((route.upstream, route.metric), ("O7", 2))

    def test_delay_is_accumulated_and_announced_only_when_known(self):
        table, node = RoutingTable(), self.Node()
        node.rtts = {"O1": 0.010}
        route = self.feed(table, node, ("O1", 1, 1, 0.003))
        self.assertAlmostEqual(route.delay, 0.008)
        forwarded, _ = node.received_broadcasts[-1]
        self.assertAlmostEqual(forwarded["data"]["delay"], 0.008)

        other, other_node = RoutingTable(), self.Node()      # sem RTT medido
        self.feed(other, other_node, ("O1", 1, 1, 0.003))
        self.assertNotIn("delay", other_node.received_broadcasts[-1][0]["data"])


class WithdrawRepairTest(unittest.TestCase):
    """Passo 6: WITHDRAW, reparação rápida e aceitação de épocas após retirada."""

    Node = RouteSelectionTest.Node
    announce = RouteSelectionTest.announce
    feed = RouteSelectionTest.feed

    def withdraw(self, src, epoch=1):
        return make_msg(MsgType.WITHDRAW, src, server_id="S1", flow="filme_a", epoch=epoch)

    def types_sent(self, node):
        return [(peer, msg["type"]) for peer, msg in node.sent]

    def test_upstream_down_with_feasible_alternative_repairs_without_withdraw(self):
        table, node = RoutingTable(), self.Node()
        route = self.feed(table, node, ("O1", 1, 1), ("O5", 1, 1))
        table.activate(node, "S1", "filme_a")
        node.sent.clear()
        node.received_broadcasts.clear()

        table.handle_neighbor_down(node, "O1")

        self.assertEqual((route.upstream, route.metric), ("O5", 2))
        self.assertEqual(self.types_sent(node), [("O5", "ACTIVATE")])
        self.assertEqual([m["type"] for m, _ in node.received_broadcasts], ["ANNOUNCE"])

    def test_alternative_that_may_go_through_this_node_is_not_feasible(self):
        table, node = RoutingTable(), self.Node()
        # O5 anuncia 2 saltos (= os nossos): pode estar a usar-nos como caminho.
        route = self.feed(table, node, ("O1", 1, 1), ("O5", 1, 2))
        node.received_broadcasts.clear()

        table.handle_neighbor_down(node, "O1")

        self.assertIsNone(route.upstream)
        self.assertEqual(route.candidates, {})
        self.assertEqual([m["type"] for m, _ in node.received_broadcasts], ["WITHDRAW"])

    def test_withdraw_from_other_neighbor_only_removes_its_candidate(self):
        table, node = RoutingTable(), self.Node()
        route = self.feed(table, node, ("O1", 1, 1), ("O5", 1, 1))
        node.received_broadcasts.clear()

        self.assertFalse(table.handle_withdraw(node, "O5", self.withdraw("O5")))

        self.assertEqual(route.upstream, "O1")
        self.assertNotIn("O5", route.candidates)
        self.assertEqual((node.sent, node.received_broadcasts), ([], []))

    def test_withdraw_from_upstream_moves_branch_to_feasible_alternative(self):
        table, node = RoutingTable(), self.Node()
        route = self.feed(table, node, ("O1", 1, 1), ("O5", 1, 1))
        table.handle_activate(node, "C1", make_msg(
            MsgType.ACTIVATE, "C1", server_id="S1", flow="filme_a"))
        node.sent.clear()

        self.assertTrue(table.handle_withdraw(node, "O1", self.withdraw("O1")))

        self.assertEqual(route.upstream, "O5")
        self.assertEqual(route.destinations, {"C1"})       # reparado: ramo mantém-se
        self.assertEqual(self.types_sent(node), [("O1", "DEACTIVATE"), ("O5", "ACTIVATE")])

    def test_withdraw_cascades_and_clears_downstream_branches(self):
        table, node = RoutingTable(), self.Node()
        route = self.feed(table, node, ("O1", 1, 1))
        table.handle_activate(node, "C1", make_msg(
            MsgType.ACTIVATE, "C1", server_id="S1", flow="filme_a"))
        node.received_broadcasts.clear()

        table.handle_withdraw(node, "O1", self.withdraw("O1"))

        self.assertIsNone(route.upstream)
        self.assertEqual((route.destinations, route.state), (set(), INACTIVE))
        self.assertEqual(table.active_destinations("S1", "filme_a"), ())
        self.assertEqual([m["type"] for m, _ in node.received_broadcasts], ["WITHDRAW"])

    def test_pathless_route_accepts_restarted_server_epoch(self):
        table, node = RoutingTable(), self.Node()
        route = self.feed(table, node, ("O1", 50, 1))
        table.activate(node, "S1", "filme_a")
        table.handle_neighbor_down(node, "O1")
        node.sent.clear()

        self.assertTrue(table.handle_announce(node, "O5", self.announce("O5", 1, 1)))

        self.assertEqual((route.upstream, route.epoch, route.state), ("O5", 1, ACTIVE))
        self.assertEqual(self.types_sent(node), [("O5", "ACTIVATE")])

    def test_route_with_path_rejects_older_epoch(self):
        table, node = RoutingTable(), self.Node()
        route = self.feed(table, node, ("O1", 50, 2))
        self.assertFalse(table.handle_announce(node, "O5", self.announce("O5", 1, 0)))
        self.assertEqual((route.upstream, route.epoch), ("O1", 50))


class RouteTtlTest(unittest.TestCase):
    """Passo 7: tempo de vida como rede de segurança."""

    Node = RouteSelectionTest.Node
    announce = RouteSelectionTest.announce
    feed = RouteSelectionTest.feed
    TTL = 30.0

    def later(self, seconds):
        return time.monotonic() + seconds

    def test_without_ttl_nothing_expires(self):
        table, node = RoutingTable(), self.Node()
        route = self.feed(table, node, ("O1", 1, 1))
        self.assertEqual(table.expire(node, now=self.later(10_000)), 0)
        self.assertEqual(route.upstream, "O1")

    def test_invalid_ttl_is_rejected(self):
        for ttl in (0, -1, float("inf"), float("nan"), True, "30"):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                RoutingTable(route_ttl=ttl)

    def test_fresh_candidates_are_kept(self):
        table, node = RoutingTable(route_ttl=self.TTL), self.Node()
        route = self.feed(table, node, ("O1", 1, 1), ("O5", 1, 1))
        self.assertEqual(table.expire(node, now=self.later(self.TTL - 1)), 0)
        self.assertEqual(set(route.candidates), {"O1", "O5"})

    def test_stale_alternative_is_forgotten_silently(self):
        table, node = RoutingTable(route_ttl=self.TTL), self.Node()
        route = self.feed(table, node, ("O1", 1, 1), ("O5", 1, 1))
        node.received_broadcasts.clear()
        route.candidates["O1"] = replace(route.candidates["O1"], seen_at=self.later(self.TTL))

        self.assertEqual(table.expire(node, now=self.later(self.TTL + 1)), 1)

        self.assertEqual((route.upstream, set(route.candidates)), ("O1", {"O1"}))
        self.assertEqual((node.sent, node.received_broadcasts), ([], []))

    def test_stale_upstream_with_fresh_alternative_is_repaired(self):
        table, node = RoutingTable(route_ttl=self.TTL), self.Node()
        route = self.feed(table, node, ("O1", 1, 1), ("O5", 1, 1))
        table.activate(node, "S1", "filme_a")
        node.sent.clear()
        route.candidates["O5"] = replace(route.candidates["O5"], seen_at=self.later(self.TTL))

        table.expire(node, now=self.later(self.TTL + 1))

        self.assertEqual(route.upstream, "O5")
        self.assertEqual([(p, m["type"]) for p, m in node.sent],
                         [("O1", "DEACTIVATE"), ("O5", "ACTIVATE")])

    def test_stale_upstream_without_alternative_withdraws(self):
        table, node = RoutingTable(route_ttl=self.TTL), self.Node()
        route = self.feed(table, node, ("O1", 1, 1))
        table.handle_activate(node, "C1", make_msg(
            MsgType.ACTIVATE, "C1", server_id="S1", flow="filme_a"))
        node.received_broadcasts.clear()

        table.expire(node, now=self.later(self.TTL + 1))

        self.assertIsNone(route.upstream)
        self.assertEqual((route.destinations, route.state), (set(), INACTIVE))
        self.assertEqual([m["type"] for m, _ in node.received_broadcasts], ["WITHDRAW"])

    def test_repeated_announcement_renews_lifetime(self):
        table, node = RoutingTable(route_ttl=self.TTL), self.Node()
        route = self.feed(table, node, ("O1", 1, 1))
        first_seen = route.candidates["O1"].seen_at
        time.sleep(0.01)
        self.assertFalse(table.handle_announce(node, "O1", self.announce("O1", 1, 1)))
        self.assertGreater(route.candidates["O1"].seen_at, first_seen)

    def test_origin_route_never_expires(self):
        table, node = RoutingTable(route_ttl=self.TTL), self.Node()
        node.id = "S1"
        route = table.publish_local("S1", "filme_a", 1)
        table.expire(node, now=self.later(10_000))
        self.assertEqual((route.metric, route.upstream), (0, None))

    def test_background_thread_expires_silent_upstream(self):
        table, node = RoutingTable(route_ttl=0.2), self.Node()
        table.attach(node)
        try:
            route = self.feed(table, node, ("O1", 1, 1))
            deadline = time.monotonic() + 2
            while route.upstream is not None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertIsNone(route.upstream)
        finally:
            table.close()


if __name__ == "__main__":
    unittest.main()
