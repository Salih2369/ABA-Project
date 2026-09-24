"""Generate unmistakably synthetic browser-test inputs, never clinical evidence."""
import hashlib
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
out = root / 'artifacts' / 'ui-fixtures'
out.mkdir(parents=True, exist_ok=True)
video = out / 'SYNTHETIC_UI_TEST.mp4'
subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=10', '-t', '20', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)], check=True, capture_output=True)
observations = []
for n in range(81):
    t = n / 4
    signals = {'orientation': 2 <= t < 8, 'body_motion': 9 <= t < 15, 'out_of_seat': 16 <= t < 19, 'hand_motion': None, 'posture_change': False}
    if 8 <= t < 9:
        signals = dict.fromkeys(signals, None)
    observations.append({'time': t, 'identity': 'uncertain' if 8 <= t < 9 else 'confirmed', 'signals': signals, 'values': {}})
bundle = {'schema_version': 1, 'mode': 'precomputed', 'provenance': 'SYNTHETIC ENGINEERING FIXTURE — not model inference or child footage', 'source': {'name': video.name, 'sha256': hashlib.sha256(video.read_bytes()).hexdigest()}, 'observations': observations}
(out / 'SYNTHETIC_UI_TEST.json').write_text(json.dumps(bundle, indent=2), encoding='utf-8')
(out / 'INVALID.json').write_text('{invalid', encoding='utf-8')
print(json.dumps({'video': str(video), 'observations': str(out / 'SYNTHETIC_UI_TEST.json'), 'frames': len(observations), 'provenance': bundle['provenance']}))
