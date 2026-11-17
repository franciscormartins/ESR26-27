# Vista compacta da consola (ott --live): linha de estado e trocas de montante.
# PYTHONPATH=src python3 -m unittest tests.test_live -v
import io
import logging
import unittest

from ott.forwarding import TrafficStats
from ott.live import LiveConsole, LiveView, TrafficView
from ott.streaming.rtp import RtpPacket


class FakeTable:
    def __init__(self):
        self.paths = {}       # fluxo -> (montante, saltos, atraso)
        self.dests = {}
        self.local = set()

    def path_info(self, origin, flow):
        return self.paths.get((origin, flow))

    def active_destinations(self, origin, flow, exclude=()):
        return tuple(self.dests.get((origin, flow), ()))

    def has_local_subscription(self, origin, flow):
        return (origin, flow) in self.local


class FakeForwarder:
    def __init__(self):
        self.traffic = TrafficStats()


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class TtyStream(io.StringIO):
    def isatty(self):
        return True


def receive(fwd, nb, seqs, flow=("O1", "filme_a")):
    for seq in seqs:
        fwd.traffic.accepted(*flow, nb, RtpPacket(seq, 0, b"", ssrc=1), 0.0)


KEY = ("O1", "filme_a")


class TrafficViewTest(unittest.TestCase):
    def setUp(self):
        self.table, self.fwd, self.clock = FakeTable(), FakeForwarder(), Clock()
        self.view = TrafficView("O5", self.fwd, self.table, clock=self.clock,
                                wall=lambda: 0.0)

    def test_block_has_total_rate_neighbor_distance_and_destinations(self):
        self.table.paths[KEY] = ("O3", 3, 0.0012)
        self.table.dests[KEY] = ("O10", "O11")
        receive(self.fwd, "O3", range(1, 11))
        self.view.render()
        receive(self.fwd, "O3", range(11, 51))
        self.clock.t += 2
        lines = self.view.render()
        self.assertTrue(lines[0].startswith("── O5 · "))
        self.assertEqual(lines[1], "O1:filme_a      50 pacotes    20/s  0 perdidos")
        self.assertEqual(lines[2],
                         "   vem de O3 (3 saltos desde O1, 1.2 ms) → [O5] → O10, O11")
        self.assertEqual(len(lines), 3)

    def test_local_client(self):
        self.table.paths[KEY] = ("O7", 2, None)
        self.table.local.add(KEY)
        receive(self.fwd, "O7", [1])
        self.assertEqual(self.view.render()[2], "   vem de O7 (2 saltos desde O1) → [O5] (cliente)")

    def test_upstream_change_is_logged_and_shown(self):
        self.table.paths[KEY] = ("O7", 3, None)
        receive(self.fwd, "O7", range(1, 4))
        self.assertEqual(self.view.check_upstreams(), [])
        self.table.paths[KEY] = ("O3", 4, None)
        with self.assertLogs("ott.live", logging.INFO) as logs:
            self.assertEqual(self.view.check_upstreams(), [(KEY, "O7", "O3")])
        self.assertIn("passa a vir de O3 (4 saltos desde O1); antes vinha de O7", logs.output[0])
        receive(self.fwd, "O3", [6])           # 4 e 5 perdidos na troca
        lines = self.view.render()
        self.assertIn("2 perdidos", lines[1])
        self.assertEqual(lines[2], "   vem de O3 (4 saltos desde O1) → [O5]")
        self.assertRegex(lines[3], r"^   mudou às \d\d:\d\d:\d\d \(antes vinha de O7, 3 saltos\)$")

    def test_route_lost(self):
        self.table.paths[KEY] = ("O7", 3, None)
        receive(self.fwd, "O7", [1])
        self.view.check_upstreams()
        self.table.paths[KEY] = (None, None, None)
        with self.assertLogs("ott.live", logging.INFO) as logs:
            self.view.check_upstreams()
        self.assertIn("perdeu o caminho (vinha de O7)", logs.output[0])
        lines = self.view.render()
        self.assertEqual(lines[2], "   sem caminho até O1 (à espera de anúncios)")
        self.assertIn("antes vinha de O7", lines[3])

    def test_published_flow_and_pending_subscription(self):
        view = TrafficView("O1", self.fwd, self.table, published=["filme_a"],
                           subscriptions=[("S2", "filme_b")], clock=self.clock)
        self.table.dests[KEY] = ("O2",)
        self.fwd.traffic.sent("O1", "filme_a", "O2")
        lines = view.render()
        self.assertEqual(lines[1:3], ["O1:filme_a       1 enviados    --/s", "   origem [O1] → O2"])
        self.assertEqual(lines[4], "   sem caminho até S2 (à espera de anúncios)")

    def test_file_lines_one_per_flow(self):
        self.table.paths[KEY] = ("O3", 1, None)
        receive(self.fwd, "O3", [1])
        [line] = self.view.file_lines()
        self.assertEqual(line, "O1:filme_a       1 pacotes    --/s  0 perdidos | vem de O3 (1 salto desde O1) → [O5]")

    def test_no_flows(self):
        self.assertEqual(self.view.render()[1:], ["sem fluxos"])


class LiveConsoleTest(unittest.TestCase):
    def test_tty_redraws_in_place_and_logs_go_above(self):
        stream = TtyStream()
        console = LiveConsole(stream)
        console.setFormatter(logging.Formatter("%(message)s"))
        console.show(["a1", "a2"])
        console.show(["b1", "b2", "b3"])
        console.emit(logging.makeLogRecord({"msg": "evento", "levelno": logging.INFO}))
        console.finish()
        self.assertEqual(stream.getvalue(),
                         "a1\na2"                       # primeiro bloco
                         "\x1b[1A\r\x1b[Jb1\nb2\nb3"     # sobe 1 linha, apaga, redesenha
                         "\x1b[2A\r\x1b[Jevento\nb1\nb2\nb3"  # evento por cima do bloco
                         "\n")

    def test_without_tty_each_line_is_printed(self):
        stream = io.StringIO()
        console = LiveConsole(stream)
        console.show(["a1", "a2"])
        console.show(["b"])
        console.finish()
        self.assertEqual(stream.getvalue(), "a1\na2\nb\n")

    def test_view_thread_draws_and_stops(self):
        stream = io.StringIO()
        file_log = logging.getLogger("ott.test.live.ficheiro")
        file_log.propagate = False
        view = TrafficView("O5", FakeForwarder(), FakeTable())
        with self.assertLogs(file_log, logging.INFO) as logs:
            live = LiveView(view, LiveConsole(stream), interval=0.05,
                            file_logger=file_log, poll=0.01).start()
            try:
                for _ in range(200):
                    if stream.getvalue().count("\n") >= 2:
                        break
                    live._stop.wait(0.01)
            finally:
                live.stop()
        self.assertFalse(live._thread.is_alive())
        self.assertIn("── O5 · ", stream.getvalue())
        self.assertIn("sem fluxos", logs.output[0])


if __name__ == "__main__":
    unittest.main()
