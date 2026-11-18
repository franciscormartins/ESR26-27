"""Coerência das topologias: o XML do CORE (underlay) e o JSON do overlay têm de bater certo."""
import collections
import ipaddress
import json
import os
import unittest
import xml.etree.ElementTree as ET  # só lê ficheiros do próprio repositório

from ott.bootstrapper import BootstrapService

TOPO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "topologies")
SCENARIOS = [1, 2, 3, 4, 5]


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
        for n in (1, 2, 4, 5):  # sem switches
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


class Scenario5Test(unittest.TestCase):
    """Cenário 5 (docs/CENARIO5.md): dois servidores com dois fluxos distintos cada, anel com acessos."""

    def setUp(self):
        self.xml, self.cfg = load(5)
        self.names, self.ips, _m, _s, self.adj = underlay(self.xml)
        self.nodes = self.cfg["nodes"]
        self.graph = symmetric(self.nodes)
        self.base = without(self.graph, {"O7"})  # O7 é o suplente: o overlay funciona sem ele
        with open(os.path.join(TOPO, "cenario5.plano.json")) as f:
            self.plan = json.load(f)["nodes"]
        self.access = {"C1": "O5", "C2": "O5", "C3": "O6", "C4": "O6", "C5": "O1"}

    def test_overlay_id_equals_core_name_and_runs_only_on_hosts(self):
        types = {d.get("name"): d.get("type") for d in self.xml.find("devices")}
        for oid, info in self.nodes.items():
            self.assertEqual(info.get("core", oid), oid)
            self.assertEqual(types[oid], "PC")

    def test_two_servers_with_two_distinct_flows_each_all_subscribed(self):
        servers = {n: set(i["publish"]) for n, i in self.plan.items() if i.get("serve")}
        self.assertEqual(sorted(servers), ["S1", "S2"])
        self.assertTrue(all(len(f) >= 2 for f in servers.values()))
        self.assertFalse(servers["S1"] & servers["S2"])
        wanted = {sub for i in self.plan.values() for sub in i.get("subscribe", [])}
        self.assertEqual(wanted, {f"{s}:{f}" for s, fl in servers.items() for f in fl})

    def test_clients_share_branches(self):
        subs = {n: set(i.get("subscribe", [])) for n, i in self.plan.items()}
        self.assertIn("S1:filme_a", subs["C1"] & subs["C2"])   # C1 e C2 atrás do O5
        self.assertIn("S2:filme_d", subs["C3"] & subs["C4"])   # C3 e C4 atrás do O6

    def test_underlay_mirrors_overlay_with_frr(self):
        routers = {d.get("name") for d in self.xml.find("devices") if d.get("type") == "router"}
        self.assertEqual(len(routers), 9)
        for d in self.xml.find("devices"):
            if d.get("type") == "router":
                self.assertEqual([s.get("name") for s in d.find("services")], ["IPForward", "FRRzebra", "FRROSPFv2"])
        rid = {v: k for k, v in self.names.items()}
        gw = {}
        for host in self.nodes:
            ip = self.nodes[host]["host"]
            gw[host] = self.ips[ip.rsplit(".", 1)[0] + ".1"]
            self.assertIn(gw[host], routers, host)
        infra = [n for n in self.nodes if not n.startswith("C")]
        for a in infra:
            for b in self.graph[a]:
                if b in infra:  # cada ligação overlay entre nós de infraestrutura é uma ligação entre routers
                    self.assertIn(rid[gw[b]], self.adj[rid[gw[a]]], f"{a}-{b} sem ligação direta no underlay")

    def test_slow_link_has_base_delay_and_others_none(self):
        delays = {}
        for link in self.xml.find("links"):
            a, b = self.names[link.get("node1")], self.names[link.get("node2")]
            delays[frozenset((a, b))] = int(link.find("options").get("delay"))
        self.assertEqual(delays.pop(frozenset(("r3", "r6"))), 10000)
        self.assertEqual(set(delays.values()), {0})

    def test_latency_path_for_c5_has_more_hops(self):
        d = distances(self.base, "S2")
        self.assertEqual(d["C5"], 3)                                   # S2-O4-O1-C5, pela ligação lenta
        self.assertEqual(distances(without(self.base, {"O4"}), "S2")["C5"], 4)   # S2-O3-O2-O1-C5, sem atraso

    def test_any_single_intermediate_failure_keeps_other_clients(self):
        for failed in ("O1", "O2", "O3", "O4"):
            graph = without(self.base, {failed})
            for server in ("S1", "S2"):
                reach_from = distances(graph, server)
                for client, acc in self.access.items():
                    if acc != failed:
                        self.assertIn(client, reach_from, f"sem {failed}, {client} fica sem {server}")

    def test_spare_reconnects_isolated_access(self):
        cut = without(self.graph, {"O1", "O4"})          # O5 perde as duas subidas
        self.assertNotIn("C1", distances(without(cut, {"O7"}), "S1"))
        self.assertIn("C1", distances(cut, "S1"))           # com o O7: S1-O2-O6-O7-O5-C1
        self.assertEqual(sorted(self.graph["O7"]), ["O5", "O6"])


if __name__ == "__main__":
    unittest.main()
