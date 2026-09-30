# Live session UI (simulation)

Therapist-facing live view for the experimental live-session shell in
`aba_demo/live/`. **Simulation only:** the source and observations are
synthetic, there is no camera or model inference, and nothing is recorded.

Stack: React 19, TypeScript, Vite, Tailwind CSS 4, Motion, lucide icons.
Fonts are bundled locally; the page makes no third-party requests.

## Run

From the repository root, in an isolated Python environment:

```bash
python -m pip install -r requirements-live.txt
```

Build the UI once, then serve it together with the API on loopback:

```bash
cd live-ui && npm ci && npm run build && cd ..
python -m aba_demo.live
```

Open <http://127.0.0.1:8767>.

To add a **precomputed replay** scenario (file-as-live), pass a local video and
the precomputed observation JSON exported for it. The pair is checked by
SHA-256 at startup; the video is decoded locally and never shown or uploaded.
Needs `opencv-python-headless`. Use synthetic or authorized media only.

```bash
python -m aba_demo.live --replay-video path/to/video.mp4 --replay-observations path/to/observations.json
```

For UI development with hot reload, run the API with `--dev` (accepts the Vite
origin) and the Vite dev server in a second terminal:

```bash
python -m aba_demo.live --dev
cd live-ui && npm run dev
```

Open <http://localhost:5173>.

## Check

```bash
npm run typecheck
npm test
```

Backend tests live in `tests/test_live_session.py` and `tests/test_live_api.py`
and run with the main suite (`python -m unittest discover -s tests -v`). API
tests are skipped when `requirements-live.txt` is not installed.

## Interface principles

- The simulation status is always visible (header badge, stage label, summary).
- "Not observable" is visually distinct from "observed · none" (hatched tile).
- Candidate cards come only from the engine's alert output, one at a time, in
  a peripheral panel. They are cleared or marked unavailable when identity is
  uncertain, the stream is interrupted, or the session ends.
- Attention uses amber; red is reserved for system failures, never behavior.
- Ending a session needs a second confirming click. `Space` pauses and resumes.
- Respects `prefers-reduced-motion` and `prefers-color-scheme`.
