from flask import Flask, render_template, request, jsonify
import json
import os
import requests
import logging

app = Flask(__name__)
log = logging.getLogger("klipper_server")
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

CONFIG_PATH = os.environ.get("KLIPPER_MON_CONFIG", "config.json")
MOONRAKER   = "http://localhost:7125"
MONITOR_URL = "http://localhost:5001"   # klipper_monitor.py

VALID_AXES = {"X", "Y", "Z", "E"}

# =========================
# CONFIG HELPERS
# =========================
def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH) as f:
        return json.load(f)

def save_config(cfg: dict):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

def _printer_cfg() -> dict:
    return load_config().get("printer", {})

# =========================
# MOONRAKER HELPER
# =========================
def send_gcode(cmd: str) -> bool:
    """Send G-code to Moonraker. Returns True on success."""
    try:
        r = requests.post(
            f"{MOONRAKER}/printer/gcode/script",
            json={"script": cmd},
            timeout=3
        )
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        log.warning("Moonraker G-code failed: %s", e)
        return False

# =========================
# PAGES
# =========================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/calibrate")
def calibrate_page():
    return render_template("calibrate.html")

# =========================
# PRINTER STATUS
# =========================
@app.route("/api/status")
def api_status():
    try:
        r = requests.get(
            f"{MOONRAKER}/printer/objects/query?toolhead",
            timeout=2
        )
        r.raise_for_status()
        pos = r.json()["result"]["status"]["toolhead"]["position"]
        return jsonify({"ok": True, "x": round(pos[0], 2),
                        "y": round(pos[1], 2), "z": round(pos[2], 2)})
    except requests.RequestException as e:
        log.warning("Moonraker status failed: %s", e)
        return jsonify({"ok": False, "x": 0, "y": 0, "z": 0})
    except (KeyError, IndexError) as e:
        log.warning("Unexpected Moonraker response: %s", e)
        return jsonify({"ok": False, "x": 0, "y": 0, "z": 0})

# =========================
# DETECTION STATUS PROXY
# =========================
@app.route("/api/detection")
def api_detection():
    """Proxy to klipper_monitor so the UI only talks to one port."""
    try:
        r = requests.get(f"{MONITOR_URL}/status", timeout=2)
        r.raise_for_status()
        return jsonify(r.json())
    except requests.RequestException as e:
        log.warning("Monitor unreachable: %s", e)
        return jsonify({"ok": False, "status": "OFFLINE", "motion": 0, "edge": 0})

# =========================
# MOVE (absolute)
# =========================
@app.route("/api/move", methods=["POST"])
def api_move():
    data = request.get_json(silent=True) or {}
    speed = _printer_cfg().get("travel_speed", 30)

    parts = ["G90", "G1"]
    for axis in ("x", "y", "z"):
        if axis in data:
            try:
                val = float(data[axis])
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": f"Invalid value for {axis}"}), 400
            parts[-1] += f" {axis.upper()}{val:.3f}"

    if parts[-1] == "G1":
        return jsonify({"ok": False, "error": "No axes specified"}), 400

    parts[-1] += f" F{speed}"
    ok = send_gcode("\n".join(parts))
    return jsonify({"ok": ok})

# =========================
# JOG (relative)
# =========================
@app.route("/api/jog", methods=["POST"])
def api_jog():
    data = request.get_json(silent=True) or {}
    speed = _printer_cfg().get("jog_speed", 3000)

    axis = str(data.get("axis", "")).upper()
    if axis not in VALID_AXES:
        return jsonify({"ok": False, "error": f"Invalid axis '{axis}'. Use X/Y/Z/E"}), 400

    try:
        dist = float(data["distance"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"ok": False, "error": "Invalid or missing 'distance'"}), 400

    ok = send_gcode(f"G91\nG1 {axis}{dist:.3f} F{speed}\nG90")
    return jsonify({"ok": ok})

# =========================
# HOME
# =========================
@app.route("/api/home", methods=["POST"])
def api_home():
    data  = request.get_json(silent=True) or {}
    axes  = str(data.get("axes", "")).upper().strip()

    if axes == "XY":
        cmd = "G28 X Y"
    elif axes == "Z":
        cmd = "G28 Z"
    elif axes == "":
        cmd = "G28"
    else:
        return jsonify({"ok": False, "error": f"Unknown axes '{axes}'"}), 400

    ok = send_gcode(cmd)
    return jsonify({"ok": ok})

# =========================
# CONFIG — GET / PATCH
# =========================
@app.route("/api/config", methods=["GET"])
def api_config_get():
    return jsonify(load_config())

@app.route("/api/config", methods=["PATCH"])
def api_config_patch():
    """
    Merge-patch: only supplied keys are updated, rest preserved.
    Example: PATCH /api/config  {"detection": {"motion_threshold": 200}}
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"ok": False, "error": "No JSON body"}), 400

    cfg = load_config()
    for section, values in data.items():
        if isinstance(values, dict) and isinstance(cfg.get(section), dict):
            cfg[section].update(values)
        else:
            cfg[section] = values

    save_config(cfg)
    log.info("Config patched: %s", list(data.keys()))
    return jsonify({"ok": True, "updated": list(data.keys())})

# =========================
# CALIBRATION SAVE
# =========================
@app.route("/api/calibrate", methods=["POST"])
def api_calibrate():
    """
    Save 4 pixel bed-corner points to config and forward to monitor.

    POST body:
    {
        "points": [
            {"x": 520, "y": 380},   ← index 0: printer origin (xbed=0, ybed=0)
            {"x": 520, "y": 100},   ← index 1: back-left      (xbed=0, ybed=max)
            {"x": 100, "y": 100},   ← index 2: back-right     (xbed=max, ybed=max)
            {"x": 100, "y": 380}    ← index 3: front-right    (xbed=max, ybed=0)
        ]
    }
    """
    data = request.get_json(silent=True)
    if not data or "points" not in data:
        return jsonify({"ok": False, "error": "Missing 'points'"}), 400

    pts = data["points"]
    if len(pts) != 4:
        return jsonify({"ok": False, "error": "Need exactly 4 points"}), 400

    # Persist to config
    cfg = load_config()
    if "calibration" not in cfg:
        cfg["calibration"] = {}
    cfg["calibration"]["bed_points"] = [
        {"label": "origin_fl", "px": pts[0]["x"], "py": pts[0]["y"]},
        {"label": "back_l",    "px": pts[1]["x"], "py": pts[1]["y"]},
        {"label": "back_r",    "px": pts[2]["x"], "py": pts[2]["y"]},
        {"label": "origin_fr", "px": pts[3]["x"], "py": pts[3]["y"]},
    ]
    save_config(cfg)

    # Forward to monitor so it hot-reloads the homography
    try:
        r = requests.post(
            f"{MONITOR_URL}/calibrate",
            json={"points": pts, "save": False},   # already saved above
            timeout=3
        )
        monitor_ok = r.ok
    except requests.RequestException as e:
        log.warning("Could not forward calibration to monitor: %s", e)
        monitor_ok = False

    return jsonify({"ok": True, "monitor_updated": monitor_ok})

# =========================
# MJPEG STREAM PROXY
# =========================
GO2RTC     = "http://localhost:1984"
GO2RTC_SRC = "esp32"

@app.route("/api/stream/mjpeg")
def api_stream_mjpeg():
    """
    Proxy the go2rtc MJPEG stream to the browser.
    Using a server-side proxy means the browser only needs port 5000,
    works from any device on the LAN.
    """
    import urllib.request
    upstream_url = f"{GO2RTC}/api/stream.mjpeg?src={GO2RTC_SRC}"
    try:
        upstream = urllib.request.urlopen(upstream_url, timeout=10)
    except Exception as e:
        log.warning("go2rtc MJPEG unreachable: %s", e)
        return "go2rtc unreachable", 502

    def generate():
        try:
            while True:
                chunk = upstream.read(4096)
                if not chunk:
                    break
                yield chunk
        finally:
            upstream.close()

    return app.response_class(
        generate(),
        mimetype=upstream.headers.get("Content-Type", "multipart/x-mixed-replace; boundary=frame"),
        direct_passthrough=True,
    )

    return "go2rtc SDP exchange failed — check server logs", 502

@app.route("/api/stream/info")
def api_stream_info():
    """
    Debug: returns go2rtc stream list so you can verify the source
    name and confirm go2rtc is reachable.
    Open in browser: http://<printer-ip>:5000/api/stream/info
    """
    try:
        r = requests.get(f"{GO2RTC}/api/streams", timeout=3)
        return jsonify({"ok": True, "streams": r.json()})
    except requests.RequestException as e:
        return jsonify({"ok": False, "error": str(e)})

# =========================
# START
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
