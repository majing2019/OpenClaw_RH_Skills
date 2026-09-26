import importlib.util
import json
import os
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "runninghub" / "scripts" / "catalog_server.py"
SPEC = importlib.util.spec_from_file_location("catalog_server", SCRIPT)
catalog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(catalog)


class CatalogServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), catalog.CatalogHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, path):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, response.getheader("Content-Type"), body

    def test_serves_html_and_capabilities(self):
        status, content_type, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn("RunningHub 能力浏览器".encode(), body)
        status, _, body = self.request("/api/capabilities")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["total"], len(data["endpoints"]))

    def test_status_never_returns_secrets(self):
        with patch.dict(os.environ, {"RUNNINGHUB_API_KEY": "secret-key"}):
            status, _, body = self.request("/api/status")
        self.assertEqual(status, 200)
        text = body.decode()
        self.assertNotIn("secret-key", text)
        data = json.loads(text)
        self.assertTrue(data["apiConfigured"])
        self.assertTrue(data["workflowConfigured"])

    def test_cover_path_traversal_is_rejected(self):
        status, _, _ = self.request("/api/covers/..%2Fsecret.png")
        self.assertEqual(status, 400)

    def test_apps_delegate_to_existing_helper(self):
        fake = {"sort": "RECOMMEND", "page": 1, "apps": [{"title": "Demo", "coverFile": "/tmp/openclaw/rh-output/app_covers/demo.png"}]}
        with patch.object(catalog, "run_json_command", return_value=fake) as command:
            catalog.STATE.app_cache.clear()
            status, _, body = self.request("/api/apps?sort=RECOMMEND&size=12&page=1")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["apps"][0]["coverUrl"], "/api/covers/demo.png")
        self.assertNotIn("coverFile", data["apps"][0])
        self.assertIn("runninghub_app.py", " ".join(command.call_args.args[0]))

    def test_workflow_info_delegates_to_official_helper(self):
        fake = {"workflowId": "1904136902449209346", "nodeCount": 1, "nodes": []}
        with patch.object(catalog, "run_json_command", return_value=fake) as command:
            status, _, body = self.request("/api/workflows/1904136902449209346")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["workflowId"], "1904136902449209346")
        args = command.call_args.args[0]
        self.assertIn("runninghub_workflow.py", " ".join(args))
        self.assertEqual(args[-2:], ["--info", "1904136902449209346"])

    def test_rhtv_catalog_delegates_to_live_read_only_helper(self):
        fake = {"source": "RHTV live catalog", "total": 2, "count": 2, "workflows": []}
        with patch.object(catalog, "run_json_command", return_value=fake) as command:
            catalog.STATE.rhtv_cache = None
            status, _, body = self.request("/api/rhtv/workflows")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["total"], 2)
        self.assertIn("rhtv_catalog.py", " ".join(command.call_args.args[0]))
        self.assertEqual(command.call_args.args[0][-1], "--list")

    def test_rhtv_refresh_bypasses_catalog_cache(self):
        old = {"total": 1, "count": 1, "workflows": []}
        fresh = {"total": 2, "count": 2, "workflows": []}
        catalog.STATE.rhtv_cache = (catalog.time.monotonic(), old)
        with patch.object(catalog, "run_json_command", return_value=fresh) as command:
            status, _, body = self.request("/api/rhtv/workflows?refresh=1")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["total"], 2)
        command.assert_called_once()

    def test_rhtv_detail_validates_and_delegates(self):
        fake = {"id": "353", "name": "Demo", "nodes": []}
        with patch.object(catalog, "run_json_command", return_value=fake) as command:
            status, _, body = self.request("/api/rhtv/workflows/353")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["id"], "353")
        self.assertEqual(command.call_args.args[0][-2:], ["--info", "353"])
        status, _, _ = self.request("/api/rhtv/workflows/not-a-number")
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
