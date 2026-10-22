# Deploy frozen EmbodiedEvo Skills/Lessons on BluPe YAM

This integration runs the existing persistent Python/session protocol with a
fresh agent and immutable exported knowledge. BluPe's pinned RunnerController
owns the one queue session, lease, trajectory dispatch, correlated completion,
gateway checks and Stop. The adapter supports a local monitor; the BluPe website does not
need an EmbodiedEvo model selector. Model calls remain in our isolated agent.

The external policy checkout is used only as an input source of verified frozen
Skills/Lessons. None of its runtime, recipes, programs, recordings or credentials
is imported. No ASPIRE coding loop or new learning phase is used.

## Install

Run from the EmbodiedEvo repository root on a Linux Docker host. The local clone
is ignored by Git; do not add it as an embedded repository or copy it into vendor/.
The integration deliberately refuses another revision or modified tracked source.

```bash
git clone https://github.com/andlyu/blupe-remote-yam.git blupe-remote-yam
git -C blupe-remote-yam checkout 64465946684c7ad3ef13b8ef4c71eeb1cb38922d
python3 -m venv .venv-blupe
.venv-blupe/bin/python -m pip install -e '.[dev]' -r requirements-blupe.txt
docker build -t embodiedevo-agent:blupe docker/agent
```

If the clone/environment/image already exists, reuse it after checking the pinned
revision. CPU kinematics does not need Isaac Sim, RoboDojo assets or a GPU. Provider
setup uses the existing [Codex configuration](CODEX_API.md); keep it outside Git.
`EMBODIEDEVO_CODEX_HOME` can select a dedicated provider home. No model request is
made by prepare, probe or smoke. Only run launches a model and requests real motion.

If the Docker build cannot reach the package mirrors through its default network,
the existing Dockerfile can use the host network and inherited proxy settings:

```bash
docker build --network host \
  --build-arg HTTP_PROXY --build-arg HTTPS_PROXY --build-arg NO_PROXY \
  -t embodiedevo-agent:blupe docker/agent
```

## Verify and stage the frozen knowledge

Set `BLUPE_KNOWLEDGE_SOURCE` to the exported policy directory (containing Workspace/)
or the Workspace/ directory itself. Its experience-manifest.json must provide the
original per-task SHA-256 hashes. All files are verified before copying begins.

```bash
BLUPE_KNOWLEDGE_SOURCE=/path/to/XPolicyLab/policy/EmbodiedEvo
BLUPE_RUN_STAMP=$(TZ=Asia/Shanghai date +%Y%m%d%H%M)
.venv-blupe/bin/python -m integrations.blupe.run prepare \
  --knowledge-source "$BLUPE_KNOWLEDGE_SOURCE" \
  --run-dir "runs/${BLUPE_RUN_STAMP}_blupe_knowledge"
```

By default all verified tasks are copied, preserving `skills/<task>/...` and
`lessons/<task>/...`. Repeat `--knowledge-task general_pickup` etc. to select a
subset explicitly. The source files stay byte-for-byte unchanged. knowledge.json
records provenance and hashes outside the workspace. Only Skills/Lessons transfer;
the submission program and instruction/manual/client are fresh deployment files.
The prepared directory is an inspection artifact; each smoke/run invocation makes
its own fresh snapshot from the source. Existing run directories are never reused.

The bundled export found during this integration has 42 tasks, 293 skill files
and 165 lesson files. These are RoboDojo/ARX X5 experience, not validated YAM skills.
Helpers using the documented generic EE/joint fields can execute through the
adapter. Absolute poses, home joints, image landmarks and task-specific numbers
need live YAM validation. Read the handwritten manual under
integrations/blupe/workspace/primitives/primitives.md. Historical observations
referenced by lessons are not copied or treated as current evidence.

## Read-only remote checks

```bash
.venv-blupe/bin/python -m integrations.blupe.run probe \
  --robot-id yam-1 --run-dir "runs/${BLUPE_RUN_STAMP}_yam_availability"
.venv-blupe/bin/python -m integrations.blupe.run probe \
  --robot-id robot-ba8413962083809c \
  --run-dir "runs/${BLUPE_RUN_STAMP}_robohouse_availability"
```

The probe uses public GET endpoints and the public monitoring WebSocket. It does
not create a queue entry or send any action. It records health/contracts, queue,
raw station feedback, calibration, camera poses, original JPEGs and timestamps.
`remote/report.json` separates service reachability, camera/stream access,
`queue_ready` and `motion_ready`. Exit 2 means queue admission prerequisites are
missing; inspect `queue_blockers`. Exit 0 means the station can be queued, not
that a hardware trajectory or task has succeeded. `motion_blockers` describes
what still needs to become ready before a policy can act. The legacy `blockers`
field has the same contents as `motion_blockers`.
`--session-api` selects a different service origin; station IDs are never changed
automatically. A connected, fresh station with `queue_ready: true` can be parked
and DISABLED with `hardware_not_initialized` and missing gripper measurements.
This is the normal automatic-queue standby state: joining can enable and home
the arms. Faults, emergency stops and a missing queue-ready signal still block
admission. After assignment, the controller waits for fresh settled Home feedback
and the provider requires measured grippers before starting the agent.

Earlier availability reports used motion readiness as the queue admission gate.
Those reports are preserved, but DISABLED alone does not establish unavailability;
inspect the recorded station's `queue_ready` flag or run a new probe.

## Offline execution check

```bash
.venv-blupe/bin/python -m integrations.blupe.run smoke \
  --knowledge-source "$BLUPE_KNOWLEDGE_SOURCE" \
  --run-dir "runs/${BLUPE_RUN_STAMP}_blupe_smoke" --duration 60
.venv-blupe/bin/python -m integrations.blupe.run smoke --container \
  --knowledge-source "$BLUPE_KNOWLEDGE_SOURCE" \
  --run-dir "runs/${BLUPE_RUN_STAMP}_blupe_container_smoke" --duration 120
.venv-blupe/bin/python -m unittest discover -s tests -v
```

Smoke uses the upstream event/trajectory mock with a local extension for its
settled feedback refresh API. It runs the original frozen
skills/general_pickup/move_ee.py through a spawned persistent session, real CPU
MuJoCo IK, the real RunnerController, correlated completion and observation export.
It verifies a 2 mm simulated lift and Python state across three submissions.
The synthetic camera frames validate data transport only. It makes no model
call and cannot establish real grasping performance. The default smoke runs the
client on the host. `--container` uses the same isolated Docker mounts and socket
protocol as deployment, with networking disabled and no credentials. It checks
that knowledge/manual files are read-only, simulator/controller packages and
GPU/Docker access are absent, and the frozen helper completes through the client.
Unit tests without the optional dependencies skip the native planner checks.

## Local UI and Astra connection check

The upstream local UI can run Astra through the provider already configured in
the selected Codex home. The launcher reads its base URL and auth.json privately;
opening the UI starts monitoring, and clicking Join Queue starts a physical run:

```bash
.venv-blupe/bin/python -u -m integrations.blupe.ui \
  --port 8788 --model gpt-6-astra \
  --run-dir "runs/$(TZ=Asia/Shanghai date +%Y%m%d%H%M)_yam_local_ui"
```

Open http://127.0.0.1:8788/ on the runner machine, or forward that port from a
remote host. This upstream developer UI targets `yam-1`. It uses the upstream
RoboCurve Astra policy and original motion validation. The frozen-knowledge
EmbodiedEvo program provider is exercised by the separate `run` command below.
UI logs, model calls and controller evidence go under the supplied run directory.

Some compatible provider gateways return `text/event-stream` even for a
non-streaming request. The UI launcher accepts JSON or SSE, exposing only the
successful final `response.completed` object to the original policy. Partial,
failed, interrupted or oversized responses never become robot tool calls. The
compatibility code lives in integrations/blupe/; the upstream checkout is unchanged.

## Physical deployment

When the chosen YAM reports queue readiness, supply an actual scene-appropriate
instruction. Creating a queue entry can automatically enable and home the arms;
the following command also permits policy motion after the control handoff:

```bash
BLUPE_DEPLOY_STAMP=$(TZ=Asia/Shanghai date +%Y%m%d%H%M)
.venv-blupe/bin/python -m integrations.blupe.run run \
  --knowledge-source "$BLUPE_KNOWLEDGE_SOURCE" \
  --knowledge-task general_pickup \
  --robot-id yam-1 \
  --instruction 'Pick up the visible green block and place it on the plate.' \
  --model gpt-6-astra --reasoning-effort xhigh \
  --duration 300 --queue-timeout 300 \
  --run-dir "runs/${BLUPE_DEPLOY_STAMP}_yam_pick"
```

The instruction is an example, not a claim that those objects are currently on
the table. The runner refuses to queue if fresh station/queue feedback, three
cameras, calibration identity, queue readiness or model image/config requirements
are missing. Initialization, Home, settled joints and measured grippers are checked
after assignment and before policy execution. `--image` selects a provisioned compatible
thin agent image. The agent root, knowledge, protocol and feedback are read-only;
only the working program/scratch and staged provider home are writable. It sees
no controller source, lease/capability, Docker socket or GPU.

For the first connection check, use an observation-only task instruction such as:
`Read get_instruction() and get_observation(), inspect the three camera views,
report the measured arm state, then finish. Do not call step() or reset().`
This exercises model access and the leased observation path. The station can
still move automatically during activation, homing and parking; it is not a
read-only probe. Follow with a separately specified motion task to validate
hardware execution. The upstream local UI can provide an independent baseline
using the same robot ID and model provider.

Ctrl-C stops/releases the controller session and terminates the agent. The local
queue-wait deadline is separate from the server's active-run deadline. The live
create contract currently accepts 60–600 seconds; the gateway remains authoritative
and can stop earlier. There is no automatic replay, replacement session or
cross-run continuation after a crash, uncertain dispatch or timeout.

## Semantics and evidence

For a local EmbodiedEvo UI, start the monitor against the planned fresh run
directory before launching `run`. It follows that single run and can request Stop:

```bash
.venv-blupe/bin/python -m integrations.blupe.monitor \
  --run-dir "runs/${BLUPE_RUN_STAMP}_yam_deployment" --port 8788
```

Open `http://127.0.0.1:8788/`. Add
`--video-url https://playground.blupe.io/synchronized-hd/index.m3u8` to the
physical `run` command to record the native synchronized video. Recording must
produce bytes before queue admission and continues through parking. Its stream
may lag the control observations; the original video and capture timing are kept
under `video/`. Recording interruption stops the run instead of silently dropping
the requested evidence. The UI's live player is independent of the recorder.

The local model and reasoning settings can be read from the selected Codex home's
config and passed explicitly with `--model` / `--reasoning-effort`; the existing
home staging preserves provider routing and credentials. The run retains the
agent's raw Codex rollout JSONL under `agent/rollouts/`, including after the
disposable agent HOME is removed. Only transcript files are copied from that
HOME. `agent.log`, numbered source/feedback snapshots, `controller-status.jsonl`
and `robot-trace.json` retain the other execution evidence. The local monitor
shows agent output, trace, controller status and video without starting a second
robot session.

- The four primitive names remain unchanged. Reset always raises in deployment.
- EE and joint action dictionaries have the familiar field names. Radians are
  converted to encoder degrees; EE positions/quaternions use the selected saved
  YAM model's base-midpoint grasp frame. This differs from RoboDojo world coordinates
  and can differ from published camera frames. FK is derived, not direct pose sensing.
- One step is a complete bounded packet at 10 Hz, with at most 300 waypoints;
  the pinned runner permits 3,000 total. All are counted. Joint increments are
  limited to 0.035 rad (0.35 rad/s); every packet still passes gateway validation.
- Raw pixels and timestamps are retained. Per-submission videos contain sampled
  feedback frames, not a synchronized continuous recording of the robot at 10 Hz.
  The optional HLS recorder saves continuous station video separately. PNGs preserve pixels
  after decoding the original JPEG; native frame dimensions are preserved in videos.
- No physical task-success evaluator is installed. Public success, task_completed
  and private ledger success/reward are null. Primitive reward 0.0 is only a
  Gymnasium compatibility placeholder. Agent statements and script completion
  cannot populate an official score.
- Failed attempts remain in their original run directories. knowledge.json,
  deployment-config.json, deployment.json, worker errors, controller recordings,
  numbered code/raw feedback and model usage are local evidence excluded from Git.
  The public-conversation publisher is disabled in this integration; public task
  and robot recordings still follow BluPe's service behavior.

This is frozen-knowledge deployment, not the full controlled Playground/Test
experiment. No claim of physical success or statistical sim-to-real transfer
follows from a mock check or an online camera view.
