import socket
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone

from serve import make_server


def _now_ms():
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def fake_collect():
    now = _now_ms()
    return {
        "read_at": now,
        "sessions": [
            {
                "machine_id": "local",
                "machine": "This box",
                "repo": "central_command",
                "display_name": "central_command",
                "agent_type": "grok",
                "agent_state": "working",
                "prompt": "Planning the desk.",
                "last_message": "",
                "tool_name": "",
                "last_output_at": now,
                "in_turn": True,
                "live": True,
                "archived": False,
            }
        ],
        "machines": [
            {"id": "local", "label": "This box", "online": True, "read_ok": True, "error": None},
            {"id": "fedora", "label": "Fedora", "online": True, "read_ok": True, "error": None},
            {"id": "mac-mini", "label": "Mac mini", "online": True, "read_ok": True, "error": None},
        ],
    }


class ServeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server(fake_collect, 0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=3)

    def test_binds_loopback_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        for address in _other_addresses():
            sock = socket.socket()
            sock.settimeout(0.4)
            try:
                with self.assertRaises(OSError):
                    sock.connect((address, self.port))
            finally:
                sock.close()

    def test_health_and_page(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as response:
            self.assertEqual(response.read(), b"ok\n")
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=2) as response:
            page = response.read().decode("utf-8")
        self.assertIn("central_command", page)
        self.assertIn("<h1>Sessions</h1>", page)
        self.assertIn("Working", page)
        self.assertIn("Planning the desk.", page)
        self.assertIn(">Kanban<", page)
        self.assertIn("/partial", page)
        self.assertNotIn("Needs you", page)
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/partial", timeout=2) as response:
            partial = response.read().decode("utf-8")
        self.assertIn("central_command", partial)
        self.assertIn('class="kanban"', partial)
        self.assertNotIn("<html", partial)

    def test_raw_snapshot_is_not_served(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/snapshot.json", timeout=2)
        self.assertEqual(caught.exception.code, 404)
        caught.exception.close()

    def test_post_is_refused(self):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/", data=b"{}", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 405)
        caught.exception.close()


def _other_addresses():
    addresses = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                addresses.append(ip)
    except socket.gaierror:
        pass
    return addresses


if __name__ == "__main__":
    unittest.main()
