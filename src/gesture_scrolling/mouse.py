"""4. Virtual mouse (Linux uinput): smooth hi-res wheel scrolling, Ctrl+wheel zoom.

Gesture updates arrive at ~12 Hz. A 60 Hz loop glides the wheel towards the requested amount
(critically damped, ~0.1 s), so the system sees many small hi-res steps instead of a few jumps.
After a release the last velocity decays smoothly (momentum).
"""
import threading
import time

HI_RES = 120  # one wheel notch in REL_WHEEL_HI_RES units


class _Dry:
    def write(self, *a): pass
    def syn(self): pass
    def close(self): pass


class VirtualMouse:
    def __init__(self, dry_run=False, rate=60.0, glide=0.10, friction=0.90, invert=False):
        self.rate, self.glide, self.friction, self.sign = rate, glide, friction, (-1 if invert else 1)
        if dry_run:
            self.dev, self.e = _Dry(), None
        else:
            from . import uinput as e
            self.e = e
            self.dev = e.UInput()
        self.lock = threading.Lock()
        self.pending = [0.0, 0.0]     # notches still to emit (x, y)
        self.vel = [0.0, 0.0]         # notches per second, for momentum
        self.acc = [0, 0]             # hi-res units since the last full notch (legacy REL_WHEEL)
        self.grabbing = False
        self.emitted = [0.0, 0.0]     # for stats
        self.stop = False
        threading.Thread(target=self._loop, daemon=True).start()

    # ---- called by the gesture loop
    def scroll(self, dx, dy, grabbing):
        """dx, dy in notches; + = right / down (content moves left / up)."""
        with self.lock:
            self.pending[0] += dx
            self.pending[1] += dy
            if grabbing and (dx or dy):
                k = 0.5
                self.vel = [(1 - k) * self.vel[0] + k * dx * 12, (1 - k) * self.vel[1] + k * dy * 12]
            if grabbing and not self.grabbing:
                self.vel = [0.0, 0.0]
            self.grabbing = grabbing

    def zoom(self, notches):
        if self.e is None or not notches:
            return
        e, n = self.e, int(round(notches))
        if not n:
            return
        self.dev.write(e.EV_KEY, e.KEY_LEFTCTRL, 1); self.dev.syn()
        for _ in range(abs(n)):
            self.dev.write(e.EV_REL, e.REL_WHEEL_HI_RES, HI_RES if n > 0 else -HI_RES)
            self.dev.write(e.EV_REL, e.REL_WHEEL, 1 if n > 0 else -1)
            self.dev.syn()
            time.sleep(0.01)
        self.dev.write(e.EV_KEY, e.KEY_LEFTCTRL, 0); self.dev.syn()

    # ---- 60 Hz glide loop
    def _loop(self):
        dt = 1.0 / self.rate
        a = 1 - pow(2.718281828, -dt / self.glide)     # fraction of the pending amount per tick
        while not self.stop:
            with self.lock:
                if not self.grabbing:                    # momentum after release
                    for i in (0, 1):
                        self.pending[i] += self.vel[i] * dt
                        self.vel[i] *= self.friction
                        if abs(self.vel[i]) < 0.05:
                            self.vel[i] = 0.0
                step = [self.pending[0] * a, self.pending[1] * a]
                self.pending = [self.pending[0] - step[0], self.pending[1] - step[1]]
            self._emit(step[0], step[1])
            time.sleep(dt)

    def _emit(self, nx, ny):
        if self.e is None:
            self.emitted[0] += nx; self.emitted[1] += ny
            return
        e = self.e
        # wheel axes: REL_WHEEL > 0 scrolls up, REL_HWHEEL > 0 scrolls right
        ux = int(round(nx * HI_RES)) * self.sign
        uy = int(round(-ny * HI_RES)) * self.sign
        if not ux and not uy:
            return
        if uy:
            self.dev.write(e.EV_REL, e.REL_WHEEL_HI_RES, uy)
            self.acc[1] += uy
            while abs(self.acc[1]) >= HI_RES:
                s = 1 if self.acc[1] > 0 else -1
                self.dev.write(e.EV_REL, e.REL_WHEEL, s); self.acc[1] -= s * HI_RES
        if ux:
            self.dev.write(e.EV_REL, e.REL_HWHEEL_HI_RES, ux)
            self.acc[0] += ux
            while abs(self.acc[0]) >= HI_RES:
                s = 1 if self.acc[0] > 0 else -1
                self.dev.write(e.EV_REL, e.REL_HWHEEL, s); self.acc[0] -= s * HI_RES
        self.dev.syn()

    def close(self):
        self.stop = True
        self.dev.close()
