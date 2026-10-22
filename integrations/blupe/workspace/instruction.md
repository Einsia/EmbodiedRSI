# Frozen EmbodiedEvo deployment on a remote YAM robot

Read primitives/primitives.md before acting. Obtain this episode's task using
get_instruction(). Use the live camera images and measured robot feedback.
Write programs under submission/ and submit through `python scripts/env.py exec`.
Python variables persist across submissions in this episode. Only numpy and math
are available in submitted code. Workspace Python may inspect exported arrays.

skills/ and lessons/ contain unchanged, hash-verified experience learned in
RoboDojo on ARX X5. They are read-only and grouped by source task. Choose relevant
helpers explicitly, for example:

    python scripts/env.py exec submission/solution.py --include skills/general_pickup/move_ee.py

Include files are executed in order in the persistent namespace; they are not
importable modules. Inspect helpers before executing them. Past observation
references and outcome claims are historical; their source episodes are absent.
All absolute coordinates, orientations, calibrated home joints, image landmarks,
timing assumptions and object sizes in that experience require fresh validation
on YAM. In particular RoboDojo world/table heights must not be reused on YAM.
Keep adapted task-specific code in submission/; never rewrite frozen knowledge.

There is no task success detector. A completed motion or a clean script exit does
not establish task success. The reward is an unavailable-signal placeholder and
info['success'] is None. Report what the raw observations establish and any
uncertainty. Do not wait for an official success signal that does not exist.

There are {{EXECUTION_BUDGET}} execution requests and at most
{{NATIVE_ACTION_LIMIT}} accepted 10 Hz waypoints. One step(action) can consume
multiple waypoints. The server also imposes a wall-clock deadline including model
thinking, planning and settling. `python scripts/env.py status` shows the local
remaining budgets; the controller can stop earlier. Inspect feedback after each
short segment. A rejected plan can be corrected; an uncertain or failed physical
dispatch ends execution and must not be replayed. Reset and cross-run learning
are forbidden. Finish with `python scripts/env.py finish` to release the session.
