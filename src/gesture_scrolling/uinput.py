"""Minimal pure-Python Linux uinput device (no compiler, no evdev): a mouse with hi-res wheels + Ctrl."""
import fcntl
import os
import struct
import time

# linux/input-event-codes.h
EV_SYN, EV_KEY, EV_REL = 0x00, 0x01, 0x02
SYN_REPORT = 0
REL_X, REL_Y, REL_HWHEEL, REL_WHEEL, REL_WHEEL_HI_RES, REL_HWHEEL_HI_RES = 0x00, 0x01, 0x06, 0x08, 0x0B, 0x0C
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE, KEY_LEFTCTRL = 0x110, 0x111, 0x112, 29
BUS_VIRTUAL = 0x06

# linux/uinput.h ioctls
def _IOW(t, nr, size): return (1 << 30) | (size << 16) | (ord(t) << 8) | nr
def _IO(t, nr): return (ord(t) << 8) | nr
UI_SET_EVBIT = _IOW("U", 100, 4)
UI_SET_KEYBIT = _IOW("U", 101, 4)
UI_SET_RELBIT = _IOW("U", 102, 4)
UI_DEV_SETUP = _IOW("U", 3, 92)         # struct uinput_setup: input_id(4×u16) + name[80] + u32
UI_DEV_CREATE = _IO("U", 1)
UI_DEV_DESTROY = _IO("U", 2)

_EVENT = struct.Struct("llHHi")         # struct input_event (64-bit time)


class UInput:
    def __init__(self, name="gesture-scrolling virtual mouse"):
        try:
            self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        except PermissionError:
            raise SystemExit("no permission for /dev/uinput: see README (udev rule or run with sudo)")
        for ev in (EV_KEY, EV_REL):
            fcntl.ioctl(self.fd, UI_SET_EVBIT, ev)
        for k in (BTN_LEFT, BTN_RIGHT, BTN_MIDDLE, KEY_LEFTCTRL):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, k)
        for r in (REL_X, REL_Y, REL_WHEEL, REL_HWHEEL, REL_WHEEL_HI_RES, REL_HWHEEL_HI_RES):
            fcntl.ioctl(self.fd, UI_SET_RELBIT, r)
        setup = struct.pack("HHHH80sI", BUS_VIRTUAL, 0x1209, 0x6573, 1, name.encode()[:79], 0)
        fcntl.ioctl(self.fd, UI_DEV_SETUP, setup)
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        time.sleep(0.3)                  # let the desktop pick up the new device

    def write(self, etype, code, value):
        os.write(self.fd, _EVENT.pack(0, 0, etype, code, value))

    def syn(self):
        self.write(EV_SYN, SYN_REPORT, 0)

    def close(self):
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        finally:
            os.close(self.fd)
