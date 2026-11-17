# Validação das opções da linha de comandos (sem abrir sockets).
# PYTHONPATH=src python3 -m unittest tests.test_cli -v
import contextlib
import io
import unittest

from ott import cli


class RouteTtlOptionTest(unittest.TestCase):
    def assert_rejected(self, *argv):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(list(argv))

    def test_ttl_must_exceed_announce_interval(self):
        self.assert_rejected("--route-ttl", "5", "--announce-interval", "5")
        self.assert_rejected("--route-ttl", "2", "--announce-interval", "5")

    def test_ttl_must_be_finite(self):
        self.assert_rejected("--route-ttl", "inf")
        self.assert_rejected("--route-ttl", "nan")

    def test_announce_interval_is_validated_on_every_node(self):
        self.assert_rejected("--announce-interval", "0")

    def test_parser_default_leaves_ttl_derived_from_interval(self):
        args = cli.build_parser().parse_args([])
        self.assertIsNone(args.route_ttl)
        self.assertEqual(args.announce_interval, 5.0)


class LiveOptionTest(unittest.TestCase):
    def test_live_defaults_to_two_seconds(self):
        parser = cli.build_parser()
        self.assertIsNone(parser.parse_args([]).live)
        self.assertEqual(parser.parse_args(["--live"]).live, 2.0)
        self.assertEqual(parser.parse_args(["--live", "0.5"]).live, 0.5)

    def test_live_interval_is_validated(self):
        for value in ("0", "-1", "nan", "inf"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                cli.main(["--live", value])

    def test_log_dir_number_can_be_the_last_argument(self):
        args = cli.build_parser().parse_args(
            ["-i", "O5", "-b", "10.0.18.20", "--live", "--log-dir", "/x/2026-11-17-3"])
        self.assertEqual((args.live, args.log_dir, args.neighbors),
                         (2.0, "/x/2026-11-17-3", []))


class GuiOptionTest(unittest.TestCase):
    def test_gui_needs_a_subscription(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(["--gui"])

    def test_gui_is_off_by_default(self):
        self.assertFalse(cli.build_parser().parse_args([]).gui)


class PublishFlowOptionTest(unittest.TestCase):
    def assert_rejected(self, *argv):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(list(argv))

    def test_empty_flow_or_video_is_rejected(self):
        self.assert_rejected("--publish-flow", "=filme.Mjpeg")
        self.assert_rejected("--publish-flow", "filme_a=")

    def test_same_flow_twice_is_rejected(self):
        self.assert_rejected("--publish-flow", "filme_a", "--publish-flow", "filme_a=outro")

    def test_missing_video_file_is_rejected(self):
        self.assert_rejected("--publish-flow", "filme_b=/nao/existe.Mjpeg")

    def test_flow_with_video_is_accepted_by_the_parser(self):
        args = cli.build_parser().parse_args(
            ["--publish-flow", "filme_a", "--publish-flow", "filme_b=b.Mjpeg"])
        self.assertEqual(args.publish_flow, ["filme_a", "filme_b=b.Mjpeg"])


if __name__ == "__main__":
    unittest.main()
