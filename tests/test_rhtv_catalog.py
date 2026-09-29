import importlib.util
import json
import os
import tempfile
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
    thumbnail = f"https://cdn.example/{record_id}.{media}"
    return {
        "id": str(record_id),
        "name": name,
        "description": f"{name} description",
        "thumbnail": thumbnail,
        "updateTime": "2026-09-26T14:23:00.000+00:00",
        "workflowContent": {
            "nodes": [
                {"id": "group-1", "type": "group", "data": {}},
                {"id": "node-1", "type": "rh-image", "data": {"label": "Image", "subType": "text-image", "modelCode": "image-demo", "sourceObjects": [f"https://cdn.example/uploads/{record_id}.png"]}},
                {"id": "node-2", "type": "rh-video", "data": {"label": "Video", "subType": "image-video", "modelCode": "video-demo", "prompt": "人物在城市街道中完成自然转场。", "params": {"duration": "6", "aspectRatio": "9:16"}, "output": [{"url": thumbnail}]}},
                {"id": "node-unused", "type": "rh-audio", "data": {"label": "Unused", "subType": "text-audio", "modelCode": "audio-unused"}},
            ],
            "edges": [{"source": "node-1", "target": "node-2"}],
        },
    }


class Handler(BaseHTTPRequestHandler):
    calls = []
    records_by_page = None

    def log_message(self, *_args):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.__class__.calls.append((self.path, body))
        if self.path == "/canvas/common-workflow/list":
            page = int(body.get("page", 1))
            source = self.__class__.records_by_page or {
                1: [record(101, "First")], 2: [record(102, "Second", "png")]
            }
            records = source.get(page, [])
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
        Handler.records_by_page = None
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_env = {
            **self.env,
            "RHTV_CATALOG_DB_PATH": str(Path(self.temp_dir.name) / "catalog.sqlite3"),
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_lists_all_pages_and_compacts_workflow_graphs(self):
        with patch.dict(os.environ, self.test_env, clear=False), patch.object(catalog, "MAX_PAGE_SIZE", 1):
            data = catalog.list_all("demo")
        self.assertEqual(data["count"], 2)  # local search is applied after the complete sync
        with patch.dict(os.environ, self.test_env, clear=False):
            data = catalog.list_cached()
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["sync"]["created"], 2)
        self.assertEqual(data["workflows"][0]["nodeCount"], 2)
        self.assertEqual(data["workflows"][0]["canvasNodeCount"], 3)
        self.assertEqual(data["workflows"][0]["models"], ["image-demo", "video-demo"])
        self.assertEqual(data["workflows"][0]["outputTypes"], ["video"])
        self.assertEqual(data["workflows"][0]["inputs"][0]["type"], "image")
        self.assertEqual(data["workflows"][0]["outputs"][0]["type"], "video")
        self.assertIn("人物在城市街道中完成自然转场", data["workflows"][0]["chineseDescription"])
        self.assertEqual(data["workflows"][0]["inputSummary"], "需要：1 个图片；工作流内含 1 段原始文字指令。")
        self.assertEqual(data["workflows"][0]["mediaInputs"][0]["type"], "image")
        self.assertEqual(data["workflows"][0]["promptInputs"][0]["value"], "人物在城市街道中完成自然转场。")
        self.assertEqual(data["workflows"][0]["outputSettings"]["duration"], "6")
        self.assertNotIn("detailPath", data["workflows"][0])
        self.assertNotIn("workflowContent", data["workflows"][0])
        self.assertTrue(Path(self.test_env["RHTV_CATALOG_DB_PATH"]).is_file())

    def test_second_sync_only_records_unchanged_entries(self):
        with patch.dict(os.environ, self.test_env, clear=False), patch.object(catalog, "MAX_PAGE_SIZE", 1):
            catalog.sync_catalog()
            data = catalog.sync_catalog()
        self.assertEqual(data["sync"]["created"], 0)
        self.assertEqual(data["sync"]["updated"], 0)
        self.assertEqual(data["sync"]["unchanged"], 2)

    def test_cached_list_does_not_contact_remote_catalog(self):
        with patch.dict(os.environ, self.test_env, clear=False), patch.object(catalog, "MAX_PAGE_SIZE", 1):
            catalog.sync_catalog()
            Handler.calls.clear()
            data = catalog.list_cached()
        self.assertEqual(data["count"], 2)
        self.assertEqual(Handler.calls, [])

    def test_sync_inserts_new_updates_changed_and_deactivates_missing(self):
        with patch.dict(os.environ, self.test_env, clear=False), patch.object(catalog, "MAX_PAGE_SIZE", 1):
            catalog.sync_catalog()
            Handler.records_by_page = {
                1: [record(101, "Changed")],
                2: [record(103, "New")],
            }
            data = catalog.sync_catalog()
        self.assertEqual(data["sync"]["created"], 1)
        self.assertEqual(data["sync"]["updated"], 1)
        self.assertEqual(data["sync"]["deactivated"], 1)
        self.assertEqual({item["id"] for item in data["workflows"]}, {"101", "103"})

    def test_detail_returns_compact_nodes(self):
        with patch.dict(os.environ, self.test_env, clear=False):
            data = catalog.get_detail("101")
        self.assertEqual(data["previewType"], "video")
        self.assertEqual(len(data["nodes"]), 2)
        self.assertEqual(data["nodes"][1]["modelCode"], "video-demo")
        self.assertEqual(
            data["sourceUrl"],
            "https://rhtv.runninghub.ai/projects/canvas/camp"
            "?section=workflow&type=workflow&category=recommended",
        )

    def test_rejects_non_numeric_detail_id(self):
        with self.assertRaises(catalog.CatalogError):
            catalog.get_detail("https://rhtv.runninghub.ai/project/canvas/123")


if __name__ == "__main__":
    unittest.main()
