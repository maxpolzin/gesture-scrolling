"""camera -> hand model -> filter -> virtual mouse wheel.

Gestures (seen from the person facing the camera):
  open hand            ready (nothing happens)
  fist + move          grab & drag: content follows the hand, both axes
  open hand again      release, with a little momentum
  two fists apart/in   zoom in/out (Ctrl + wheel)
"""
import math
import os
import shutil
import subprocess
import time
import urllib.request

import cv2
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions, vision

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/latest/hand_landmarker.task")


# ----------------------------------------------------------------- 1. camera
class Camera:
    """Always returns the newest frame (no stale buffered frames at low processing rates)."""

    def __init__(self, index=0, width=320, height=240):
        self.cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)   # keep only the newest frame (no stale backlog)
        if not self.cap.isOpened():
            raise SystemExit(f"camera {index} not available")
        # Many UVC webcams lower their frame rate in dim light ("dynamic framerate"), which made
        # tracking crawl at ~4 Hz. Keep the full frame rate; the hand model copes with a darker image.
        if shutil.which("v4l2-ctl"):
            subprocess.run(["v4l2-ctl", "-d", f"/dev/video{index}", "-c", "exposure_dynamic_framerate=0"],
                           capture_output=True)

    def read(self):
        ok, frame = self.cap.read()
        return frame if ok else None


# ----------------------------------------------------------------- 2. hand model
def model_path():
    p = os.path.expanduser("~/.cache/gesture-scrolling/hand_landmarker.task")
    if not os.path.exists(p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        print("downloading hand model …", flush=True)
        urllib.request.urlretrieve(MODEL_URL, p)
    return p


class Hands:
    def __init__(self):
        self.lm = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=model_path()),
            running_mode=vision.RunningMode.VIDEO, num_hands=2,
            min_hand_detection_confidence=0.55, min_tracking_confidence=0.5))
        self.t0 = time.monotonic()

    def detect(self, frame_bgr):
        """-> list of dicts sorted by size: palm (length, 0..1), fist (bool), open (bool), x, y (0..1,
        x mirrored so moving right = x grows), landmarks."""
        img = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
        res = self.lm.detect_for_video(img, int((time.monotonic() - self.t0) * 1000))
        out = []
        for lm in res.hand_landmarks:
            w = lm[0]
            d = lambda a, b: math.hypot(a.x - b.x, a.y - b.y)
            curled = sum(1 for tip, pip in ((8, 6), (12, 10), (16, 14), (20, 18))
                         if d(lm[tip], w) < d(lm[pip], w) * 1.05)
            pts = (0, 5, 9, 13, 17)
            out.append(dict(palm=d(w, lm[9]), fist=curled >= 3, open=curled <= 1,
                            x=1 - sum(lm[i].x for i in pts) / 5, y=sum(lm[i].y for i in pts) / 5,
                            landmarks=lm))
        return sorted(out, key=lambda h: -h["palm"])


# ----------------------------------------------------------------- 3. filter + gestures
class OneEuro:
    """One Euro filter: smooth when slow, responsive when fast (Casiez et al. 2012)."""

    def __init__(self, min_cutoff=1.0, beta=4.0, d_cutoff=1.0):
        self.mc, self.beta, self.dc = min_cutoff, beta, d_cutoff
        self.x = self.dx = self.t = None

    @staticmethod
    def _a(cutoff, dt):
        r = 2 * math.pi * cutoff * dt
        return r / (r + 1)

    def __call__(self, x, t):
        if self.x is None:
            self.x, self.dx, self.t = x, 0.0, t
            return x
        dt = max(1e-3, t - self.t)
        self.t = t
        dx = (x - self.x) / dt
        self.dx += self._a(self.dc, dt) * (dx - self.dx)
        cutoff = self.mc + self.beta * abs(self.dx)
        self.x += self._a(cutoff, dt) * (x - self.x)
        return self.x

    def reset(self):
        self.x = None


class Gestures:
    """Turns hand detections into scroll / zoom requests (in wheel notches)."""

    def __init__(self, gain=30.0, min_palm=0.08, zoom_gain=6.0):
        self.gain, self.min_palm, self.zoom_gain = gain, min_palm, zoom_gain
        self.fx, self.fy = OneEuro(), OneEuro()
        self.state, self.px, self.py, self.zd = "none", None, None, None
        self.fist_n = self.open_n = self.zoom_n = 0
        self.lost_since = None

    def update(self, hands, t):
        """-> (state, scroll_dx, scroll_dy, zoom_notches); scroll in notches, + = right/down."""
        hands = [h for h in hands if h["palm"] >= self.min_palm]
        if not hands:
            self.lost_since = self.lost_since or t
            if t - self.lost_since > 0.4:
                self.state = "none"
                self.fx.reset(); self.fy.reset()
            return self.state, 0.0, 0.0, 0.0
        self.lost_since = None

        two = (len(hands) >= 2 and hands[1]["palm"] > 0.65 * hands[0]["palm"]
               and hands[0]["fist"] and hands[1]["fist"])
        self.zoom_n = self.zoom_n + 1 if two else 0
        if self.zoom_n >= 2:
            d = math.hypot(hands[0]["x"] - hands[1]["x"], hands[0]["y"] - hands[1]["y"]) / hands[0]["palm"]
            if self.state != "zoom":
                self.state, self.zd = "zoom", d
                return self.state, 0.0, 0.0, 0.0
            z = math.log(d / self.zd) * self.zoom_gain     # apart -> positive -> zoom in
            if abs(z) < 0.25:
                return self.state, 0.0, 0.0, 0.0
            self.zd = d
            return self.state, 0.0, 0.0, z
        if self.state == "zoom":
            self.state, self.px = "ready", None
            self.fx.reset(); self.fy.reset()

        h = hands[0]
        x, y = self.fx(h["x"], t), self.fy(h["y"], t)
        self.fist_n = self.fist_n + 1 if h["fist"] else 0
        self.open_n = self.open_n + 1 if h["open"] else 0
        if self.state in ("none", "ready"):
            if self.fist_n >= 2:
                self.state, self.px, self.py = "grab", x, y
            elif self.open_n >= 1:
                self.state = "ready"
            return self.state, 0.0, 0.0, 0.0
        if self.open_n >= 2:                                # release
            self.state = "ready"
            return self.state, 0.0, 0.0, 0.0
        dx, dy = (x - self.px) * self.gain, (y - self.py) * self.gain
        self.px, self.py = x, y
        # drag: content follows the hand -> scroll the opposite way
        return self.state, -dx, -dy, 0.0
