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


if __name__ == "__main__":
    unittest.main()
