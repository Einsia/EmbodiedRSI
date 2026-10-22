"""Read-only station checks. Never creates a session or sends an action."""

import hashlib
import io
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from urllib import error, parse, request

from PIL import Image

from orchestrator.files import write_json


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def validate_origin(origin):
    url = parse.urlsplit(origin)
    if (
        url.scheme not in {"https", "http"}
        or not url.hostname
        or url.username
        or url.password
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
    ):
        raise ValueError("Session API must be an HTTP(S) origin without credentials")
    return origin.rstrip("/")


def read(origin, path, *, timeout=8):
    req = request.Request(validate_origin(origin) + path, method="GET")
    started = time.monotonic()
    try:
        with request.build_opener(NoRedirect()).open(req, timeout=timeout) as response:
            body = response.read(8 * 1024 * 1024 + 1)
            if len(body) > 8 * 1024 * 1024:
                raise ValueError("Response exceeds 8 MiB")
            return {
                "ok": True,
                "http_status": response.status,
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
                "headers": dict(response.headers),
                "body": body,
            }
    except error.HTTPError as exc:
        code = exc.code
        exc.close()
        return {"ok": False, "http_status": code, "error": "HTTPError"}
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": type(exc).__name__}


def json_read(origin, path):
    result = read(origin, path)
    if result["ok"]:
        try:
            result["data"] = json.loads(result.pop("body"))
        except (ValueError, UnicodeError):
            result.update(ok=False, error="InvalidJSON")
        result.pop("headers", None)
    return result


def readiness(observation, *, now=None):
    """Require explicit measured state before motion, after the queue handoff."""
    now = time.time() if now is None else now
    reasons = []
    if observation.get("source") != "hardware":
        reasons.append("not_hardware")
    stamp = observation.get("observed_at")
    if type(stamp) not in (int, float) or not math.isfinite(stamp) or not -2 <= now - stamp <= 10:
        reasons.append("stale_or_missing_observation")
    if observation.get("mode") in (None, "DISABLED", "FAULT"):
        reasons.append("controller_" + str(observation.get("mode")))
    for key in ("homed", "settled"):
        if observation.get(key) is not True:
            reasons.append("not_" + key)
    safety = observation.get("safety") or {}
    if safety.get("ok") is not True or safety.get("estop_engaged") is not False:
        reasons.append("safety_" + str(safety.get("reason") or "unconfirmed"))
    for arm in ("left", "right"):
        joints = observation.get(arm + "_joints_deg")
        if (
            not isinstance(joints, list)
            or len(joints) != 6
            or any(type(x) not in (int, float) or not math.isfinite(x) for x in joints)
        ):
            reasons.append(arm + "_joints_unavailable")
        grip = observation.get(arm + "_gripper")
        if type(grip) not in (int, float) or not math.isfinite(grip) or not 0 <= grip <= 1:
            reasons.append(arm + "_gripper_unavailable")
    return reasons


def queue_readiness(observation, station, robot_id, *, now=None):
    """Admission can activate a parked station; it does not authorize a trajectory."""
    now = time.time() if now is None else now
    reasons = []
    if station.get("connected") is not True:
        reasons.append("station_disconnected_or_unknown")
    if station.get("queue_ready") is not True or observation.get("queue_ready") is False:
        reasons.append("station_not_queue_ready")
    for name, value in (("observation", observation), ("station", station)):
        if value.get("jetson_id", value.get("robot_id")) != robot_id:
            reasons.append(name + "_robot_identity_mismatch")
        if value.get("source") != "hardware":
            reasons.append(name + "_not_hardware")
        stamp = value.get("observed_at")
        if (
            type(stamp) not in (int, float)
            or not math.isfinite(stamp)
            or not -2 <= now - stamp <= 10
        ):
            reasons.append(name + "_stale_or_missing")
        if value.get("mode") in (None, "FAULT"):
            reasons.append(name + "_controller_" + str(value.get("mode")))
    safety = observation.get("safety") or {}
    if safety.get("estop_engaged") is not False:
        reasons.append("emergency_stop_engaged_or_unknown")
    if safety.get("contact_count") not in (None, 0) or safety.get("active_contacts") not in (
        None,
        0,
        [],
    ):
        reasons.append("station_contact_reported")
    parked = observation.get("mode") in {"DISABLED", "STOPPED"}
    if safety.get("ok") is not True and not (
        parked and safety.get("reason") == "hardware_not_initialized"
    ):
        reasons.append("safety_" + str(safety.get("reason") or "unconfirmed"))
    return reasons


def probe(origin, robot_id, directory, *, websocket_check=True):
    origin = validate_origin(origin)
    directory.mkdir(parents=True, exist_ok=False)
    robot = parse.quote(robot_id, safe="")
    paths = {
        "health": "/health",
        "contracts": "/v1/contracts",
        **{
            name: f"/v1/robots/{robot}/{path}"
            for name, path in (
                ("queue", "queue"),
                ("observation", "observation"),
                ("calibration", "calibration"),
                ("camera_poses", "camera-poses"),
            )
        },
    }
    with ThreadPoolExecutor(max_workers=6) as pool:
        checks = dict(
            zip(paths, pool.map(lambda path: json_read(origin, path), paths.values()), strict=True)
        )
    for name, result in checks.items():
        write_json(directory / (name + ".json"), result)
    obs = checks["observation"].get("data", {})
    obs = obs.get("observation", obs)
    connection_reasons = [
        name + "_unavailable"
        for name in ("health", "contracts", "queue", "observation", "calibration")
        if not checks[name]["ok"]
    ]
    if obs.get("jetson_id", obs.get("robot_id")) != robot_id:
        connection_reasons.append("robot_identity_mismatch")
    queue = checks["queue"].get("data", {})
    stations = queue.get("stations", [])
    matching = [s for s in stations if s.get("jetson_id", s.get("robot_id")) == robot_id]
    station = matching[0] if matching else {}
    if station.get("connected") is not True:
        connection_reasons.append("station_disconnected_or_unknown")
    calibration = checks["calibration"].get("data", {})
    if calibration.get("robot_id") != robot_id:
        connection_reasons.append("calibration_unavailable_or_mismatched")

    def camera(role):
        result = read(origin, f"/v1/robots/{robot}/cameras/{role}.jpg")
        if result["ok"]:
            body, headers = result.pop("body"), result.pop("headers")
            try:
                with Image.open(io.BytesIO(body)) as frame:
                    frame.load()
                    result.update(size=list(frame.size), format=frame.format)
                if result["format"] != "JPEG":
                    raise ValueError("Expected native JPEG")
                (directory / (role + ".jpg")).write_bytes(body)
                result["sha256"] = hashlib.sha256(body).hexdigest()
                stamp = next((v for k, v in headers.items() if k.lower() == "x-captured-at"), None)
                age = time.time() - float(stamp) if stamp is not None else None
                result.update(
                    captured_at=stamp, age_s=age, fresh=age is not None and -2 <= age <= 10
                )
            except (OSError, ValueError):
                result.update(ok=False, error="InvalidCameraFrame")
        return role, result

    with ThreadPoolExecutor(max_workers=3) as pool:
        cameras = dict(pool.map(camera, ("left", "top", "right")))
    for role, result in cameras.items():
        if not result["ok"] or not result.get("fresh"):
            connection_reasons.append(role + "_camera_unavailable_or_stale")
    stream = {"checked": False}
    if websocket_check:
        sock = None
        try:
            import websocket

            url = origin.replace("https://", "wss://").replace("http://", "ws://")
            sock = websocket.create_connection(url + f"/v1/robots/{robot}/events", timeout=8)
            event = json.loads(sock.recv())
            stream = {"checked": True, "ok": True, "event_type": event.get("type")}
        except Exception as exc:
            stream = {"checked": True, "ok": False, "error": type(exc).__name__}
        finally:
            if sock is not None:
                sock.close()
    if websocket_check and not stream.get("ok"):
        connection_reasons.append("monitor_websocket_unavailable")
    motion_reasons = list(dict.fromkeys(connection_reasons + readiness(obs)))
    queue_reasons = list(
        dict.fromkeys(connection_reasons + queue_readiness(obs, station, robot_id))
    )
    result = {
        "checked_at": time.time(),
        "robot_id": robot_id,
        "origin": origin,
        "read_only": True,
        "session_created": False,
        "motion_sent": False,
        "service_reachable": checks["health"]["ok"],
        "checks": checks,
        "cameras": cameras,
        "monitor_websocket": stream,
        "queue_ready": not queue_reasons,
        "queue_blockers": queue_reasons,
        "motion_ready": not motion_reasons,
        "motion_blockers": motion_reasons,
        "blockers": motion_reasons,  # Keep the original motion-only report field.
        "task_success": None,
        "physical_execution_verified": False,
    }
    write_json(directory / "report.json", result)
    return result
