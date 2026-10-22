"""Import only the pinned, repository-local BluPe runtime, without editing it."""

import importlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKOUT = ROOT / "blupe-remote-yam"
REVISION = "64465946684c7ad3ef13b8ef4c71eeb1cb38922d"
SESSION_API = "https://yam-session-api.n5hthc3gj4cqy.us-east-1.cs.amazonlightsail.com"


def load():
    if CHECKOUT.is_symlink() or not (CHECKOUT / ".git").exists():
        raise RuntimeError(
            "Provision the repository-local blupe-remote-yam checkout; see docs/BLUPE.md"
        )
    revision = subprocess.check_output(
        ["git", "-C", str(CHECKOUT), "rev-parse", "HEAD"], text=True
    ).strip()
    dirty = subprocess.check_output(
        ["git", "-C", str(CHECKOUT), "status", "--porcelain", "--untracked-files=no"], text=True
    ).strip()
    if revision != REVISION or dirty:
        raise RuntimeError("BluPe runtime must be clean and pinned to " + REVISION)
    source = CHECKOUT / "codex-runner" / "src"
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    package = importlib.import_module("remote_yam")
    if Path(package.__file__).resolve().parent != source / "remote_yam":
        raise RuntimeError("Another remote_yam installation is shadowing the pinned runtime")
    return revision
