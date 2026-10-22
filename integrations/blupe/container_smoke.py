"""Deterministic in-container client for the offline YAM smoke, with no model login."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


def main():
    for package in ("mujoco", "remote_yam"):
        if importlib.util.find_spec(package) is not None:
            raise RuntimeError("Controller dependency leaked into the agent image: " + package)
    for file in ("skills/general_pickup/move_ee.py", "primitives/primitives.md", "instruction.md"):
        try:
            with Path(file).open("a"):
                pass
        except OSError:
            continue
        raise RuntimeError("Agent can open a read-only input for writing: " + file)
    if Path("/var/run/docker.sock").exists() or list(Path("/dev").glob("nvidia*")):
        raise RuntimeError("Agent has unexpected Docker/GPU access")
    programs = [
        "persistent_value = 41\nobs = get_observation()\n",
        "assert persistent_value == 41\n"
        "target = list(get_observation()['state']['left_ee_pose'])\n"
        "target[2] += 0.002\n"
        "obs, movement = move_ee('left', target, 0.5, 2)\n"
        "assert movement['reason'] == 'reached'\n"
        "persistent_value += 1\nprint(movement)\n",
        "assert persistent_value == 42\nprint('container session roundtrip OK')\n",
    ]
    for index, code in enumerate(programs):
        path = Path("submission") / f"smoke_{index}.py"
        path.write_text(code)
        command = [sys.executable, "scripts/env.py", "exec", str(path)]
        if index == 1:
            command += ["--include", "skills/general_pickup/move_ee.py"]
        reply = subprocess.run(command, capture_output=True, text=True, timeout=90)
        if reply.returncode or json.loads(reply.stdout).get("ok") is not True:
            raise RuntimeError("Offline container execution failed: " + reply.stdout + reply.stderr)
    print(
        "Frozen knowledge is read-only; isolated container/session/skill roundtrip OK", flush=True
    )
    subprocess.run([sys.executable, "scripts/env.py", "finish"], check=True, timeout=10)


if __name__ == "__main__":
    main()
