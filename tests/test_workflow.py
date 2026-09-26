import importlib.util
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "runninghub" / "scripts" / "runninghub_workflow.py"
SPEC = importlib.util.spec_from_file_location("runninghub_workflow", SCRIPT)
workflow = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workflow)


class Handler(BaseHTTPRequestHandler):
    calls = []
    output_polls = 0

    def log_message(self, *_args):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.__class__.calls.append((self.path, body, self.headers.get("Authorization")))
        if self.path == "/api/openapi/getJsonApiFormat":
            prompt = {
                "3": {
                    "class_type": "KSampler",
                    "inputs": {"seed": 1, "model": ["4", 0]},
                    "_meta": {"title": "Sampler"},
                },
                "6": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {"text": "old prompt", "clip": ["4", 1]},
                    "_meta": {"title": "Positive prompt"},
                },
            }
            response = {"code": 0, "msg": "SUCCESS", "data": {"prompt": json.dumps(prompt)}}
        elif self.path == "/task/openapi/create":
            response = {"code": 0, "msg": "success", "data": {"taskId": "task-123", "taskStatus": "QUEUED"}}
        elif self.path == "/task/openapi/outputs":
            self.__class__.output_polls += 1
            if self.__class__.output_polls == 1:
                response = {"code": 813, "msg": "queued", "data": None}
            else:
                response = {
                    "code": 0,
                    "msg": "success",
                    "data": [{"fileUrl": "https://example.invalid/result.png", "fileType": "png"}],
                }
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


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.env = {
            "RUNNINGHUB_WORKFLOW_API_BASE_URL": f"http://127.0.0.1:{cls.server.server_port}",
        }

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Handler.calls.clear()
        Handler.output_polls = 0

    def test_info_uses_official_api_key_and_summarizes_nodes(self):
        with patch.dict(os.environ, self.env, clear=False):
            prompt = workflow.get_workflow("test-api-key", "1904136902449209346")
        summary = workflow.summarize_workflow("1904136902449209346", prompt)
        self.assertEqual(summary["nodeCount"], 2)
        self.assertEqual(summary["nodes"][1]["title"], "Positive prompt")
        self.assertFalse(summary["nodes"][1]["fields"][1]["modifiable"])
        self.assertEqual(Handler.calls[0][2], "Bearer test-api-key")

    def test_submit_overrides_and_waits_for_outputs(self):
        args = SimpleNamespace(
            instance_type="plus",
            access_password=None,
            retain_seconds=60,
        )
        with patch.dict(os.environ, self.env, clear=False):
            node_info = workflow.build_node_info("test-api-key", ["6:text=new prompt", "3:seed=42"], None)
            task_id = workflow.submit_workflow("test-api-key", "1904136902449209346", node_info, args)
            outputs = workflow.wait_for_outputs("test-api-key", task_id, timeout=2, poll_interval=0)
        self.assertEqual(task_id, "task-123")
        self.assertEqual(outputs[0]["fileType"], "png")
        submit = next(call for call in Handler.calls if call[0] == "/task/openapi/create")
        self.assertEqual(submit[1]["nodeInfoList"][0]["fieldValue"], "new prompt")
        self.assertEqual(submit[1]["nodeInfoList"][1]["fieldValue"], 42)
        self.assertEqual(submit[1]["instanceType"], "plus")
        self.assertEqual(submit[1]["retainSeconds"], 60)

    def test_canvas_url_is_not_accepted_as_workflow_id(self):
        with self.assertRaises(workflow.WorkflowError):
            workflow.validate_id("https://rhtv.runninghub.ai/project/canvas/2103485063252774914", "workflowId")


if __name__ == "__main__":
    unittest.main()
