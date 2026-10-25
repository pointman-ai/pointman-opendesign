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


if __name__ == "__main__":
    unittest.main()
