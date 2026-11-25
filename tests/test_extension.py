"""The extension's process as a node runs it, through the SDK's stand-in node, with no Open Design built: it speaks
the typed protocol, its status says what's missing, and a design asks to be tried again once it's there. Skipped
where $POINTMAN_EXTENSIONS_SDK doesn't name the node's Python SDK."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SDK = os.environ.get("POINTMAN_EXTENSIONS_SDK")
if SDK:
    sys.path.insert(0, SDK)
try:
    import pointman_extension_types as types
    from pointman_extension_testing import ExtensionFailed, StandInNode
except ImportError:
    StandInNode = None


@unittest.skipUnless(StandInNode, "needs the node's Python SDK ($POINTMAN_EXTENSIONS_SDK)")
class NotBuiltTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.missing = Path(self.tmp.name) / "open-design"
        self.node = StandInNode(ROOT, settings={"path": str(self.missing)}, state_dir=self.tmp.name)
        self.node.start()

    def tearDown(self):
        self.node.stop()
        self.tmp.cleanup()

    def test_it_speaks_the_typed_protocol(self):
        self.assertEqual(self.node.info.protocol, types.Protocol.PROTOCOL_TYPED)
        self.assertEqual(self.node.info.id, "opendesign")

    def test_its_status_says_open_design_isnt_built(self):
        for _ in range(100):
            if "opendesign.status" in self.node.events:
                break
            time.sleep(0.05)
        status = self.node.events["opendesign.status"]
        self.assertFalse(status["running"])
        self.assertEqual(status["reason"], f"no built Open Design at {self.missing}")

    def test_a_design_is_worth_asking_again_once_it_is_built(self):
        with self.assertRaises(ExtensionFailed) as failed:
            self.node.run("opendesign.make", {"brief": "A landing page"})
        self.assertTrue(failed.exception.retry)
        self.assertIn("isn't built", str(failed.exception))


# Open Design's daemon as far as a run needs it: the first run writes page.html, each revision page-v2.html beside
# it, the brief as its text, and the run's end names every page the project has, the first one first.
FAKE_OD = """#!{python}
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
DATA, projects = Path(os.environ["OD_DATA_DIR"]) / "projects", set()

class H(BaseHTTPRequestHandler):
    def answer(self, body, code=200):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.startswith("/api/projects/"):
            return self.answer({{}}, 200 if self.path.split("/")[-1] in projects else 404)
        if self.path.endswith("/events"):
            folder = DATA / self.path.split("/")[3]
            end = {{"status": "succeeded", "artifactPaths": [p.name for p in sorted(folder.glob("*.html"), key=lambda p: p.stat().st_mtime_ns)]}}
            raw = ("event: end\\ndata: " + json.dumps(end) + "\\n\\n").encode()
            self.send_response(200)
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            return self.wfile.write(raw)
        self.answer({{"ok": True}})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])) or b"{{}}")
        if self.path == "/api/projects":
            projects.add(body["id"])
        elif self.path == "/api/runs":
            folder = DATA / body["projectId"]
            folder.mkdir(parents=True, exist_ok=True)
            name = "page-v2.html" if (folder / "page.html").exists() else "page.html"
            (folder / name).write_text(body["pluginInputs"]["prompt"])
            return self.answer({{"runId": body["projectId"]}})
        self.answer({{}})

    def log_message(self, *a):
        pass

HTTPServer(("127.0.0.1", int(sys.argv[sys.argv.index("--port") + 1])), H).serve_forever()
"""


@unittest.skipUnless(StandInNode, "needs the node's Python SDK ($POINTMAN_EXTENSIONS_SDK)")
class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        (tmp / "od" / "apps" / "daemon" / "dist").mkdir(parents=True)
        (tmp / "od" / "apps" / "daemon" / "dist" / "cli.js").touch()
        (tmp / "bin").mkdir()
        for name, text in (("fake-od", FAKE_OD.format(python=sys.executable)), ("claude", "#!/bin/sh\n")):
            (tmp / "bin" / name).write_text(text)
            (tmp / "bin" / name).chmod(0o755)
        self.path = os.environ["PATH"]
        os.environ["PATH"] = f"{tmp / 'bin'}{os.pathsep}{self.path}"
        self.posts = []
        posted = lambda r: self.posts.append((r.channel.id, r.text)) or types.BoardPostResponse()  # noqa: E731
        self.node = StandInNode(ROOT, settings={"path": str(tmp / "od"), "node": str(tmp / "bin" / "fake-od"),
                                                "still": False}, state_dir=tmp, methods={"board.post": posted})
        self.node.start()

    def tearDown(self):
        self.node.stop()
        os.environ["PATH"] = self.path
        self.tmp.cleanup()

    def test_a_revision_publishes_the_page_it_wrote_and_an_unchanged_one_nothing(self):
        first = self.node.run("opendesign.make", {"brief": "A landing page"})
        project = first.result["project"]
        self.assertEqual([Path(o.path).read_text() for o in first.outputs], ["A landing page"])

        second = self.node.run("opendesign.make", {"brief": "Bigger title", "project": project})
        self.assertEqual([Path(o.path).name for o in second.outputs], ["page-v2.html"])
        self.assertEqual(Path(second.outputs[0].path).read_text(), "Bigger title")
        self.assertTrue(second.result["revised"])

        self.assertEqual(self.posts, [])
        third = self.node.run("opendesign.make", {"brief": "Bigger title", "project": project, "channel_id": 412})
        self.assertEqual(third.outputs, [])
        self.assertTrue(third.result["unchanged"])
        self.assertIn("unchanged", third.progress[-1].stage)
        self.assertEqual(self.posts, [("ch_412", "Open Design left the page as it was: no new version.")])
        self.assertTrue(self.node.manifest["permissions"]["post"])     # the node refuses board.post without it


if __name__ == "__main__":
    unittest.main()
