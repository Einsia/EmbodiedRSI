"""Evidence retention and local Stop authorization; never touches hardware."""

import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from integrations.blupe.monitor import handler_for
from integrations.blupe.worker import TraceMirror, stop_and_archive_agent
from orchestrator.session import ExecutionTimeout


class EvidenceTests(unittest.TestCase):
    def test_controller_interrupt_during_shutdown_still_archives_trace_and_usage(self):
        with tempfile.TemporaryDirectory() as root:
            home, run = Path(root) / "home", Path(root) / "run"
            source = home / ".codex/sessions/rollout.jsonl"
            source.parent.mkdir(parents=True)
            source.write_text(json.dumps({
                "type": "event_msg", "payload": {
                    "type": "token_count", "info": {
                        "total_token_usage": {"input_tokens": 17, "output_tokens": 3, "total_tokens": 20}
                    }
                }
            }) + "\n")
            with patch("integrations.blupe.worker.stop_agent", side_effect=ExecutionTimeout("cancelled")):
                with self.assertRaises(ExecutionTimeout):
                    stop_and_archive_agent(None, "unused", TraceMirror(home, run), run, home)
            self.assertEqual((run / "agent/rollouts/rollout.jsonl").read_bytes(), source.read_bytes())
            self.assertEqual(json.loads((run / "model-usage.json").read_text())["total_tokens"], 20)

    def test_rollout_survives_disposable_home_and_append_is_exact(self):
        with tempfile.TemporaryDirectory() as root:
            home, run = Path(root)/'home', Path(root)/'run'
            source=home/'.codex/sessions/2026/10/19/rollout.jsonl'
            source.parent.mkdir(parents=True)
            source.write_bytes(b'{"event":"first"}\n')
            (home/'.codex/auth.json').write_text('private credential')
            mirror=TraceMirror(home,run)
            mirror.sync();mirror.sync()
            with source.open('ab') as stream:
                stream.write(b'{"event":"second"}\n')
            mirror.sync()
            saved=run/'agent/rollouts/2026/10/19/rollout.jsonl'
            expected=source.read_bytes()
            source.unlink()
            self.assertEqual(saved.read_bytes(),expected)
            self.assertFalse(list(run.rglob('auth.json')))
            self.assertEqual(json.loads((run/'agent/traces.json').read_text())['bytes'],len(expected))

    def test_monitor_stop_requires_own_origin_and_token(self):
        with tempfile.TemporaryDirectory() as root:
            run=Path(root)
            (run/'controller-status.json').write_text('{"status":"running"}')
            server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(run,'test-token'))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            origin=f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(origin+'/api/state') as response:
                    self.assertEqual(json.load(response)['controller']['status'],'running')
                for headers in ({},{'X-Run-Token':'test-token','Origin':'https://unrelated.example'}):
                    with self.assertRaises(HTTPError) as error:
                        urlopen(Request(origin+'/api/stop',data=b'{}',headers=headers))
                    self.assertEqual(error.exception.code,403)
                self.assertFalse((run/'stop-request.json').exists())
                headers={'X-Run-Token':'test-token','Origin':origin}
                with urlopen(Request(origin+'/api/stop',data=b'{}',headers=headers)) as response:
                    self.assertEqual(response.status,202)
                self.assertEqual(json.loads((run/'stop-request.json').read_text())['source'],'local_ui')
            finally:
                server.shutdown();server.server_close();thread.join()
