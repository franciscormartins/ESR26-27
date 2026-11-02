# Executar a partir da raiz do projeto:
# PYTHONPATH=src python3 -m unittest tests.test_routing -v

import unittest
import time
import threading

from ott.routing import (ACTIVE, INACTIVE, AnnouncementPublisher, RouteEntry,
                         RoutingTable)
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
        self.assertEqual(route.seen_announcements, set())
        self.assertEqual(route.state, INACTIVE)

    def test_mutable_defaults_are_independent(self):
        first = RouteEntry(flow="filme_a", origin="S1", metric=0)
        second = RouteEntry(flow="filme_b", origin="S1", metric=0)

        first.destinations.add("O2")
        first.seen_announcements.add("ann-1")

        self.assertEqual(second.destinations, set())
        self.assertEqual(second.seen_announcements, set())

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
        self.assertTrue(table.handle_announce(node, "O4", self.announce(2, 4)))
        route = table.get("S1", "filme_a")
        self.assertEqual((route.epoch, route.metric, route.upstream), (2, 5, "O4"))
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

    def test_downstream_subscribers_share_upstream_branch(self):
        table, node = RoutingTable(), self.Node()
        route = self.make_route(table)

        table.handle_activate(node, "C1", self.request(MsgType.ACTIVATE, "client-1"))
        table.handle_activate(node, "C1", self.request(MsgType.ACTIVATE, "client-2"))
        self.assertEqual(route.destinations, {"C1"})
        self.assertEqual(route.subscriptions["C1"], {"client-1", "client-2"})
        self.assertEqual([m[1]["type"] for m in node.sent], ["ACTIVATE", "ACTIVATE"])

        table.handle_deactivate(node, "C1", self.request(MsgType.DEACTIVATE, "client-1"))
        self.assertEqual(route.state, ACTIVE)
        self.assertEqual([m[1]["type"] for m in node.sent],
                         ["ACTIVATE", "ACTIVATE", "DEACTIVATE"])
        table.handle_deactivate(node, "C1", self.request(MsgType.DEACTIVATE, "client-2"))
        self.assertEqual(route.destinations, set())
        self.assertEqual(route.state, INACTIVE)
        self.assertEqual([m[1]["type"] for m in node.sent],
                         ["ACTIVATE", "ACTIVATE", "DEACTIVATE", "DEACTIVATE"])

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
        self.assertEqual(set(node.handlers), {"ANNOUNCE", "ACTIVATE", "DEACTIVATE"})
        self.assertEqual(len(node.up_callbacks), 1)
        self.assertEqual(len(node.down_callbacks), 1)

    def test_upstream_failure_invalidates_route_until_alternative_is_learned(self):
        table, node = RoutingTable(), self.Node()
        route = self.make_route(table)
        table.handle_activate(node, "C1", self.request(MsgType.ACTIVATE, "C1"))
        node.sent.clear()

        table.handle_neighbor_down(node, "O1")

        self.assertIsNone(route.upstream)
        self.assertEqual(route.metric, float("inf"))
        self.assertEqual(route.state, INACTIVE)
        self.assertEqual(route.destinations, {"C1"})

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

    def test_new_neighbor_receives_announcements_for_known_routes(self):
        table, node = RoutingTable(), self.Node()
        route = self.make_route(table)

        table.handle_neighbor_up(node, "O9")

        self.assertEqual(len(node.sent), 1)
        peer, announcement = node.sent[0]
        self.assertEqual(peer, "O9")
        self.assertEqual(announcement["type"], "ANNOUNCE")
        self.assertEqual(announcement["data"]["metric"], route.metric)

    def test_better_announcement_moves_active_subscriptions_to_new_upstream(self):
        table, node = RoutingTable(), self.Node()
        route = self.make_route(table)
        table.handle_activate(node, "C1", self.request(MsgType.ACTIVATE, "C1"))
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


if __name__ == "__main__":
    unittest.main()
