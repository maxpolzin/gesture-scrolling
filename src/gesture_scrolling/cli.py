"""gesture-scrolling: scroll and zoom system-wide with hand gestures in front of a webcam."""
import argparse
import os
import resource
import threading
import time

os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

from .mouse import VirtualMouse            # noqa: E402
from .pipeline import Camera, Gestures, Hands  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--camera", type=int, default=0, help="video device index (/dev/videoN)")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--hz", type=float, default=12, help="gesture updates per second")
    ap.add_argument("--gain", type=float, default=30, help="wheel notches per full frame of hand travel")
    ap.add_argument("--min-palm", type=float, default=0.08,
                    help="ignore hands smaller than this (palm length / frame height); raise = must be closer")
    ap.add_argument("--cooldown", type=float, default=1.0,
                    help="seconds after scrolling stops before it can start again")
    ap.add_argument("--zoom-gain", type=float, default=6, help="zoom notches per e-fold of hand distance")
    ap.add_argument("--only", choices=("scroll", "zoom"), help="enable only one gesture (for testing)")
    ap.add_argument("--glide", type=float, default=0.10, help="smoothing time constant of the wheel (s)")
    ap.add_argument("--invert", action="store_true", help="flip scroll direction")
    ap.add_argument("--dry-run", action="store_true", help="no virtual mouse; only print what would happen")
    ap.add_argument("--preview", type=int, metavar="PORT",
                    help="serve an annotated camera preview on http://localhost:PORT")
    ap.add_argument("--verbose", "-v", action="store_true", help="print every state change and 5 s stats")
    a = ap.parse_args()

    cam = Camera(a.camera, a.width, a.height)
    hands = Hands()
    gest = Gestures(gain=a.gain, min_palm=a.min_palm, zoom_gain=a.zoom_gain, cooldown=a.cooldown,
                    modes=(a.only,) if a.only else ("scroll", "zoom"))
    mouse = VirtualMouse(dry_run=a.dry_run, glide=a.glide, invert=a.invert)
    preview = None
    if a.preview:
        from .preview import Preview
        preview = Preview(a.preview)
        print(f"preview: http://localhost:{a.preview}", flush=True)

    print(f"gesture-scrolling running at {a.hz:g} Hz, camera {a.camera} {a.width}x{a.height}"
          f"{' (dry run)' if a.dry_run else ''}. Ctrl+C to stop.", flush=True)
    period = 1.0 / a.hz
    last_state, n, t_stat = None, 0, time.monotonic()
    cpu0 = resource.getrusage(resource.RUSAGE_SELF)
    try:
        while True:
            t = time.monotonic()
            frame = cam.read()
            if frame is None:
                time.sleep(0.2)
                continue
            found = hands.detect(frame)
            state, dx, dy, zoom = gest.update(found, t)
            mouse.scroll(dx, dy, grabbing=(state == "scroll"))
            if zoom:
                threading.Thread(target=mouse.zoom, args=(zoom,), daemon=True).start()
            if preview:
                preview.show(frame, found, state)
            if a.verbose and state != last_state:
                print(f"{time.strftime('%H:%M:%S')} {state}", flush=True)
            if a.dry_run and (dx or dy or zoom):
                print(f"{state:6s} scroll dx={dx:+.2f} dy={dy:+.2f} notches  zoom={zoom:+.2f}", flush=True)
            last_state = state
            n += 1
            if a.verbose and t - t_stat >= 5:
                cpu1 = resource.getrusage(resource.RUSAGE_SELF)
                cpu = (cpu1.ru_utime + cpu1.ru_stime - cpu0.ru_utime - cpu0.ru_stime) / (t - t_stat)
                print(f"stats: {n / (t - t_stat):.1f} Hz, CPU {cpu * 100:.0f} % of one core", flush=True)
                n, t_stat, cpu0 = 0, t, cpu1
            time.sleep(max(0.0, period - (time.monotonic() - t)))
    except KeyboardInterrupt:
        pass
    finally:
        mouse.close()


if __name__ == "__main__":
    main()
