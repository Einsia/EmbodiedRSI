# YAM deployment primitives — workspace-session-v1

## get_instruction()

Returns the fixed operator-supplied task. It does not move the robot.

## get_observation()

Refreshes detached measured feedback and all three native-resolution camera views;
it sends no motion. `vision.cam_head` maps to YAM top; `cam_left_wrist` and
`cam_right_wrist` map to left and right cameras. `color` contains RGB pixels.
Capture timestamps are included. Views are not guaranteed to be synchronized.

`state` contains left/right `arm_joint_state` (six encoder radians),
`ee_joint_state` (one measured normalized gripper value: 0 closed, 1 open), and
`ee_pose` ([x, y, z, qw, qx, qy, qz]). Positions use metres and orientations use
unit, scalar-first quaternions. EE poses come from forward kinematics of measured
joints using the pinned YAM model's grasp frame, NOT a measured object pose.

The EE frame is the saved YAM model's common base-midpoint frame, +z upward.
It is NOT RoboDojo's world frame, a camera frame, or the floor frame. Model base
spacing and offsets follow the selected station's saved controller profile;
these can differ from camera calibration frames. Never apply a camera transform
without reconciling those frames. The remote gateway remains authoritative for
motion validation; saved profiles are not proof of the live configuration.

`additional_info.frequency` is 10 Hz. `action` is a detached joint/gripper hold
dictionary built from measured state, not a claim that any command executed.

## step(action)

Accepts one complete dictionary for both arms and both grippers. Joint mode:

    obs = get_observation()
    action = {k: v for k, v in obs['state'].items() if k.endswith('joint_state')}
    obs, reward, terminated, truncated, info = step(action)

EE mode uses `left_ee_pose`, `right_ee_pose`, `left_ee_joint_state`,
`right_ee_joint_state`. Keep the inactive arm at its measured pose. Do not mix modes.
The host checks units, shapes, finiteness, joint/workspace limits, model contacts,
IK reachability and pacing before submitting a complete short trajectory.

One call reaches the requested target through one bounded packet, not one
RoboDojo 25 Hz tick. Waypoints run at 10 Hz, with joint increments <= 0.035 rad
and gripper increments <= 0.1. Large moves exceeding 300 waypoints per packet or
3,000 per session are rejected before dispatch. Use smaller intermediate targets.
Counts reflect waypoints, not Python calls. These are pinned runner limits; live
server/gateway limits may be stricter. The call returns only after the controller
correlates completion with settled measured feedback. HTTP acceptance alone is
insufficient. Missing cameras/state or cancellation prevents further commands.

Returns `(observation, 0.0, terminated, truncated, info)`.
`info['success']` is None and `info['reward_available']` is False: no task evaluator
is installed. The 0.0 is an interface placeholder, not failure or success evidence.
`info['native_steps']` counts confirmed executed waypoints. Ambiguous/aborted motion
is retained separately in controller records and must never be automatically replayed.

## reset()

Raises PermissionError in this deployment, including through aliases. A new
physical episode needs a new workspace and control session. Homing an arm does
not restore object positions. No PlayGround scene-reset service is implemented.
