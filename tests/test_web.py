"""Servidor do painel: API, proteções contra pedidos de outros sítios e ações."""
import http.client
import json
import tempfile
import threading
import unittest

from ott.ctl.backend import BackendError, LocalBackend
from ott.ctl.control import Orchestrator
from ott.ctl.plan import load_plan
from ott.ctl.web import do_action, make_server
from tests.support_ctl import write_mini


class RecordingOrchestrator(Orchestrator):
    """Regista as ações em vez de lançar processos."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.calls = []

    def _record(self, name, ids):
        for node_id in ids:
            self._spec(node_id)       # valida os ids como o orquestrador a sério
        self.calls.append((name, ids))

    def start(self, ids):
        self._record("start", ids)

    def stop(self, ids):
        self._record("stop", ids)

    def kill(self, ids):
        self._record("kill", ids)

    def up(self, **k):
        self.calls.append(("up", k))
        return list(self.plan.nodes)

    def down(self, **k):
        self.calls.append(("down",))

    def link_netem(self, a, ia, b, ib, **params):
        self.calls.append(("link_netem", a, ia, b, ib, params))


class WebTest(unittest.TestCase):
    def setUp(self):
        plan = load_plan(write_mini())
        self.orch = RecordingOrchestrator(plan, LocalBackend(plan), tempfile.mkdtemp(prefix="ottctl-web-"))
        self.server = make_server(self.orch, 0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Host": f"127.0.0.1:{self.port}", **(headers or {})}
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def post(self, payload, **headers):
        return self.request("POST", "/api/action", json.dumps(payload),
                            {"Content-Type": "application/json", **headers})

    def test_page_and_state(self):
        status, data = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"ottctl", data)
        status, data = self.request("GET", "/api/state")
        state = json.loads(data)
        self.assertEqual((status, state["scenario"], sorted(state["nodes"])), (200, "mini", ["A", "C", "S"]))
        self.assertEqual({n["health"] for n in state["nodes"].values()}, {"off"})
        self.assertEqual(self.request("GET", "/nada")[0], 404)

    def test_requests_for_another_host_are_refused(self):
        self.assertEqual(self.request("GET", "/api/state", headers={"Host": "evil.example.com"})[0], 403)
        self.assertEqual(self.post({"action": "down"}, Host="evil.example.com")[0], 403)
        self.assertEqual(self.orch.calls, [])

    def test_post_must_be_json(self):
        status, _ = self.request("POST", "/api/action", '{"action": "down"}', {"Content-Type": "text/plain"})
        self.assertEqual(status, 415)
        status, _ = self.request("POST", "/api/action", "não é json", {"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        status, _ = self.request("POST", "/api/action", "[1]", {"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        self.assertEqual(self.orch.calls, [])

    def test_actions_reach_the_orchestrator(self):
        self.assertEqual(self.post({"action": "kill", "node": "S"})[0], 200)
        self.assertEqual(self.post({"action": "stop", "nodes": ["A", "C"]})[0], 200)
        self.assertEqual(self.post({"action": "down"})[0], 200)
        self.assertEqual(self.orch.calls, [("kill", ["S"]), ("stop", ["A", "C"]), ("down",)])

    def test_invalid_actions_are_errors_not_crashes(self):
        for payload in ({"action": "formatar"}, {"action": "kill"}, {"action": "kill", "nodes": "S"},
                        {"action": "kill", "node": "nao_existe"}, {"action": "netem", "node": "S"},
                        {"action": "link_netem", "a": "r1"}):
            with self.subTest(payload=payload):
                status, data = self.post(payload)
                self.assertEqual(status, 400)
                self.assertFalse(json.loads(data)["ok"])

    def test_link_netem_passes_the_parameters(self):
        status, _ = self.post({"action": "link_netem", "a": "r4", "ia": "eth1", "b": "r3", "ib": "eth2",
                               "delay": "30ms", "jitter": "", "loss": 5})
        self.assertEqual(status, 200)
        self.assertEqual(self.orch.calls[-1], ("link_netem", "r4", "eth1", "r3", "eth2",
                                               {"delay": "30ms", "jitter": None, "loss": "5", "rate": None}))

    def test_do_action_rejects_bad_node_lists(self):
        with self.assertRaises(BackendError):
            do_action(self.orch, {"action": "kill", "nodes": [1, 2]})


if __name__ == "__main__":
    unittest.main()
