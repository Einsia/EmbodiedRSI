# EmbodiedEvo: Self-Evolving Coding Agent For Physical Intelligence

<p align="center">
  <a href="https://arxiv.org/abs/2610.18867">
    <img src="https://img.shields.io/badge/arXiv-2610.18867-B31B1B?style=flat-square" alt="arXiv: 2610.18867" />
  </a>
  <a href="https://lab.einsia.ai/embodiedevo/">
    <img src="https://img.shields.io/badge/Project%20Page-Website-315F85?style=flat-square" alt="Project Page" />
  </a>
</p>

<p align="center">
  <img src="docs/images/embodiedevo-hero.png" alt="EmbodiedEvo overview: a coding agent observes physical feedback, revises and executes control code, and accumulates reusable Skills and Lessons that are frozen and transferred to a fresh agent in each new Test scene." width="100%" />
</p>

**Observe, revise code, and act. Learn Skills and Lessons from physical feedback.**

EmbodiedEvo connects a coding agent's workspace to a persistent RoboDojo simulation
session. The agent inspects observations, edits Python control code, executes it,
and uses the resulting physical feedback to guide its next revision. Its evolving
knowledge lives in reusable code and written lessons in the workspace.

1. **Observe, revise, execute.** Inspect camera observations and robot state,
   update control code, and execute the revised segment in the same Python and
   simulation session. New observations close the interaction loop.
2. **Build Skills and Lessons in Playground.** Turn interaction experience into
   reusable helpers in `skills/` and practical guidance in `lessons/`, such as
   grasping from above, verifying a grasp, or carrying an object in small steps.
   Playground permits same-scene resets while the agent refines this knowledge.
3. **Freeze and reuse in Test.** Freeze only `skills/` and `lessons/` before
   selecting Test scenes. Each scene starts with a fresh agent, workspace, and
   simulator. The transferred knowledge is read-only; the agent can observe and
   revise code within the scene, but cannot reset or learn across Test scenes.

## Setup

Use a Linux host and Python 3.11 or newer for the orchestration environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-host.txt -e '.[dev]'
.venv/bin/python -m unittest discover -s tests -v
```

Build the thin coding-agent image directly from the included Dockerfile:

```bash
docker build -t embodiedevo-agent:latest docker/agent
```

The Dockerfile installs pinned Codex CLI 0.159.2 and basic Python/image tools;
no host Codex installation or API key is needed for the build. Its context
contains no credentials, task files or simulator assets. Build
the image on each Docker host that will run agents; the simulator is provisioned
separately. See [the complete image build procedure](docs/INSTALL.md#thin-agent-container).

Run commands from this repository root. The editable installation keeps task
templates and recipes available at their source paths. The project is currently
distributed as a source checkout, not a standalone wheel with simulator assets.

Real episodes require a separately provisioned RoboDojo/Isaac Sim environment,
assets, an available NVIDIA GPU, Docker, and configured model credentials.
Follow [installation](docs/INSTALL.md), including
[Codex API configuration](docs/CODEX_API.md), before starting an experiment.

## Playground and Test

After setup, choose a native task with `--env-task`. The model and reasoning
effort are explicit in `tasks/RoboDojo/task.toml`; configure them before a new run.

```bash
RUN_DIR="runs/$(date +%Y%m%d%H%M)_stack_bowls"
.venv/bin/embodiedevo explore --task RoboDojo --env-task stack_bowls \
  --run-dir "$RUN_DIR" --gpu 0
.venv/bin/embodiedevo test --task RoboDojo --env-task stack_bowls \
  --run-dir "$RUN_DIR" --gpu 0
```

`embodiedevo run` performs both stages. Test-only baseline:

```bash
.venv/bin/embodiedevo test --task tasks_baseline/RoboDojo --env-task stack_bowls \
  --run-dir "runs/$(date +%Y%m%d%H%M)_stack_bowls_baseline" --gpu 0
```

The baseline starts with its own instruction and no transferred Skills/Lessons.
Public task recipes are enabled by default for both configurations, matching the
source snapshot. Set `EMBODIEDEVO_USE_RECIPE=0` before preparing a new run to omit
that supplied knowledge. The choice and recipe source are recorded per run.
See [the protocol](docs/PROTOCOL.md) and [running and evaluation](docs/RUNNING.md).

## Source map

For frozen Skills/Lessons deployment on BluPe's remote YAM arms, see
[the BluPe deployment guide](docs/BLUPE.md). It provides a separate headless runner,
an offline execution check and a read-only remote availability probe.

| Path | Role |
| --- | --- |
| `orchestrator/` | Task configuration, isolated agents, sessions, freeze and Test lifecycle |
| `environments/`, `simulators/` | Python execution, four primitives and RoboDojo adaptation |
| `integrations/robodojo_policy/` | XPolicyLab policy and native evaluation transport |
| `tasks/RoboDojo/` | Playground/Test templates, primitive manual and public recipes |
| `tasks_baseline/RoboDojo/` | Fresh Test-only baseline |
| `docker/agent/`, `scripts/` | Thin agent image, provisioning and diagnostics |
| `tests/` | CPU tests; optional transport tests require a provisioned XPolicyLab |

Cluster campaigns, private deployment profiles, historical recovery tools,
dashboards and result archives are outside this source distribution.
[Source provenance](docs/SOURCE.md) describes the export and its validation limits.

## License and third-party material

A project-wide license has not yet been selected. This is a local release
preparation, not yet a licensed public release. The included RoboProbe task
recipes retain their Apache-2.0 license and source attribution; see
[third-party notices](THIRD_PARTY_NOTICES.md).

## Citation

If you use EmbodiedEvo in your research, please cite our
[paper](https://arxiv.org/abs/2610.18867).

```bibtex
@misc{li2026embodiedevoselfimprovingcodingagents,
  title         = {EmbodiedEvo: Self-improving Coding Agents for Physical Intelligence},
  author        = {Wenyi Li and Yizhe Chi and Youjie Zheng and Chunyu Liu and
                   Xiaoqiu Wang and Mingju Gao and Zheng Jiang and Deyao Hong and
                   Calvin Xiao and Kaisen Yang and Qinhuai Na},
  year          = {2026},
  eprint        = {2610.18867},
  archivePrefix = {arXiv},
  primaryClass  = {cs.RO},
  url           = {https://arxiv.org/abs/2610.18867}
}
```
