"""Verificador de cenários, demonstração guiada, acompanhamento de efeitos de rede e novos eventos do estado."""
import tempfile
import unittest
from unittest import mock

from ott import status as status_mod
from ott.ctl import scenarios
from ott.ctl.backend import LocalBackend
from ott.ctl.control import Orchestrator
from ott.ctl.plan import load_plan
from ott.ctl.web import Controller
from tests.support_ctl import write_mini
from tests.test_status import FakeReceiver, writer


def result(test_id, status="ok", value=None):
    return {"id": test_id, "title": "t", "status": status, "error": None, "seconds": 1.0, "checks": [], "notes": [],
            "measures": [] if value is None else [{"name": "m", "unit": "s", "value": value, "expect": None, "ok": True}]}


class ScenarioTestsTest(unittest.TestCase):
    def test_catalogue_depends_on_scenario_and_backend(self):
        plan = load_plan("cenario4")
        local = {t["id"]: t for t in scenarios.tests_for(plan, "local")}
        core = {t["id"]: t for t in scenarios.tests_for(plan, "core")}
        self.assertFalse(local["B6"]["runs_here"])
        self.assertTrue(core["B6"]["runs_here"])
        self.assertIn("C7", local)
        mini = load_plan(write_mini())
        ids = {t["id"] for t in scenarios.tests_for(mini)}
        self.assertTrue({"G1", "G2"} <= ids)
        self.assertNotIn("C7", ids)

    def test_summary_and_markdown(self):
        results = [result("A1", value=2.0), result("A1", value=4.0), result("A1", "falhou"), result("B1", "ignorado")]
        by = {s["id"]: s for s in scenarios.summarize(results)}
        self.assertEqual((by["A1"]["runs"], by["A1"]["ok"]), (3, 2))
        self.assertEqual((by["A1"]["measures"][0]["min"], by["A1"]["measures"][0]["mean"], by["A1"]["measures"][0]["max"]),
                         (2.0, 3.0, 4.0))
        self.assertEqual(by["B1"]["runs"], 0)
        md = scenarios.render_markdown(results)
        self.assertIn("2/3 ok", md)
        self.assertIn("ignorado", md)
        self.assertIn("A1", scenarios.render_text(results))

    def test_core_only_test_is_skipped_locally(self):
        plan = load_plan(write_mini())
        orch = Orchestrator(plan, LocalBackend(plan), tempfile.mkdtemp(prefix="ottctl-sc-"))
        t = {"id": "X", "title": "x", "core": True, "fn": lambda ctx: self.fail("não devia correr")}
        self.assertEqual(scenarios.run_test(orch, t)["status"], "ignorado")

    def test_demo_steps_exist_for_every_scenario(self):
        for name in ("cenario1", "cenario2", "cenario3", "cenario4", "cenario5"):
            steps = scenarios.demo_for(load_plan(name))
            titles = [s["title"] for s in steps]
            self.assertGreaterEqual(len(steps), 8, name)
            self.assertTrue(all("title" in s and "text" in s for s in steps))
            for wanted in ("O caminho de cada cliente", "O cliente muda de filme", "Repor tudo"):
                self.assertIn(wanted, titles, name)
            self.assertEqual(titles[-1], "Repor tudo")
            self.assertTrue(all(s["tab"] in (None, "node", "video", "paths", "clients") for s in steps if "tab" in s), name)

    def test_every_scenario_has_the_generic_tests_and_the_network_ones_need_the_core(self):
        for name in ("cenario1", "cenario2", "cenario3", "cenario4", "cenario5"):
            plan = load_plan(name)
            local = {t["id"]: t for t in scenarios.tests_for(plan, "local")}
            core = {t["id"]: t for t in scenarios.tests_for(plan, "core")}
            self.assertTrue({f"G{i}" for i in range(1, 10)} <= set(local), name)
            for n in ("N1", "N2", "N3", "N4"):
                self.assertFalse(local[n]["runs_here"], name)
                self.assertTrue(core[n]["runs_here"], name)
            self.assertEqual("A1" in local, name == "cenario4")

    def test_failure_helpers_use_the_overlay_graph(self):
        plan = load_plan("cenario5")
        self.assertTrue(scenarios._connected(plan, "S1", "C1", {"O2"}))          # há outro caminho
        self.assertFalse(scenarios._connected(plan, "S1", "C1", {"O5"}))         # o O5 é o único acesso do C1
        mini = load_plan(write_mini())
        self.assertFalse(scenarios._connected(mini, "S", "C", {"A"}))
        state = {"paths": [{"node": "C", "key": "S:filme_a", "path": ["S", "A", "C"], "fresh": True, "hops": 2, "seq": 1, "age": 1}],
                 "tree": {"S:filme_a": [{"from": "S", "to": "A"}, {"from": "A", "to": "C"}]}}
        self.assertEqual(scenarios.client_paths(state, "C"), {"S:filme_a": ["S", "A", "C"]})
        self.assertEqual(scenarios.tree_chain(state, "S:filme_a", "C"), ["S", "A", "C"])
        self.assertEqual(scenarios._pick_failure(mini, state), ("C", "S:filme_a", ["S", "A", "C"], "A", False))
        state["paths"][0]["fresh"] = False                                       # um teste antigo já não conta
        self.assertEqual(scenarios.client_paths(state, "C"), {})
        d = scenarios._depends_on(plan, "S1")
        self.assertIn(("C2", "S1:filme_a"), d[0])                                 # perde o fluxo
        self.assertIn(("C1", "filme_c"), d[1])                                    # tem réplica no S2
        self.assertIn(("C4", "S2:filme_d"), d[2])                                 # não depende do S1


class ControllerTest(unittest.TestCase):
    def test_history_sampling_is_incremental(self):
        c = Controller(orch=None)
        self.assertIsNotNone(c)
        self.assertTrue(hasattr(c, "sample"))


class StatusPhaseAndGapTest(unittest.TestCase):
    def test_phase_and_bind(self):
        w = writer()
        w.phase = "a registar"
        self.assertEqual(w.snapshot()["phase"], "a registar")
        rx = {("S", "f"): FakeReceiver()}
        w.bind(receivers=rx, published=["f"])
        w.phase = "a correr"
        snap = w.snapshot()
        self.assertEqual((snap["phase"], snap["published"], len(snap["flows"])), ("a correr", ["f"], 1))

    def test_short_interruption_is_reported_as_gap(self):
        rx = FakeReceiver()
        with mock.patch.object(status_mod, "STALL_AFTER", 60), mock.patch.object(status_mod, "GAP_MIN", 0.2):
            w = writer(receivers={("S", "f"): rx}, active=[("S", "f")])
            rx.stats.received = 10
            w.snapshot()
            rx.stats.received = 20
            w.snapshot()
            import time
            time.sleep(0.4)
            rx.stats.received = 30
            w.snapshot()
        gaps = [e for e in w.events if e["kind"] == "fluxo_lacuna"]
        self.assertEqual(len(gaps), 1)
        self.assertGreaterEqual(gaps[0]["gap"], 0.3)


if __name__ == "__main__":
    unittest.main()


class UnderlayDeTodosOsCenariosTest(unittest.TestCase):
    """O painel desenha a topologia do CORE: tem de funcionar em todos os cenários (o 3 tem switches em <networks>)."""

    def test_todos_os_nos_overlay_tem_ligacoes(self):
        from ott.ctl.plan import available, load_plan, underlay_of
        for name in available():
            plan = load_plan(name)
            under = underlay_of(plan)
            ligados = {end for l in under["links"] for end in (l["a"], l["b"])}
            for node in plan.nodes.values():
                self.assertIn(node.core, ligados, f"{name}: {node.core} sem ligações")

    def test_cenario3_marca_ligacoes_a_switches(self):
        from ott.ctl.plan import load_plan, underlay_of
        under = underlay_of(load_plan("cenario3"))
        self.assertTrue(any(n["type"] == "switch" for n in under["nodes"]))
        self.assertTrue(any(l.get("sw") for l in under["links"]))
