# ABA Visual Observation Demo

> **Live-session workstream (proposal under review):** this repository adds a
> separate live-session prototype on top of the demo below. Start with
> [LIVE_WORKSTREAM.md](LIVE_WORKSTREAM.md) for what was built, how to run it,
> what is verified, and the decisions that are pending.

An **experimental, recorded-video demonstration** for an ABA therapist. It is
not a clinical product, a diagnostic tool, a behavior-function assessment, or a
treatment recommender. A therapist selects the target child, reviews observable
evidence, and makes the decisions.

## What is implemented

- Recorded-video tracking/pose analysis, five experimental visual signals
  (`orientation`, `body_motion`, `out_of_seat`, `hand_motion`,
  `posture_change`), and a deterministic temporal alert engine. An uncertain
  target identity suppresses the signals; `null` means **not observable**, not
  absence of behavior.
- An optional visual-context and human-review workflow. Context is separate from
  measured observations and cannot determine target identity or fill
  unobservable signals.
- A **local precomputed replay dashboard** that pairs a video with its matching
  observation JSON using SHA-256. It replays existing observations; opening the
  dashboard does not run pose inference or upload the selected video.

**Not implemented:** a live camera/session runtime, continuous live inference, a
multi-user therapist application, production recording/storage governance, or
clinical validation. Passing synthetic/unit tests does not establish real-video
accuracy or live latency.

## Try the local replay

Use Python 3.11+ from the repository root:

```bash
python -m aba_demo.server
```

Open <http://127.0.0.1:8766>. The server listens on loopback only; do not expose
it as a hosted service. Import a local video and its **matching** precomputed
observation JSON. No real recordings or generated results are included in this
repository.

For a clearly labeled **synthetic UI fixture** (not model inference or child
footage), install `ffmpeg` and run:

```bash
python tests/make_ui_fixture.py
```

Then select `artifacts/ui-fixtures/SYNTHETIC_UI_TEST.mp4` and
`artifacts/ui-fixtures/SYNTHETIC_UI_TEST.json` in the dashboard. The generated
files are ignored by Git; never commit session media, observation exports, or
credentials.

## Tests

The replay server and temporal engine do not require the heavy vision stack. The
complete test suite uses Pillow for JPEG fixtures and Node.js for
served-JavaScript checks:

```bash
python -m pip install "Pillow>=10,<13"
python -m unittest discover -s tests -q
```

The suite has **172 tests** passing locally in this repository snapshot; these
are primarily synthetic/logic tests, not a clinical or real-video performance
benchmark.

## Optional recorded-video analysis

The reviewed [Colab notebook](notebooks/ABA_Colab_Ready.ipynb) and
[`aba_demo/vision.py`](aba_demo/vision.py) are the recorded-video analysis path.
Heavy inference dependencies are in
[`requirements-demo-vision.txt`](requirements-demo-vision.txt); approved
compute, pre-provisioned model weights, and an authorized recording are supplied
separately. A model specified only by filename may be downloaded if missing;
this demo does **not** enforce offline operation. Verify the weights and
dependency-download behavior before processing sensitive footage. To create the
notebook's code-only upload bundle without committing it:

```bash
python scripts/prepare_demo_colab_bundle.py --output ../demo_colab_bundle.zip
```

The notebook has no saved outputs. The bundle, model weights, recordings, and
analysis results are **not** repository assets. Follow the notebook's integrity
check when transferring the bundle.

## Privacy and scope

Do not upload identifiable child or therapist footage, notes, frame crops,
generated JSON, weights, `.env`, or API keys to this public repository. Using
Colab or the optional OpenRouter visual-context adapter can send data to third
parties; the context adapter may include a **full-scene frame** as well as a
target crop. Obtain explicit authorization covering everyone visible and the
chosen provider/data handling before any external processing of real footage.
Otherwise use synthetic media or an independently verified offline workflow;
this demo does not enforce offline inference. Review feedback and context review
decisions persist in this browser's `localStorage` until its site data is
cleared; the dashboard has no clinical record-management controls.

Alerts are candidate observations for therapist review, not conclusions about
attention, intent, behavior function, diagnosis, severity, or treatment. Team
handoff and proposed live-runtime documents are shared separately; they do not
describe features shipped in this repository.
