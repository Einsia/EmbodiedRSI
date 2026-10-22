"""Verify and snapshot ONLY frozen Skills/Lessons from an exported workspace."""

import hashlib
import json
import re
import shutil
from pathlib import Path, PurePosixPath

from orchestrator.files import write_json


def new_run(path):
    path = Path(path).absolute()
    if not re.match(r"^\d{12}(?:_|$)", path.name):
        raise ValueError("--run-dir must have a YYYYMMDDHHMM prefix")
    path.mkdir(parents=True, exist_ok=False)
    return path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def inventory(source, tasks=None):
    source = Path(source).resolve()
    if (source / "Workspace").is_dir():
        source /= "Workspace"
    manifest_path = source / "experience-manifest.json"
    if manifest_path.is_symlink():
        raise ValueError("Knowledge manifest must not be a symlink")
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    selected = sorted(manifest["tasks"] if not tasks else set(tasks))
    if not selected:
        raise ValueError("No frozen knowledge selected")
    files = {}
    for task in selected:
        if not re.fullmatch(r"[A-Za-z0-9_]+", task) or task not in manifest["tasks"]:
            raise ValueError(f"Unknown knowledge task: {task}")
        for name, expected in manifest["tasks"][task]["files"].items():
            relative = PurePosixPath(name)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or len(relative.parts) < 2
                or relative.parts[0] not in {"skills", "lessons"}
            ):
                raise ValueError(f"Unsafe knowledge path: {name}")
            relative = Path(relative.parts[0], task, *relative.parts[1:])
            path = source / relative
            if any(p.is_symlink() for p in (path, *path.parents) if p != source.parent):
                raise ValueError(f"Symlink in frozen knowledge: {relative}")
            data = path.read_bytes()
            if digest(data) != expected:
                raise ValueError(f"Frozen knowledge hash mismatch: {relative}")
            files[str(relative)] = (data, expected)
    return source, manifest, digest(raw), files, selected


def prepare(source, run, tasks=None):
    source, manifest, manifest_hash, files, selected = inventory(source, tasks)
    workspace = run / "workspace"
    workspace.mkdir()
    for name, (data, _) in files.items():
        target = workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o444)
    templates = Path(__file__).parent / "workspace"
    for name in ("instruction.md", "primitives"):
        target = workspace / name
        if (templates / name).is_dir():
            shutil.copytree(templates / name, target)
        else:
            shutil.copyfile(templates / name, target)
    for name in ("scripts", "submission", "scratch", "observations"):
        (workspace / name).mkdir()
    from orchestrator import session_client

    shutil.copyfile(session_client.__file__, workspace / "scripts" / "env.py")
    (workspace / "submission" / "solution.py").write_text(
        "# Start with fresh observations; choose compatible frozen helpers.\n"
        "obs = get_observation()\nprint(get_instruction())\n"
    )
    report = {
        "schema": "blupe-frozen-knowledge-v1",
        "protocol": "workspace-session-v1",
        "source_workspace": str(source),
        "source_manifest_sha256": manifest_hash,
        "source_run": manifest.get("source_run"),
        "model": manifest.get("model"),
        "reasoning_effort": manifest.get("reasoning_effort"),
        "tasks": selected,
        "files": {name: sha for name, (_, sha) in files.items()},
        "counts": {
            kind: sum(n.startswith(kind + "/") for n in files) for kind in ("skills", "lessons")
        },
        "recipes_transferred": False,
        "hardware_validation": "not_validated",
    }
    write_json(run / "knowledge.json", report)
    verify(run)
    return report


def verify(run):
    record = json.loads((run / "knowledge.json").read_text())
    workspace = run / "workspace"
    actual = {}
    for kind in ("skills", "lessons"):
        for path in (workspace / kind).rglob("*"):
            if path.is_symlink():
                raise ValueError("Frozen knowledge contains a symlink")
            if path.is_file():
                actual[str(path.relative_to(workspace))] = digest(path.read_bytes())
    if actual != record["files"]:
        raise ValueError("Frozen deployment knowledge changed")
    return record
