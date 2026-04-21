import cv2
import numpy as np
import time
import requests
import json
import os
import subprocess

CONFIG_PATH = "config.json"
MOONRAKER = "http://localhost:7125"
STREAM_URL = "http://localhost:1984/api/stream.mjpeg?src=esp32"

DEBUG_PATH = "/tmp/ai_debug.jpg"

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)

def get_toolhead():
    try:
        r = requests.get(f"{MOONRAKER}/printer/objects/query?toolhead", timeout=0.3)
        pos = r.json()["result"]["status"]["toolhead"]["position"]
        return pos[0], pos[1]
    except:
        return None, None

def get_transform(cfg):
    m = cfg["calibration"].get("transform_matrix", [])
    if len(m) == 9:
        return np.array(m).reshape(3,3)
    return None

def warp(frame, matrix, w, h):
    return cv2.warpPerspective(frame, matrix, (w, h))

def dynamic_mask(frame, cfg):
    x, y = get_toolhead()
    if x is None:
        return frame

    kin = cfg["printer"]["kinematics"]

    x_norm = (x - kin["x_min"]) / (kin["x_max"] - kin["x_min"])
    y_norm = (y - kin["y_min"]) / (kin["y_max"] - kin["y_min"])

    h, w = frame.shape[:2]

    px = int(x_norm * w)
    py = int((1 - y_norm) * h)

    mask = np.ones((h, w), dtype=np.uint8) * 255

    r = cfg["calibration"]["mask_radius"]
    cv2.circle(mask, (px, py), r, 0, -1)

    return cv2.bitwise_and(frame, frame, mask=mask)

# FFmpeg stream
ffmpeg_cmd = [
    "ffmpeg", "-loglevel", "quiet",
    "-i", STREAM_URL,
    "-vf", "scale=640:480",
    "-f", "rawvideo",
    "-pix_fmt", "bgr24", "-"
]

pipe = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, bufsize=10**8)

prev = None

while True:
    cfg = load_config()

    raw = pipe.stdout.read(640*480*3)
    if len(raw) != 640*480*3:
        continue

    frame = np.frombuffer(raw, dtype=np.uint8).reshape((480,640,3))

    matrix = get_transform(cfg)
    if matrix is not None:
        frame_proc = warp(frame, matrix, 300, 300)
    else:
        frame_proc = frame

    frame_proc = dynamic_mask(frame_proc, cfg)

    gray = cv2.cvtColor(frame_proc, cv2.COLOR_BGR2GRAY)

    if prev is None:
        prev = gray
        continue

    diff = cv2.absdiff(prev, gray)
    _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)

    motion = np.sum(thresh)/255

    edges = cv2.Canny(gray, 50, 150)
    edge = np.sum(edges)/255

    status = "OK"
    if motion > 100 and edge > 5000:
        status = "FAIL"

    # DEBUG OVERLAY (low cost)
    overlay = frame_proc.copy()
    cv2.putText(overlay, f"M:{int(motion)} E:{int(edge)} {status}",
                (10,20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,255,0),1)

    cv2.imwrite(DEBUG_PATH, overlay)

    print(status, motion, edge)

    prev = gray
    time.sleep(0.2)
