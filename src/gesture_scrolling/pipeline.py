"""camera -> hand model -> filter -> virtual mouse wheel.

Gestures (seen from the person facing the camera):
  right open palm      (all five fingers spread) scroll: the content follows the hand, both axes
  two open palms       zoom: hands apart / together = zoom in / out (Ctrl + wheel)
  palm(s) closed > 0.5 s   stop, then no new start for `cooldown` seconds
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
        """-> list of dicts sorted by size: palm (length, 0..1), spread (all five fingers out), right (person's
        right hand), x, y (0..1, x mirrored so moving right = x grows), landmarks."""
        img = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
        res = self.lm.detect_for_video(img, int((time.monotonic() - self.t0) * 1000))
        out = []
        for lm, hd in zip(res.hand_landmarks, res.handedness):
            w = lm[0]
            d = lambda a, b: math.hypot(a.x - b.x, a.y - b.y)
            palm = d(w, lm[9])
            # open palm, relaxed: four fingers straight-ish, thumb not tucked into the palm
            fingers = all(d(lm[tip], w) > d(lm[pip], w) * 1.05 for tip, pip in ((8, 6), (12, 10), (16, 14), (20, 18)))
            thumb = d(lm[4], lm[5]) > 0.35 * palm
            pts = (0, 5, 9, 13, 17)
            out.append(dict(palm=palm, spread=fingers and thumb,
                            right=hd[0].category_name == "Right",
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

    START = 0.25                         # s the gesture must be seen to start
    STOP = 0.5                           # s without the gesture to stop

    def __init__(self, gain=30.0, min_palm=0.08, zoom_gain=6.0, cooldown=1.0, modes=("scroll", "zoom")):
        self.gain, self.min_palm, self.zoom_gain, self.cooldown = gain, min_palm, zoom_gain, cooldown
        self.modes = modes
        self.fx, self.fy = OneEuro(), OneEuro()
        self.state, self.px, self.py, self.zd = "none", None, None, None   # none / ready / scroll / zoom
        self.since = {"scroll": None, "zoom": None}       # when each gesture was first seen (continuously)
        self.lost_since, self.blocked_until = None, 0.0

    def _start(self, mode):
        self.state, self.px, self.zd, self.lost_since = mode, None, None, None

    def update(self, hands, t):
        """-> (state, scroll_dx, scroll_dy, zoom_notches); scroll in notches, + = right/down."""
        hands = [h for h in hands if h["palm"] >= self.min_palm]
        two = len(hands) >= 2 and hands[1]["palm"] > 0.65 * hands[0]["palm"]
        right = next((h for h in hands if h["right"]), None)
        sig = {"zoom": "zoom" in self.modes and two and hands[0]["spread"] and hands[1]["spread"]}
        sig["scroll"] = "scroll" in self.modes and right is not None and right["spread"] and not sig["zoom"]
        for m in sig:
            self.since[m] = (self.since[m] or t) if sig[m] else None
        held = lambda m: self.since[m] is not None and t - self.since[m] >= self.START

        if self.state in ("scroll", "zoom"):
            self.lost_since = None if sig[self.state] else (self.lost_since or t)
            if self.state == "scroll" and held("zoom"):
                self._start("zoom")                         # second palm joins: straight to zoom
            elif self.lost_since is not None and t - self.lost_since > self.STOP:
                self.state, self.blocked_until = "ready", t + self.cooldown
        if self.state not in ("scroll", "zoom") and t >= self.blocked_until:
            for m in ("zoom", "scroll"):
                if held(m):
                    self._start(m)
                    break
        if self.state not in ("scroll", "zoom"):
            self.state = "ready" if hands else "none"

        if self.state == "zoom":
            if not two:                                     # re-reference when both hands are back
                self.zd = None
                return self.state, 0.0, 0.0, 0.0
            a, b = hands[0], hands[1]
            d = math.hypot(a["x"] - b["x"], a["y"] - b["y"]) / ((a["palm"] + b["palm"]) / 2)
            if self.zd is None:
                self.zd = d
                return self.state, 0.0, 0.0, 0.0
            n = int(math.log(d / self.zd) * self.zoom_gain)   # apart -> positive -> zoom in
            if n:
                self.zd *= math.exp(n / self.zoom_gain)      # keep the remainder for next time
            return self.state, 0.0, 0.0, float(n)

        if right is None:                                   # don't jump when the hand comes back
            self.px = None
            self.fx.reset(); self.fy.reset()
            return self.state, 0.0, 0.0, 0.0
        x, y = self.fx(right["x"], t), self.fy(right["y"], t)
        if self.state != "scroll" or self.px is None:
            self.px, self.py = x, y
            return self.state, 0.0, 0.0, 0.0
        dx, dy = (x - self.px) * self.gain, (y - self.py) * self.gain
        self.px, self.py = x, y
        # drag: content follows the hand -> scroll the opposite way
        return self.state, -dx, -dy, 0.0
