"""Run the upstream local UI with the configured Astra endpoint and credential."""

import argparse
import importlib.util
import json
import os
import sys
import tomllib
from pathlib import Path

from integrations.blupe import knowledge, upstream
from integrations.blupe.responses import compatible_opener
from orchestrator.files import write_json
from orchestrator.launch_agent import agent_codex_home


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--port", type=int, default=8788)
    ap.add_argument("--model", default="gpt-6-astra")
    args = ap.parse_args()
    run = knowledge.new_run(args.run_dir)
    upstream.load()
    home = agent_codex_home()
    config = tomllib.loads((home / "config.toml").read_text())
    provider = config["model_providers"][config["model_provider"]]
    endpoint = provider["base_url"].rstrip("/") + "/responses"
    key = json.loads((home / "auth.json").read_text())["OPENAI_API_KEY"]
    if not key:
        raise ValueError("Configured model key is missing")
    os.environ["ASTRA_API_KEY"] = key
    os.environ["ASTRA_ENDPOINT"] = endpoint
    os.environ["ASTRA_MODEL"] = args.model
    os.environ.setdefault("MUJOCO_GL", "egl")
    path = upstream.CHECKOUT / "codex-runner/run.py"
    spec = importlib.util.spec_from_file_location("blupe_local_ui", path)
    ui = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = ui
    spec.loader.exec_module(ui)

    class CompatibleAstra(ui.AstraAdapter):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._urlopen = compatible_opener(lambda: self.cancelled())

    # Preserve upstream UI, policy and motion validation; adapt response framing only.
    ui.AstraAdapter = CompatibleAstra
    ui.PROJECT_ROOT = run  # STATIC_ROOT already points to the original static assets.
    write_json(
        run / "launch.json",
        {
            "ui": f"http://127.0.0.1:{args.port}/",
            "robot_id": "yam-1",
            "model": args.model,
            "model_endpoint": endpoint,
            "session_api": upstream.SESSION_API,
            "upstream_revision": upstream.REVISION,
            "ui_source": str(path),
            "mode": "upstream_local_ui_api_astra",
            "response_formats": ["application/json", "text/event-stream"],
            "share_conversation": False,
            "key_loaded": True,
        },
    )
    print(json.dumps({"run_dir": str(run), "ui": f"http://127.0.0.1:{args.port}/"}), flush=True)
    # The upstream legacy UI's queue client targets yam-1; do not offer other IDs here.
    sys.argv = [
        str(path),
        "--legacy-ui",
        "--session-api",
        upstream.SESSION_API,
        "--robot-id",
        "yam-1",
        "--provider",
        "astra",
        "--waypoint-packets",
        "--no-browser",
        "--no-key-prompt",
        "--no-share-conversation",
        "--port",
        str(args.port),
    ]
    with (run / "server.log").open("a", buffering=1) as output:
        sys.stdout = output
        sys.stderr = output
        ui.main()


if __name__ == "__main__":
    main()
