# Executar a partir da raiz do projeto:
# PYTHONPATH=src python3 -m unittest tests.test_routing -v

import unittest

from ott.routing import ACTIVE, INACTIVE, RouteEntry, RoutingTable


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


if __name__ == "__main__":
    unittest.main()
