import importlib.util
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "runninghub" / "scripts" / "rhtv_catalog.py"
SPEC = importlib.util.spec_from_file_location("rhtv_catalog", SCRIPT)
catalog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(catalog)


def record(record_id, name, media="mp4"):
    return {
        "id": str(record_id),
        "name": name,
        "description": f"{name} description",
        "thumbnail": f"https://cdn.example/{record_id}.{media}",
        "updateTime": "2026-09-26T14:23:00.000+00:00",
        "workflowContent": {
            "nodes": [
                {"id": "group-1", "type": "group", "data": {}},
                {"id": "node-1", "type": "rh-image", "data": {"label": "Image", "subType": "text-image", "modelCode": "image-demo"}},
                {"id": "node-2", "type": "rh-video", "data": {"label": "Video", "subType": "image-video", "modelCode": "video-demo"}},
            ],
            "edges": [{"source": "node-1", "target": "node-2"}],
        },
    }


class Handler(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *_args):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.__class__.calls.append((self.path, body))
        if self.path == "/canvas/common-workflow/list":
            page = int(body.get("page", 1))
            records = [record(101, "First")] if page == 1 else [record(102, "Second", "png")]
            response = {"code": 0, "msg": "success", "data": {"records": records, "total": 2, "size": 1, "current": page, "pages": 2}}
        elif self.path == "/canvas/admin/common-workflow/detail":
            response = {"code": 0, "msg": "success", "data": record(body["id"], "Detail")}
        else:
            self.send_response(404)
            self.end_headers()
            return
        encoded = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class RHTVCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.env = {"RHTV_CATALOG_API_BASE_URL": f"http://127.0.0.1:{cls.server.server_port}"}

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Handler.calls.clear()

    def test_lists_all_pages_and_compacts_workflow_graphs(self):
        with patch.dict(os.environ, self.env, clear=False), patch.object(catalog, "MAX_PAGE_SIZE", 1):
            data = catalog.list_all("demo")
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["workflows"][0]["nodeCount"], 2)
        self.assertEqual(data["workflows"][0]["models"], ["image-demo", "video-demo"])
        self.assertEqual(data["workflows"][0]["outputTypes"], ["image", "video"])
        self.assertNotIn("workflowContent", data["workflows"][0])
        self.assertTrue(all(call[1]["keyword"] == "demo" for call in Handler.calls))

    def test_detail_returns_compact_nodes(self):
        with patch.dict(os.environ, self.env, clear=False):
            data = catalog.get_detail("101")
        self.assertEqual(data["previewType"], "video")
        self.assertEqual(len(data["nodes"]), 2)
        self.assertEqual(data["nodes"][1]["modelCode"], "video-demo")

    def test_rejects_non_numeric_detail_id(self):
        with self.assertRaises(catalog.CatalogError):
            catalog.get_detail("https://rhtv.runninghub.ai/project/canvas/123")


if __name__ == "__main__":
    unittest.main()
