# gesture-scrolling

Scroll and zoom **system-wide** on Linux with hand gestures in front of a webcam. It works in every app,
on Wayland and X11.

```
camera → hand model (MediaPipe) → filter → virtual mouse wheel (uinput)
```

| Hands (facing the camera) | Effect |
|---|---|
| **right open palm** | **scroll**: the content follows your hand (up/down/left/right) |
| **both open palms** | **zoom**: pull the hands apart / push them together to zoom in / out (Ctrl + wheel) |
| close the palm(s) or drop the hand(s) for > 0.5 s | stop; scrolling glides on a little (momentum) |

An open palm means four fingers roughly straight and the thumb not tucked in; it doesn't need to be stretched.
A gesture has to be seen for 0.25 s to start, and after a stop nothing starts for 1 s (`--cooldown`), so it
doesn't flicker on and off. Raising the left palm while scrolling switches straight to zoom. The left hand alone
does nothing.

Hands further away than about 1.5–2 m are ignored. Nothing is recorded or sent anywhere.

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

While changing the code, mount `src/` over the installed package; then a `docker restart` (about 2 s) picks up
every change and the image only needs a rebuild when dependencies change:

```bash
docker run -d --name gesture-scrolling --device /dev/video0 --device /dev/uinput --network host \
    -v "$PWD/src:/app/src:ro" -e PYTHONPATH=/app/src gesture-scrolling -v --preview 8765
docker restart gesture-scrolling        # after each change
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
| `--width/--height` | 640×480 | camera resolution; 320×240 is cheaper but loses the hand more often |
| `--gain` | 30 | wheel notches per full frame of hand travel (scroll speed) |
| `--min-palm` | 0.08 | ignore smaller (further) hands; raise it if people in the background trigger it |
| `--zoom-gain` | 6 | zoom notches per e-fold of hand distance (zoom speed; lower = calmer) |
| `--cooldown` | 1.0 | seconds after a stop before the next scroll or zoom can start |
| `--only scroll\|zoom` | both | enable only one gesture (for testing) |
| `--glide` | 0.10 | smoothing time constant of the wheel in seconds (higher = smoother, more delay) |
| `--invert` | off | flip the scroll direction |
| `--preview PORT` | off | annotated camera view at `http://localhost:PORT`, test page at `/test` (see below) |
| `-v` | off | state changes plus every 5 s: achieved Hz and CPU load |

Reference: on an Intel N95 mini PC (Ubuntu 26.04, Logitech Brio 105) it runs at 12 Hz using about 38 % of one
core at 320×240; on a laptop at 640×480 about 65 %.

### Test page

With `--preview 8765`, open `http://localhost:8765/test`: a page of wide, long text to scroll in both directions
and to zoom (Ctrl + wheel zooms only the text there). A floating panel shows the camera, the current mode, the
wheel events that arrive, and a 10 s timeline of *right hand seen / right palm open / two palms / mode*. Gaps in
that timeline show whether the hand model loses the hand or the palm check is too strict.

## How it works (for tuning or changing it)

- `pipeline.py`:
  - **Camera:** OpenCV/V4L2, MJPG, keeps only the newest frame. It switches off the webcam's
    `exposure_dynamic_framerate`, because many webcams otherwise drop to about 7 fps in dim rooms, which made
    tracking crawl at 4 Hz.
  - **Hand model:** MediaPipe HandLandmarker, 2 hands, with handedness. Open palm: each fingertip ≥5 % further
    from the wrist than its middle joint, and the thumb tip more than 0.35 palm lengths from the index knuckle.
  - **Filter:** a One Euro filter on the hand position (smooth when slow, responsive when fast).
  - **State machine:** ready → scroll (right palm 0.25 s) or zoom (two palms 0.25 s) → ready (gesture gone
    for 0.5 s) → `--cooldown`. Zoom follows the log of the hand distance in palm lengths and is sent as whole
    Ctrl + wheel notches right away (an attempt to buffer and spread them over 1 s made zoom stop working).
- `mouse.py`: receives scroll requests at about 12 Hz and **glides** them out at 60 Hz as small hi-res wheel
  steps (`REL_WHEEL_HI_RES`, 1/120 notch), with momentum after release. That's why it's smooth even though the
  camera side runs slowly.
- `uinput.py`: pure-Python `/dev/uinput` mouse (no compiler, no evdev).
- `preview.py`: MJPEG preview, `/state` JSON and the `/test` page (`testpage.html`), only with `--preview`.

## Troubleshooting

- **`camera 0 not available`:** wrong index or the camera is in use (close the video call). Try `--camera 2`.
- **`no permission for /dev/uinput`:** see the permission section above, or use `--dry-run`.
- **Reacts to people walking by:** raise `--min-palm` (e.g. 0.12), so you have to be closer.
- **Too fast or too jumpy:** lower `--gain`, raise `--glide` (e.g. 0.15); for zoom lower `--zoom-gain`.
- **Stops scrolling by itself:** open `/test` and look at the timeline: red gaps = hand lost (more light, come
  closer); grey gaps in *right palm* = the palm check is too strict for your hand (`Hands.detect` in `pipeline.py`).
- **Scrolls the wrong way:** `--invert`.

## Notes for a coding assistant working on this repo

- Entry point: `gesture_scrolling.cli:main`. Run `uv run gesture-scrolling --dry-run -v` to test without
  touching the desktop.
- Keep the chain simple: camera → hand model → filter → virtual mouse. Smoothing belongs in `mouse.py` (60 Hz
  glide), detection rate in `--hz`.
- Wheel sign: `REL_WHEEL` > 0 scrolls up; drag semantics mean hand up → content up → scroll down.
- Requires Linux (uinput). Python 3.10–3.12 because of MediaPipe wheels.
