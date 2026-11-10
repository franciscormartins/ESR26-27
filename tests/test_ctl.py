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
        self.assertEqual(available(), ["cenario1", "cenario2", "cenario3", "cenario4"])
        for name in available():
            with self.subTest(plano=name):
                plan = load_plan(name)
                self.assertTrue(plan.nodes[plan.bootstrap].serve)
                self.assertEqual(len(plan.positions()), len(plan.nodes))
                for spec in plan.nodes.values():
                    self.assertIn(spec.id, plan.overlay)

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

    def test_the_local_mode_has_no_network_commands(self):
        orch = Orchestrator(load_plan(write_mini()), LocalBackend(load_plan(write_mini())), tempfile.mkdtemp())
        with self.assertRaises(BackendError):
            orch.netem("S", "eth0", delay="10ms")


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
        state = self.wait(lambda s: any(f["rate"] > 5 for f in (s["nodes"]["C"]["status"] or {}).get("flows", [])))
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
