import socket
import unittest

from ott import protocol as proto


class ProtocolTest(unittest.TestCase):
    def test_roundtrip_and_eof(self):
        a, b = socket.socketpair()
        msg = proto.make_msg(proto.MsgType.HELLO, "O1", x=[1, 2])
        proto.send_msg(a, msg)
        self.assertEqual(proto.recv_msg(b), msg)
        a.close()
        self.assertIsNone(proto.recv_msg(b))
        b.close()


if __name__ == "__main__":
    unittest.main()
