# gesture-scrolling

Scroll and zoom **system-wide** on Linux with hand gestures in front of a webcam. It works in every app,
on Wayland and X11.

```
camera → hand model (MediaPipe) → filter → virtual mouse wheel (uinput)
```

| Hand (facing the camera) | Effect |
|---|---|
| open hand | ready, nothing happens |
| **fist**, then move it | **grab & drag**: the content follows your hand (up/down/left/right) |
| open the hand again | release; the content glides on a little (momentum) |
| **two fists**, pull apart / push together | **zoom** in / out (Ctrl + wheel) |

Only the biggest (nearest) hand counts; hands further away than about 1.5–2 m are ignored. Nothing is
recorded or sent anywhere.

## Run it

### Option A: uv (recommended, no root for the build)

[uv](https://docs.astral.sh/uv/) brings the right Python (3.12) by itself; MediaPipe has no wheels for newer
Pythons yet.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh     # once, if uv is missing
git clone https://github.com/maxpolzin/gesture-scrolling && cd gesture-scrolling
uv run gesture-scrolling --dry-run -v               # test: camera + gestures, no mouse, prints what it would do
uv run gesture-scrolling -v --preview 8765          # real: open http://localhost:8765 to see what it sees
```

The first start downloads the hand model (about 8 MB) to `~/.cache/gesture-scrolling/`.

### Option B: Docker

```bash
docker build -t gesture-scrolling .
docker run --rm -it --device /dev/video0 --device /dev/uinput --network host \
    gesture-scrolling -v --preview 8765
```

`--device /dev/uinput` lets the container create the virtual mouse on the host. `--network host` is only
needed for the preview.

## Permission for the virtual mouse (`/dev/uinput`)

Without Docker, your user needs write access to `/dev/uinput`. Either run once with `sudo`
(`sudo $(which uv) run gesture-scrolling`), or allow it permanently:

```bash
echo 'KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"' \
  | sudo tee /etc/udev/rules.d/60-gesture-scrolling.rules
sudo usermod -aG input "$USER"
sudo udevadm control --reload && sudo udevadm trigger /dev/uinput
# log out and back in (group change), then run without sudo
```

`--dry-run` needs no permission at all.

## Tuning

| Flag | Default | Meaning |
|---|---|---|
| `--camera N` | 0 | `/dev/videoN` (`v4l2-ctl --list-devices` shows them) |
| `--hz` | 12 | gesture updates per second; 10–15 is plenty, because the wheel is smoothed at 60 Hz |
| `--width/--height` | 320×240 | camera resolution; the model works on ~200 px internally, so low is fine |
| `--gain` | 30 | wheel notches per full frame of hand travel (scroll speed) |
| `--zoom-gain` | 6 | zoom notches per e-fold of hand distance (zoom speed; lower = calmer) |
| `--min-palm` | 0.08 | ignore smaller (further) hands; raise it if people in the background trigger it |
| `--glide` | 0.10 | smoothing time constant of the wheel in seconds (higher = smoother, more delay) |
| `--invert` | off | flip the scroll direction |
| `--preview PORT` | off | annotated camera view at `http://localhost:PORT` (hand skeleton, state, palm size) |
| `-v` | off | state changes plus every 5 s: achieved Hz and CPU load |

Reference: on an Intel N95 mini PC (Ubuntu 26.04, Logitech Brio 105) it runs at 12 Hz using about 38 % of one
core.

## How it works (for tuning or changing it)

- `pipeline.py`:
  - **Camera:** OpenCV/V4L2, MJPG, keeps only the newest frame. It switches off the webcam's
    `exposure_dynamic_framerate`, because many webcams otherwise drop to about 7 fps in dim rooms, which made
    tracking crawl at 4 Hz.
  - **Hand model:** MediaPipe HandLandmarker, 2 hands. A fist means ≥3 fingertips closer to the wrist than
    their middle joints.
  - **Filter:** a One Euro filter on the hand position (smooth when slow, responsive when fast).
  - **State machine:** ready → grab → release; two fists of similar size → zoom.
- `mouse.py`: receives scroll requests at about 12 Hz and **glides** them out at 60 Hz as small hi-res wheel
  steps (`REL_WHEEL_HI_RES`, 1/120 notch), with momentum after release. That's why it's smooth even though the
  camera side runs slowly.
- `uinput.py`: pure-Python `/dev/uinput` mouse (no compiler, no evdev).
- `preview.py`: MJPEG preview server (only with `--preview`).

## Troubleshooting

- **`camera 0 not available`:** wrong index or the camera is in use (close the video call). Try `--camera 2`.
- **`no permission for /dev/uinput`:** see the permission section above, or use `--dry-run`.
- **Reacts to people walking by:** raise `--min-palm` (e.g. 0.12), so you have to be closer.
- **Too fast or too jumpy:** lower `--gain`, raise `--glide` (e.g. 0.15).
- **Scrolls the wrong way:** `--invert`.

## Notes for a coding assistant working on this repo

- Entry point: `gesture_scrolling.cli:main`. Run `uv run gesture-scrolling --dry-run -v` to test without
  touching the desktop.
- Keep the chain simple: camera → hand model → filter → virtual mouse. Smoothing belongs in `mouse.py` (60 Hz
  glide), detection rate in `--hz`.
- Wheel sign: `REL_WHEEL` > 0 scrolls up; drag semantics mean hand up → content up → scroll down.
- Requires Linux (uinput). Python 3.10–3.12 because of MediaPipe wheels.
