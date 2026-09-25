import importlib.util
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "runninghub" / "scripts" / "rhtv.py"
SPEC = importlib.util.spec_from_file_location("rhtv", SCRIPT)
rhtv = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rhtv)


class Handler(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *_args):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.__class__.calls.append((self.path, body, self.headers.get("Authorization")))
        if self.path == "/canvas/getCanvasDetail":
            data = {
                "name": "Demo",
                "canvas": json.dumps({
                    "nodes": [
                        {"id": "n1", "type": "rh-image", "data": {"params": {"prompt": "old"}}},
                        {"id": "n2", "type": "rh-video", "data": {"label": "Video"}},
                    ],
                    "edges": [{"source": "n1", "target": "n2"}],
                }),
            }
        elif self.path == "/canvas/task/run":
            data = {"taskId": "task-123"}
        elif self.path == "/canvas/task/batchStatus":
            data = {"tasks": [{"taskId": "task-123", "status": "SUCCESS"}]}
        elif self.path == "/canvas/task/cancel":
            data = {"taskId": "task-123", "status": "CANCELED"}
        else:
            self.send_response(404)
            self.end_headers()
            return
        encoded = json.dumps({"code": 0, "data": data}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class RHTVTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.env = {
            "RHTV_ACCESS_TOKEN": "test-token",
            "RHTV_API_BASE_URL": f"http://127.0.0.1:{cls.server.server_port}",
        }

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Handler.calls.clear()

    def test_parse_canvas_url(self):
        value = "https://rhtv.runninghub.ai/project/canvas/2103485063252774914"
        self.assertEqual(rhtv.parse_canvas_id(value), "2103485063252774914")
        with self.assertRaises(rhtv.RHTVError):
            rhtv.parse_canvas_id("https://www.runninghub.ai/ai-detail/123")

    def test_info_and_run_node(self):
        with patch.dict(os.environ, self.env, clear=False):
            detail = rhtv.get_canvas_detail("210")
            graph = rhtv.extract_graph(detail)
            summary = rhtv.summarize_canvas("210", detail, graph)
            self.assertEqual(summary["nodeCount"], 2)
            result = rhtv.run_node("210", "n1", ["n1:params.prompt=new prompt", "n1:generateNum=2"])
        self.assertEqual(result["taskId"], "task-123")
        run_call = next(call for call in Handler.calls if call[0] == "/canvas/task/run")
        node = run_call[1]["canvas"]["nodes"][0]
        self.assertEqual(node["data"]["params"]["prompt"], "new prompt")
        self.assertEqual(node["data"]["generateNum"], 2)
        self.assertEqual(run_call[2], "Bearer test-token")

    def test_status_wait_and_cancel(self):
        with patch.dict(os.environ, self.env, clear=False):
            waited = rhtv.wait_for_task("task-123", timeout=2, poll_interval=1)
            cancelled = rhtv.post_json("/canvas/task/cancel", {"taskId": "task-123"})
        self.assertEqual(rhtv.status_name(waited), "SUCCESS")
        self.assertEqual(cancelled["status"], "CANCELED")


if __name__ == "__main__":
    unittest.main()
