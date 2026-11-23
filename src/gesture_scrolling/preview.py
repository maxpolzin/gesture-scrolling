"""Optional tuning view: annotated camera frames as an MJPEG stream on http://localhost:PORT,
live state as JSON on /state, and a scroll test page with a detection timeline on /test."""
import collections
import json
import pathlib
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

COLORS = {"none": (160, 160, 160), "ready": (200, 200, 200), "scroll": (30, 90, 255), "zoom": (255, 120, 40)}
EDGES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11),
         (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]


class Preview:
    def __init__(self, port):
        self.jpg, self.cond = None, threading.Condition()
        self.samples = collections.deque(maxlen=400)     # (t, right hand seen, right palm spread, two palms, state)
        self.page = (pathlib.Path(__file__).parent / "testpage.html").read_bytes()
        prev = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def send(self, body, ctype):
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/test":
                    return self.send(prev.page, "text/html; charset=utf-8")
                if self.path == "/state":
                    return self.send(json.dumps(prev.state()).encode(), "application/json")
                if self.path != "/":
                    self.send_error(404); return
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=f")
                self.end_headers()
                try:
                    while True:
                        with prev.cond:
                            prev.cond.wait()
                            jpg = prev.jpg
                        self.wfile.write(b"--f\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    pass

        srv = ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()

    def state(self):
        now = time.monotonic()
        s = [x for x in self.samples if now - x[0] <= 10]
        return dict(state=s[-1][-1] if s else "none", hz=len(s) / 10,
                    timeline=[[round(now - x[0], 3), *x[1:]] for x in s])

    def show(self, frame, hands, state):
        right = next((h for h in hands if h["right"]), None)
        self.samples.append((time.monotonic(), right is not None, bool(right and right["spread"]),
                             sum(h["spread"] for h in hands) >= 2, state))
        img = cv2.flip(frame, 1)                      # mirror, like looking into a mirror
        h, w = img.shape[:2]
        for i, hand in enumerate(hands):
            pts = [(int((1 - p.x) * w), int(p.y * h)) for p in hand["landmarks"]]
            col = COLORS.get(state, (200, 200, 200)) if i == 0 else (120, 120, 120)
            for a, b in EDGES:
                cv2.line(img, pts[a], pts[b], col, 2)
            cv2.putText(img, f"{'R' if hand['right'] else 'L'} palm {hand['palm']:.2f} {'PALM' if hand['spread'] else ''}",
                        (pts[0][0] - 40, pts[0][1] + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1)
        cv2.putText(img, state.upper(), (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, COLORS.get(state), 2)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            with self.cond:
                self.jpg = buf.tobytes()
                self.cond.notify_all()
