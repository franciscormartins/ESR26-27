"""Coerência das topologias: o XML do CORE (underlay) e o JSON do overlay têm de bater certo."""
import collections
import ipaddress
import json
import os
import unittest
import xml.etree.ElementTree as ET  # só lê ficheiros do próprio repositório

from ott.bootstrapper import BootstrapService

TOPO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "topologies")
SCENARIOS = [1, 2, 3, 4]


def load(n):
    xml = ET.parse(os.path.join(TOPO, f"cenario{n}.xml")).getroot()
    with open(os.path.join(TOPO, f"cenario{n}.json")) as f:
        cfg = json.load(f)
    return xml, cfg


def underlay(xml):
    names = {d.get("id"): d.get("name") for d in xml.find("devices")}
    ips, macs, subnets, adj = {}, [], collections.defaultdict(list), collections.defaultdict(set)
    for link in xml.find("links"):
        a, b = link.get("node1"), link.get("node2")
        for node, tag in ((a, "iface1"), (b, "iface2")):
            iface = link.find(tag)
            if iface.get("ip4"):
                ips[iface.get("ip4")] = names[node]
                subnets[str(ipaddress.ip_interface(f"{iface.get('ip4')}/24").network)].append(names[node])
            if iface.get("mac"):
                macs.append(iface.get("mac"))
        adj[a].add(b)
        adj[b].add(a)
    return names, ips, macs, subnets, adj


def reach(start, graph):
    seen, todo = {start}, [start]
    for u in todo:
        for v in graph.get(u, ()):
            if v not in seen:
                seen.add(v)
                todo.append(v)
    return seen


def symmetric(nodes):
    graph = {o: set() for o in nodes}
    for o, info in nodes.items():
        for peer in info["neighbors"]:
            graph[o].add(peer)
            graph[peer].add(o)
    return graph


class TopologyConsistencyTest(unittest.TestCase):
    def test_xml_and_json_agree(self):
        for n in SCENARIOS:
            with self.subTest(cenario=n):
                xml, cfg = load(n)
                names, ips, macs, _subnets, _adj = underlay(xml)
                self.assertEqual(len(set(names.values())), len(names), "nomes de nós repetidos")
                self.assertEqual(len(set(macs)), len(macs), "MACs repetidos")
                nodes = cfg["nodes"]
                for oid, info in nodes.items():
                    core = info.get("core", oid)
                    self.assertIn(core, names.values(), f"{oid}: nó {core} não existe no XML")
                    self.assertEqual(ips.get(info["host"]), core, f"{oid}: {info['host']} não é IP de {core}")
                    for peer in info["neighbors"]:  # uma aresta basta: o bootstrapper torna-a simétrica (D6)
                        self.assertIn(peer, nodes, f"{oid}: vizinho {peer} desconhecido")
                cores = [i.get("core", o) for o, i in nodes.items()]
                self.assertEqual(len(set(cores)), len(cores), "dois nós overlay no mesmo nó do CORE")
                BootstrapService(cfg)  # a configuração tem de ser aceite pelo bootstrapper

    def test_every_subnet_has_two_endpoints_in_p2p_scenarios(self):
        for n in (1, 2, 4):  # sem switches
            with self.subTest(cenario=n):
                subnets = underlay(load(n)[0])[3]
                self.assertEqual([s for s, v in subnets.items() if len(v) != 2], [])

    def test_overlay_is_connected(self):
        for n in SCENARIOS:
            with self.subTest(cenario=n):
                graph = symmetric(load(n)[1]["nodes"])
                self.assertEqual(reach(next(iter(graph)), graph), set(graph))


def distances(graph, src):
    dist, todo = {src: 0}, [src]
    for u in todo:
        for v in graph[u]:
            if v not in dist:
                dist[v] = dist[u] + 1
                todo.append(v)
    return dist


def without(graph, removed):
    return {k: {x for x in v if x not in removed} for k, v in graph.items() if k not in removed}


class CanvasTest(unittest.TestCase):
    def test_every_node_is_inside_the_core_canvas(self):
        for n in SCENARIOS:
            with self.subTest(cenario=n):
                xml = load(n)[0]
                meta = {c.get("name"): c.get("value") for c in xml.find("session_metadata")}
                width, height = json.loads(meta["canvas"])["canvases"][0]["dimensions"]
                for d in xml.find("devices"):
                    p = d.find("position")
                    x, y = float(p.get("x")), float(p.get("y"))
                    self.assertTrue(0 < x < width and 0 < y < height,
                                    f"{d.get('name')} fora do canvas {width}x{height}: ({x:.0f}, {y:.0f})")


class Scenario4Test(unittest.TestCase):
    def setUp(self):
        self.xml, self.cfg = load(4)
        self.names, self.ips, _m, _s, self.adj = underlay(self.xml)
        self.nodes = self.cfg["nodes"]
        self.graph = symmetric(self.nodes)
        self.base = without(self.graph, {"O5"})  # O5 é o nó suplente: o overlay tem de funcionar sem ele
        self.clients = ["C1", "C2", "C3", "C4"]

    def test_overlay_id_equals_core_name_and_runs_only_on_hosts(self):
        types = {d.get("name"): d.get("type") for d in self.xml.find("devices")}
        for oid, info in self.nodes.items():
            self.assertEqual(info.get("core", oid), oid)
            self.assertEqual(types[oid], "PC")

    def test_underlay_is_a_web_of_routers_with_frr(self):
        routers = [d for d in self.xml.find("devices") if d.get("type") == "router"]
        self.assertEqual(len(routers), 13)
        for r in routers:
            self.assertEqual([s.get("name") for s in r.find("services")], ["IPForward", "FRRzebra", "FRROSPFv2"])
        self.assertEqual(len(self.xml.find("service_configurations")), 13)
        names = {r.get("name") for r in routers}
        rgraph = {self.names[i]: {self.names[j] for j in self.adj[i] if self.names[j] in names}
                  for i in self.adj if self.names[i] in names}
        self.assertEqual(sum(len(v) for v in rgraph.values()) // 2, 24)  # 6 raios + 6 + 6 + 6 radiais
        self.assertEqual(len(rgraph["r1"]), 6)
        for r in names:
            self.assertGreaterEqual(len(rgraph[r]), 3, r)
            self.assertEqual(set(distances(without(rgraph, {r}), next(x for x in names if x != r))),
                             names - {r}, f"sem {r} a teia fica desligada")

    def test_hosts_have_gateway_on_a_router(self):
        routers = {d.get("name") for d in self.xml.find("devices") if d.get("type") == "router"}
        for host in (n for n in self.names.values() if n not in routers):
            ip = next(i for i, o in self.ips.items() if o == host)
            self.assertIn(self.ips.get(ip.rsplit(".", 1)[0] + ".1"), routers, host)

    def test_each_server_is_transit_for_the_other(self):
        for server, other, client in (("S1", "S2", "C1"), ("S2", "S1", "C2")):
            d = distances(self.base, other)
            self.assertEqual(d[client], 2)  # o fluxo do outro servidor passa por este servidor
            self.assertEqual(distances(without(self.base, {other}), server)[client], 1)
            # sem este servidor há um caminho mais longo (redirecionamento)
            self.assertEqual(distances(without(self.base, {server}), other)[client], 3)

    def test_any_single_failure_leaves_clients_reachable(self):
        for failed in ("S1", "S2", "O1", "O2", "O3", "O4"):
            graph = without(self.base, {failed})
            survivors = [s for s in ("S1", "S2") if s != failed]
            for server in survivors:
                reach_from = distances(graph, server)
                for client in self.clients:
                    self.assertIn(client, reach_from, f"sem {failed}, {client} fica sem {server}")

    def test_dual_homed_clients_have_equal_cost_paths(self):
        d1 = distances(self.base, "S1")
        d2 = distances(self.base, "S2")
        self.assertEqual(d1["O1"] + 1, d1["O2"] + 1)  # C3 a 2 saltos de S1 por O1 ou por O2
        self.assertEqual(sorted(self.graph["C3"]), ["O1", "O2"])
        self.assertEqual(sorted(self.graph["C4"]), ["O3", "O4"])
        self.assertEqual(d2["O3"], d2["O4"])  # C4 a 2 saltos de S2 por O3 ou por O4

    def test_spare_node_is_optional(self):
        self.assertEqual(set(distances(self.base, "S1")), set(self.base))
        self.assertEqual(sorted(self.graph["O5"]), ["O1", "O4"])


if __name__ == "__main__":
    unittest.main()
