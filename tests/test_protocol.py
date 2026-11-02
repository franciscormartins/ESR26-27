import json
import socket
import struct
import unittest

from ott import protocol as proto
from ott.protocol import MsgType, ProtocolError, make_msg


def raw_send(sock, obj):
    raw = json.dumps(obj).encode()
    sock.sendall(struct.pack("!I", len(raw)) + raw)


class ProtocolTest(unittest.TestCase):
    def test_roundtrip_and_eof(self):
        a, b = socket.socketpair()
        msg = proto.make_msg(proto.MsgType.HELLO, "O1", x=[1, 2])
        proto.send_msg(a, msg)
        self.assertEqual(proto.recv_msg(b), msg)
        a.close()
        self.assertIsNone(proto.recv_msg(b))
        b.close()

    def test_valid_messages(self):
        good = [
            make_msg(MsgType.HELLO, "A"),
            make_msg(MsgType.PING, "A", ts=1.5),
            make_msg(MsgType.PONG, "A", ts=3),
            make_msg(MsgType.REGISTER, "A"),
            make_msg(MsgType.NEIGHBORS, "A", neighbors=[{"id": "B", "host": "h", "port": 5}]),
            make_msg(MsgType.NEIGHBORS, "A", neighbors=[]),
            make_msg(MsgType.ERROR, "A", reason="x"),
            make_msg(MsgType.REPLACE, "A", have=["B"], lost=[], need=1),
            make_msg(MsgType.ANNOUNCE, "A", server_id="S1", flow="filme_a", epoch=1, metric=0),
            make_msg(MsgType.ACTIVATE, "C1", server_id="S1", flow="filme_a", subscriber_id="C1"),
            make_msg(MsgType.DEACTIVATE, "C1", server_id="S1", flow="filme_a", subscriber_id="C1"),
        ]
        for m in good:
            self.assertIs(proto.validate(m), m)

    def test_invalid_messages(self):
        bad = [
            "texto",
            [],
            {"src": "A", "data": {}},                                   # sem type
            {"type": "NAO_EXISTE", "src": "A", "data": {}},             # tipo desconhecido
            {"type": "HELLO", "data": {}},                              # sem src
            {"type": "HELLO", "src": "", "data": {}},                   # src vazio
            {"type": "HELLO", "src": "A"},                              # sem data
            {"type": "HELLO", "src": "A", "data": []},                  # data não é objeto
            make_msg(MsgType.PING, "A"),                                # falta ts
            make_msg(MsgType.PING, "A", ts="agora"),                    # ts não numérico
            make_msg(MsgType.PING, "A", ts=True),                       # bool não é número
            make_msg(MsgType.NEIGHBORS, "A", neighbors="B"),
            make_msg(MsgType.NEIGHBORS, "A", neighbors=[{"id": "B"}]),  # sem host/port
            make_msg(MsgType.NEIGHBORS, "A", neighbors=[{"id": "B", "host": "h", "port": "5"}]),
            make_msg(MsgType.ERROR, "A"),
            make_msg(MsgType.REPLACE, "A", have=["B"], lost=[]),        # falta need
            make_msg(MsgType.REPLACE, "A", have=["B"], lost=[], need=0),
            make_msg(MsgType.REPLACE, "A", have="B", lost=[], need=1),
            make_msg(MsgType.ANNOUNCE, "A", server_id="S1", flow="filme_a", epoch=1),
            make_msg(MsgType.ANNOUNCE, "A", server_id="S1", flow="filme_a", epoch=0, metric=0),
            make_msg(MsgType.ANNOUNCE, "A", server_id="S1", flow="filme_a", epoch=1, metric=-1),
            make_msg(MsgType.ANNOUNCE, "A", server_id="S1", flow="filme_a", epoch=1, metric=float("nan")),
            make_msg(MsgType.ACTIVATE, "C1", server_id="S1", flow="filme_a"),
            make_msg(MsgType.DEACTIVATE, "C1", server_id="S1", flow="filme_a", subscriber_id=""),
        ]
        for m in bad:
            with self.assertRaises(ProtocolError, msg=repr(m)):
                proto.validate(m)

    def test_send_refuses_invalid(self):
        a, b = socket.socketpair()
        with self.assertRaises(ProtocolError):
            proto.send_msg(a, make_msg(MsgType.PING, "A"))
        a.close()
        b.close()

    def test_recv_rejects_invalid_content(self):
        for payload in ({"type": "PING", "src": "A", "data": {}}, [1, 2], "x"):
            a, b = socket.socketpair()
            raw_send(a, payload)
            with self.assertRaises(ProtocolError):
                proto.recv_msg(b)
            a.close()
            b.close()

    def test_recv_rejects_bad_json_and_oversize(self):
        a, b = socket.socketpair()
        a.sendall(struct.pack("!I", 3) + b"{{{")
        with self.assertRaises(ProtocolError):
            proto.recv_msg(b)
        a.sendall(struct.pack("!I", proto.MAX_MSG_SIZE + 1))
        with self.assertRaises(ProtocolError):
            proto.recv_msg(b)
        a.close()
        b.close()

    def test_truncated_message_is_eof(self):
        a, b = socket.socketpair()
        a.sendall(struct.pack("!I", 50) + b"{")
        a.close()
        self.assertIsNone(proto.recv_msg(b))
        b.close()


if __name__ == "__main__":
    unittest.main()
