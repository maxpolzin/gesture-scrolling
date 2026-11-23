"""Optional tuning view: annotated camera frames as an MJPEG stream on http://localhost:PORT."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

COLORS = {"none": (160, 160, 160), "ready": (200, 200, 200), "grab": (30, 90, 255), "zoom": (255, 120, 40)}
EDGES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11),
         (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]


class Preview:
    def __init__(self, port):
        self.jpg, self.cond = None, threading.Condition()
        prev = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
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

    def show(self, frame, hands, state):
        img = cv2.flip(frame, 1)                      # mirror, like looking into a mirror
        h, w = img.shape[:2]
        for i, hand in enumerate(hands):
            pts = [(int((1 - p.x) * w), int(p.y * h)) for p in hand["landmarks"]]
            col = COLORS.get(state, (200, 200, 200)) if i == 0 else (120, 120, 120)
            for a, b in EDGES:
                cv2.line(img, pts[a], pts[b], col, 2)
            cv2.putText(img, f"palm {hand['palm']:.2f} {'fist' if hand['fist'] else 'open' if hand['open'] else ''}",
                        (pts[0][0] - 40, pts[0][1] + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1)
        cv2.putText(img, state.upper(), (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, COLORS.get(state), 2)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            with self.cond:
                self.jpg = buf.tobytes()
                self.cond.notify_all()
