"""ottctl: planos, comandos do CORE (com um executor simulado), validação do tc e um cenário local com nós reais."""
import os
import shutil
import tempfile
import time
import unittest

from ott.ctl.backend import BackendError, CoreBackend, LocalBackend, netem_command
from ott.ctl.control import Orchestrator, build
from ott.ctl.plan import PlanError, available, load_plan
from tests.support_ctl import write_mini


class PlanTest(unittest.TestCase):
    def test_all_scenario_plans_load_and_are_consistent(self):
        self.assertEqual(available(), ["cenario1", "cenario2", "cenario3", "cenario4", "cenario5"])
        for name in available():
            with self.subTest(plano=name):
                plan = load_plan(name)
                self.assertTrue(plan.nodes[plan.bootstrap].serve)
                self.assertEqual(len(plan.positions()), len(plan.nodes))
                for spec in plan.nodes.values():
                    self.assertIn(spec.id, plan.overlay)

    def test_each_flow_has_its_own_video_and_the_same_flow_is_the_same_video(self):
        plan = load_plan("cenario5")
        videos = {f: plan.video_of(f) for f in plan.all_flows()}
        self.assertIsNone(videos["filme_a"])                       # o filme real (progex/movie.Mjpeg)
        self.assertEqual(plan.video_label("filme_a"), "movie.Mjpeg")
        for flow in ("filme_b", "filme_c", "filme_d"):
            self.assertTrue(videos[flow].endswith(f"videos{os.sep}{flow}.Mjpeg"), videos[flow])
            self.assertTrue(os.path.isfile(videos[flow]))
        self.assertEqual(len({v for v in videos.values() if v}), 3)   # todos diferentes
        self.assertEqual(plan.publishers_of("filme_c"), ["S1", "S2"])  # replicado: o mesmo ficheiro nos dois
        be = LocalBackend(plan)
        be.rundir = tempfile.mkdtemp()
        for server in ("S1", "S2"):
            argv = be.argv(plan.nodes[server], "/tmp/x.json")
            self.assertIn(f"filme_c={videos['filme_c']}", argv)

    def test_servers_generate_route_tests_unless_the_plan_turns_them_off(self):
        plan = load_plan("cenario1")
        be = LocalBackend(plan)
        be.rundir = tempfile.mkdtemp()
        argv = be.argv(plan.nodes["S1"], "/tmp/x.json")
        self.assertEqual(argv[argv.index("--route-test") + 1], "2")
        self.assertNotIn("--route-test", be.argv(plan.nodes["C1"], "/tmp/x.json"))
        plan.route_test = 0
        self.assertNotIn("--route-test", be.argv(plan.nodes["S1"], "/tmp/x.json"))

    def test_a_missing_video_in_the_plan_is_an_error(self):
        plan = load_plan("cenario1")
        plan.videos["filme_a"] = "videos/nao_existe.Mjpeg"
        with self.assertRaises(PlanError):
            plan.video_of("filme_a")

    def test_scenario4_has_two_servers_with_two_flows_each(self):
        plan = load_plan("cenario4")
        servers = {n.id: n.publish for n in plan.nodes.values() if n.serve}
        self.assertEqual(sorted(servers), ["S1", "S2"])
        self.assertTrue(all(len(set(f)) >= 2 for f in servers.values()))
        self.assertEqual([n.id for n in plan.nodes.values() if n.spare], ["O5"])

    def test_a_subscription_nobody_publishes_is_rejected(self):
        path = write_mini()
        import json
        with open(path) as f:
            data = json.load(f)
        data["nodes"]["C"]["subscribe"] = ["S:inexistente"]
        with open(path, "w") as f:
            json.dump(data, f)
        with self.assertRaises(PlanError):
            load_plan(path)

    def test_unknown_scenario(self):
        with self.assertRaises(PlanError):
            load_plan("cenario99")


class NetemTest(unittest.TestCase):
    def test_builds_the_tc_command(self):
        self.assertEqual(netem_command("eth1", "30ms", "10ms", "5", "500kbit"),
                         ["tc", "qdisc", "replace", "dev", "eth1", "root", "netem", "delay", "30ms", "10ms",
                          "distribution", "normal", "loss", "5%", "rate", "500kbit"])
        self.assertEqual(netem_command("eth0", loss="2.5%")[-2:], ["loss", "2.5%"])

    def test_rejects_anything_that_is_not_a_plain_value(self):
        for kwargs in ({"iface": "eth0; reboot", "delay": "1ms"}, {"iface": "$(id)", "delay": "1ms"},
                       {"iface": "eth0", "delay": "30ms; id"}, {"iface": "eth0", "delay": "x"},
                       {"iface": "eth0", "loss": "5% && id"}, {"iface": "eth0", "rate": "1;id"},
                       {"iface": "eth0", "jitter": "5ms"}, {"iface": "eth0"}, {"iface": "", "delay": "1ms"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(BackendError):
                netem_command(**kwargs)


class FakeRunner:
    def __init__(self, rc=0):
        self.calls, self.rc = [], rc

    def __call__(self, argv, timeout=20):
        self.calls.append(argv)
        return self.rc, ""


class CoreBackendTest(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="pycore-")
        self.plan = load_plan("cenario4")
        for session in (3, 5):
            os.makedirs(os.path.join(self.base, f"pycore.{session}"))
            for name in self.plan.core_names() + ["r4", "r3"]:
                open(os.path.join(self.base, f"pycore.{session}", name), "w").close()
        self.addCleanup(shutil.rmtree, self.base, True)

    def backend(self, runner=None, session=None):
        return CoreBackend(self.plan, session=session, runner=runner or FakeRunner(), base=self.base)

    def test_session_is_the_newest_with_all_the_nodes(self):
        self.assertEqual(self.backend().session, 5)
        self.assertEqual(self.backend(session=3).session, 3)

    def test_no_session_with_the_scenario_nodes_is_an_error(self):
        os.remove(os.path.join(self.base, "pycore.5", "C3"))
        os.remove(os.path.join(self.base, "pycore.3", "C3"))
        with self.assertRaises(BackendError):
            self.backend()

    def test_start_runs_the_ott_inside_the_node(self):
        runner = FakeRunner()
        be = self.backend(runner)
        spec = self.plan.nodes["C1"]
        be.start(spec, be.argv(spec, "/tmp/x/C1.json"), "/tmp/x/C1.log")
        argv = runner.calls[0]
        self.assertEqual(argv[:4], ["vcmd", "-c", os.path.join(self.base, "pycore.5", "C1"), "--"])
        command = argv[-1]
        self.assertIn("exec setsid ./bin/ott -i C1 --status-file /tmp/x/C1.json", command)
        self.assertIn("-b 10.0.34.20:5000", command)
        self.assertIn("--subscribe S1:filme_a --subscribe S2:filme_c", command)
        self.assertTrue(command.endswith("&"))

    def test_clients_save_their_frames_for_the_dashboard(self):
        be = self.backend(FakeRunner())
        argv = be.argv(self.plan.nodes["C1"], "/tmp/x/C1.json")
        self.assertEqual(argv[argv.index("--frame-dir") + 1], "/tmp/x/frames/C1")
        self.assertNotIn("--frame-dir", be.argv(self.plan.nodes["S1"], "/tmp/x/S1.json"))

    def test_signals_use_pkill_inside_the_node(self):
        runner = FakeRunner()
        self.backend(runner).signal(self.plan.nodes["S1"], "STOP")
        self.assertEqual(runner.calls[0][-4:], ["pkill", "-STOP", "-f", "^python3 -m ott"])
        with self.assertRaises(BackendError):   # pkill devolve 1 quando não encontra o processo
            self.backend(FakeRunner(rc=1)).signal(self.plan.nodes["S1"], "KILL")

    def test_servers_start_before_the_other_nodes(self):
        runner = FakeRunner()
        orch = Orchestrator(self.plan, self.backend(runner), tempfile.mkdtemp(prefix="ottctl-"))
        orch.up(settle=0)
        order = [c[2].rsplit("/", 1)[1] for c in runner.calls]
        self.assertEqual(sorted(order[:2]), ["S1", "S2"])
        self.assertNotIn("O5", order)                         # o suplente só com spare=True
        orch2 = Orchestrator(self.plan, self.backend(FakeRunner()), tempfile.mkdtemp(prefix="ottctl-"))
        self.assertIn("O5", orch2.up(spare=True, settle=0))

    def test_link_netem_is_applied_on_both_ends_and_validated(self):
        runner = FakeRunner()
        orch = Orchestrator(self.plan, self.backend(runner), tempfile.mkdtemp(prefix="ottctl-"))
        orch.link_netem("r4", "eth1", "r3", "eth2", delay="30ms")
        self.assertEqual([c[2].rsplit("/", 1)[1] for c in runner.calls], ["r4", "r3"])
        self.assertEqual(runner.calls[0][4:9], ["tc", "qdisc", "replace", "dev", "eth1"])
        with self.assertRaises(BackendError):
            orch.netem("r4", "eth1; id", delay="30ms")
        with self.assertRaises(BackendError):
            orch.netem("rm-rf", "eth1", delay="30ms")        # nó que não existe no cenário
        n = len(runner.calls)
        orch.link_set("r4", "eth1", "r3", "eth2", False)
        self.assertEqual(runner.calls[n][4:], ["ip", "link", "set", "eth1", "down"])

    def test_router_down_puts_every_interface_of_the_router_down_and_up_restores_them(self):
        runner = FakeRunner()
        orch = Orchestrator(self.plan, self.backend(runner), tempfile.mkdtemp(prefix="ottctl-"))
        ifaces = len(routerlinks(orch, "r4"))
        self.assertGreater(ifaces, 2)
        orch.router_set("r4", False)
        cmds = [c[4:] for c in runner.calls]
        self.assertEqual(len(cmds), ifaces)
        self.assertTrue(all(c[:3] == ["ip", "link", "set"] and c[-1] == "down" for c in cmds))
        self.assertTrue(all(c[2].endswith("/r4") for c in runner.calls))
        self.assertEqual(len([e for e in orch.net_effects.values() if e.get("down")]), ifaces)
        orch.router_set("r4", True)
        self.assertTrue(all(c[-1] == "up" for c in [c[4:] for c in runner.calls[ifaces:]]))
        with self.assertRaises(BackendError):
            orch.router_set("O1", False)          # um host não é um router
        with self.assertRaises(BackendError):
            orch.router_set("r99", False)

    def test_ip_path_follows_the_routing_tables_hop_by_hop(self):
        under = Orchestrator(self.plan, self.backend(FakeRunner()), tempfile.mkdtemp()).underlay()
        # tabelas de rotas de brincar: cada nó sabe o vizinho (e o seu IP) a que deve entregar para chegar a cada destino
        nbr = {}
        for l in under["links"]:
            nbr.setdefault(l["a"], {})[l["b"]] = l["ipb"]
            nbr.setdefault(l["b"], {})[l["a"]] = l["ipa"]
        wanted = ["C1", "r10", "r4", "O1"]       # caminho forçado: não é o mais curto do desenho
        class Runner:
            def __call__(self_, argv, timeout=20):
                here, dst_ip = argv[2].rsplit("/", 1)[1], argv[-1]
                nxt = wanted[wanted.index(here) + 1] if here in wanted else None
                return 0, f"{dst_ip} via {nbr[here][nxt]} dev eth0 src 1.1.1.1 uid 0"
        orch = Orchestrator(self.plan, self.backend(Runner()), tempfile.mkdtemp())
        # o destino vê-se pelo IP: só o último salto é directo
        self.assertEqual(orch.ip_path("C1", "O1")[:3], ["C1", "r10", "r4"])
        self.assertIsNone(Orchestrator(self.plan, LocalBackend(self.plan), tempfile.mkdtemp()).ip_path("C1", "O1"))

    def test_hop_link_picks_a_link_between_routers_on_the_real_ip_path(self):
        under = Orchestrator(self.plan, self.backend(FakeRunner()), tempfile.mkdtemp()).underlay()
        nbr = {}
        for l in under["links"]:
            nbr.setdefault(l["a"], {})[l["b"]] = l["ipb"]
            nbr.setdefault(l["b"], {})[l["a"]] = l["ipa"]
        wanted = ["C1", "r10", "r4", "O1"]

        class Runner:
            calls = []

            def __call__(self_, argv, timeout=20):
                if argv[-3:-1] == ["route", "get"]:
                    here = argv[2].rsplit("/", 1)[1]
                    nxt = wanted[wanted.index(here) + 1]
                    return 0, f"{argv[-1]} via {nbr[here][nxt]} dev eth0"
                self_.calls.append(argv)
                return 0, ""
        orch = Orchestrator(self.plan, self.backend(Runner()), tempfile.mkdtemp())
        x, ix, y, iy, sw = orch.hop_link("C1", "O1")
        self.assertEqual({x, y}, {"r10", "r4"})              # a ligação entre routers, não as dos hosts
        self.assertIsNone(sw)
        orch.hop_netem("C1", "O1", delay="50ms")
        tc = [c for c in Runner.calls if "netem" in c]
        self.assertEqual(len(tc), 2)                         # nos dois extremos
        self.assertEqual({c[2].rsplit("/", 1)[1] for c in tc}, {"r10", "r4"})
        orch.hop_set("C1", "O1", False)
        self.assertEqual(orch.hop_link("C1", "O1"), (x, ix, y, iy, sw))   # a mesma ligação, mesmo que o OSPF desvie
        self.assertEqual(orch.load()["hops"], {"C1>O1": [x, ix, y, iy, sw]})
        orch.clear_network()
        self.assertEqual(orch.hop_links, {})

    def test_hop_obstacles_need_the_core(self):
        plan = load_plan(write_mini())
        orch = Orchestrator(plan, LocalBackend(plan), tempfile.mkdtemp())
        with self.assertRaises(BackendError):
            orch.hop_netem("S", "A", delay="10ms")

    def test_the_local_mode_has_no_network_commands(self):
        orch = Orchestrator(load_plan(write_mini()), LocalBackend(load_plan(write_mini())), tempfile.mkdtemp())
        with self.assertRaises(BackendError):
            orch.netem("S", "eth0", delay="10ms")


def routerlinks(orch, name):
    return [l for l in orch.underlay()["links"] if name in (l["a"], l["b"])]


class LocalScenarioTest(unittest.TestCase):
    """Lança nós reais (S - A - C) nesta máquina, mata um e confere o que o painel veria."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ottctl-int-")
        self.orch = build(write_mini(self.dir), "local", port=47960, data_port=47961, rundir=self.dir)
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.addCleanup(self.orch.down)

    def wait(self, cond, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            state = self.orch.state()
            if cond(state):
                return state
            time.sleep(0.2)
        self.fail(f"não aconteceu a tempo; estado: { {k: v['health'] for k, v in state['nodes'].items()} }")

    def test_up_streams_video_and_reacts_to_a_failure(self):
        self.assertEqual(self.orch.up(), ["S", "A", "C"])
        state = self.wait(lambda s: any(f["rate"] > 5 for f in (s["nodes"]["C"]["status"] or {}).get("flows", []))
                          and all(e["active"] for e in s["tree"].get("S:filme_a", [])) and len(s["tree"].get("S:filme_a", [])) == 2)
        self.assertEqual({k: v["health"] for k, v in state["nodes"].items()}, {"S": "up", "A": "up", "C": "up"})
        self.assertEqual(state["tree"]["S:filme_a"], [{"from": "S", "to": "A", "active": True},
                                                      {"from": "A", "to": "C", "active": True}])
        self.assertEqual(state["flows"], ["S:filme_a"])
        self.assertEqual(self.orch.state()["nodes"]["S"]["status"]["bootstrapper"], ["A", "C"])

        self.orch.kill(["A"])
        state = self.wait(lambda s: any(e["kind"] == "fluxo_parou" for e in s["events"]))
        self.assertEqual(state["nodes"]["A"]["health"], "dead")
        kinds = [e["kind"] for e in state["events"] if e["node"] == "C"]
        self.assertIn("rota_perdida", kinds)
        self.assertNotIn({"from": "S", "to": "A", "active": True}, state["tree"].get("S:filme_a", []))

        self.orch.start(["A"])                                # o nó volta e o fluxo regressa
        self.wait(lambda s: s["nodes"]["A"]["health"] == "up" and
                  any(f["rate"] > 5 for f in (s["nodes"]["C"]["status"] or {}).get("flows", [])), timeout=25)

    def test_route_tests_show_the_path_and_the_client_can_change_its_movies(self):
        self.orch.up()
        state = self.wait(lambda s: any(p["fresh"] for p in s["paths"]), timeout=20)
        (path,) = state["paths"]
        self.assertEqual((path["node"], path["key"], path["path"], path["hops"]), ("C", "S:filme_a", ["S", "A", "C"], 2))
        self.assertEqual(state["route_test"], 2.0)
        self.assertEqual(state["options"], [{"key": "S:filme_a", "flow": "filme_a", "server": "S",
                                             "publishers": ["S"], "video": "movie.Mjpeg"}])
        for bad in (["S:filme_x"], ["filme_a:S"], "S:filme_a", ["S:filme_a", "S:filme_a:b"]):
            with self.assertRaises(BackendError):
                self.orch.set_subscriptions("C", bad)
        with self.assertRaises(BackendError):
            self.orch.set_subscriptions("S", [])             # um servidor não subscreve

        self.orch.set_subscriptions("C", [])                 # o cliente deixa de pedir o filme
        state = self.wait(lambda s: s["nodes"]["C"]["health"] == "up" and not s["nodes"]["C"]["status"].get("flows"))
        self.assertEqual(state["nodes"]["C"]["subscribe"], [])
        self.assertEqual(state["subs_changed"], ["C"])
        state = self.wait(lambda s: (s["nodes"]["S"]["status"]["routes"] or [{}])[0].get("state") == "inativa")
        other = Orchestrator.attach(write_mini(self.dir), self.dir)   # outro processo vê a mudança
        self.assertEqual(other.plan.nodes["C"].subscribe, [])

        self.orch.set_subscriptions("C", ["S:filme_a"])      # e volta a pedi-lo
        state = self.wait(lambda s: any(f["rate"] > 5 for f in (s["nodes"]["C"]["status"] or {}).get("flows", [])), timeout=25)
        self.assertEqual(state["subs_changed"], [])
        self.orch.set_subscriptions("C", [])
        self.orch.reset_subscriptions()
        self.assertEqual(self.orch.plan.nodes["C"].subscribe, ["S:filme_a"])

    def test_freeze_is_reported_and_thaw_recovers(self):
        self.orch.up()
        self.wait(lambda s: s["nodes"]["A"]["health"] == "up")
        self.orch.freeze(["A"])
        self.assertEqual(self.orch.state()["nodes"]["A"]["health"], "frozen")
        self.orch.thaw(["A"])
        self.wait(lambda s: s["nodes"]["A"]["health"] == "up")

    def test_a_node_cannot_be_started_twice_and_attach_finds_the_run(self):
        self.orch.up()
        self.wait(lambda s: all(n["health"] == "up" for n in s["nodes"].values()))
        with self.assertRaises(BackendError):
            self.orch.start(["S"])
        other = Orchestrator.attach(write_mini(self.dir), self.dir)   # outro processo ottctl a ligar-se
        self.assertEqual(other.backend.name, "local")
        other.stop(["C"])
        self.wait(lambda s: s["nodes"]["C"]["health"] == "stopped")


if __name__ == "__main__":
    unittest.main()
