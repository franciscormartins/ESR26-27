# PYTHONPATH=src python3 -m unittest tests.test_streaming -v
import os
import tempfile
import time
import unittest

from ott.streaming.client import StreamReceiver, StreamStats
from ott.streaming.rtp import (PacketError, RtpPacket, pack_data, seq_newer,
                               unpack_data)
from ott.streaming.server import MjpegFile, StreamSource, udp_sender

MOVIE = os.path.join(os.path.dirname(__file__), "..", "progex", "movie.Mjpeg")


def write_mjpeg(frames):
    f = tempfile.NamedTemporaryFile(delete=False, suffix=".Mjpeg")
    for frame in frames:
        f.write(b"%05d" % len(frame) + frame)
    f.close()
    return f.name


class PacketTest(unittest.TestCase):
    def test_roundtrip(self):
        rtp = RtpPacket(65535, 123456, b"jpeg").encode()
        origin, flow, raw = unpack_data(pack_data("S1", "filme_a", rtp))
        self.assertEqual((origin, flow), ("S1", "filme_a"))
        back = RtpPacket.decode(raw)
        self.assertEqual((back.seq, back.timestamp, back.payload, back.pt),
                         (65535, 123456, b"jpeg", 26))

    def test_rejects_garbage(self):
        for bad in (b"", b"XX\x01\x02S1", b"OT\x09\x02S1\x01f", b"OT\x01\x05S1"):
            with self.assertRaises(PacketError):
                unpack_data(bad)
        with self.assertRaises(PacketError):
            RtpPacket.decode(b"\x00" * 12)            # versão RTP 0

    def test_seq_wraparound(self):
        self.assertTrue(seq_newer(1, 65535))
        self.assertTrue(seq_newer(10, 9))
        self.assertFalse(seq_newer(9, 10))
        self.assertFalse(seq_newer(5, 5))


class StatsTest(unittest.TestCase):
    def test_loss_and_late(self):
        s = StreamStats()
        for seq in (1, 2, 5, 4, 6):                    # 3 nunca chega, 4 chega tarde
            s.update(RtpPacket(seq, seq * 4500, b""), seq * 0.05)
        self.assertEqual((s.received, s.lost, s.late), (4, 2, 1))
        self.assertAlmostEqual(s.jitter, 0.0, places=6)  # ritmo certinho: sem jitter

    def test_new_ssrc_restarts_sequence(self):
        s = StreamStats()
        for seq in range(1, 101):                      # 1.ª emissão até ao 100
            s.update(RtpPacket(seq, seq * 4500, b"", ssrc=111), seq * 0.05)
        shown = [s.update(RtpPacket(seq, seq * 4500, b"", ssrc=222), 10 + seq * 0.05)
                 for seq in range(1, 6)]               # servidor reiniciou
        self.assertEqual(shown, [True] * 5)            # não são "atrasados"
        self.assertEqual((s.received, s.lost, s.late, s.restarts), (105, 0, 0, 1))


class MjpegTest(unittest.TestCase):
    def test_reads_and_loops(self):
        path = write_mjpeg([b"a" * 3, b"bb"])
        try:
            v = MjpegFile(path, loop=True)
            self.assertEqual([v.next_frame() for _ in range(5)],
                             [b"aaa", b"bb", b"aaa", b"bb", b"aaa"])
            v.close()
            v = MjpegFile(path, loop=False)
            self.assertEqual([v.next_frame() for _ in range(3)], [b"aaa", b"bb", None])
            v.close()
        finally:
            os.unlink(path)

    @unittest.skipUnless(os.path.exists(MOVIE), "movie.Mjpeg não encontrado")
    def test_movie_has_500_frames(self):
        v = MjpegFile(MOVIE, loop=False)
        n = 0
        while v.next_frame() is not None:
            n += 1
        v.close()
        self.assertEqual(n, 500)


class LoopbackTest(unittest.TestCase):
    def test_server_to_client_over_udp(self):
        path = write_mjpeg([b"frame%d" % i for i in range(10)])
        frames = []
        rx = StreamReceiver("S1", "filme_a", port=0, bind="127.0.0.1",
                            on_frame=lambda f, rtp: frames.append(f)).start()
        send = udp_sender([("127.0.0.1", rx.port)])
        other = StreamSource("S1", "filme_b", path, send, fps=200, loop=False).start()
        src = StreamSource("S1", "filme_a", path, send, fps=200, loop=False).start()
        try:
            src.join(5)
            other.join(5)
            deadline = time.time() + 2
            while len(frames) < 10 and time.time() < deadline:
                time.sleep(0.01)
            self.assertEqual(frames, [b"frame%d" % i for i in range(10)])  # só filme_a
            self.assertEqual(rx.stats.lost, 0)
            # o mesmo fluxo emitido outra vez (servidor reiniciado) também é mostrado
            again = StreamSource("S1", "filme_a", path, send, fps=200, loop=False).start()
            again.join(5)
            deadline = time.time() + 2
            while len(frames) < 20 and time.time() < deadline:
                time.sleep(0.01)
            self.assertEqual(len(frames), 20)
            self.assertEqual((rx.stats.late, rx.stats.restarts), (0, 1))
        finally:
            rx.stop()
            send.sock.close()
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
