"""Export real local-video pose observations for explicitly precomputed playback.

Run: python -m aba_demo.export --video approved.mp4 --config config.json --output observations.json
No upload API, mock inference, automatic target choice, or diagnostic labels.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

from .vision import VideoAnalyzer, resolve_tracker_profile, validate_config


MAX_TRAILING_DECODE_GAP_FRAMES = 3


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def export_video(video, config, output):
    config = validate_config(config)
    click = config.get('target_click')
    if (not isinstance(click, (list, tuple)) or len(click) != 2
            or not all(type(v) in (float, int) and math.isfinite(v) and 0 <= v <= 1 for v in click)):
        raise ValueError('config.target_click must be an explicit normalized [x, y] on the first frame')
    reselections = config.get('target_reselections', [])
    if not isinstance(reselections, list):
        raise ValueError('target_reselections must be an ordered list of {time, click}')
    previous = -1
    for event in reselections:
        if not isinstance(event, dict):
            raise ValueError('Each reselection must be {time, click}')
        t, point = event.get('time'), event.get('click')
        if (type(t) not in (float, int) or not math.isfinite(t) or t < 0 or t <= previous
                or not isinstance(point, (tuple, list)) or len(point) != 2
                or not all(type(v) in (float, int) and math.isfinite(v) and 0 <= v <= 1 for v in point)):
            raise ValueError('Reselections need unique increasing nonnegative times and normalized clicks')
        previous = t
    sample_hz = config.get('sample_hz', 5)
    if type(sample_hz) not in (int, float) or not math.isfinite(sample_hz) or sample_hz <= 0:
        raise ValueError('sample_hz must be finite and positive')
    reviewed_target_id = config.get('reviewed_target_track_id')
    if reviewed_target_id is not None and (type(reviewed_target_id) is not int
                                            or reviewed_target_id < 0):
        raise ValueError('reviewed_target_track_id must be a nonnegative integer')
    reviewed_video_sha256 = config.get('reviewed_video_sha256')
    if (reviewed_video_sha256 is not None
            and (not isinstance(reviewed_video_sha256, str)
                 or len(reviewed_video_sha256) != 64
                 or any(character not in '0123456789abcdef'
                        for character in reviewed_video_sha256))):
        raise ValueError('reviewed_video_sha256 must be a lowercase SHA-256 hex digest')
    video, output = Path(video), Path(output)
    if video.resolve() == output.resolve():
        raise ValueError('Output must not overwrite the source video')
    digest = file_sha256(video)
    if reviewed_video_sha256 is not None and digest != reviewed_video_sha256:
        raise ValueError('Source does not match the reviewed video SHA-256')
    analyzer = VideoAnalyzer(video, {**config, 'include_frame': False})
    try:
        analyzer.preview()
        initial_selection = analyzer.select_target(*click)
        if reviewed_target_id is not None:
            if (not isinstance(initial_selection, dict)
                    or initial_selection.get('identity') != 'confirmed'
                    or initial_selection.get('target_id') != reviewed_target_id):
                raise ValueError('Initial selection does not match the reviewed target track ID')
        stride = max(1, round(analyzer.fps / sample_hz))
        indices = list(range(0, analyzer.frame_count, stride))
        if indices[-1] != analyzer.frame_count - 1:
            indices.append(analyzer.frame_count - 1)
        if reselections and reselections[-1]['time'] > indices[-1] / analyzer.fps:
            raise ValueError('Reselection time exceeds the final video frame')
        observations, event_index, applied_reselections = [], 0, []
        trailing_decode = None
        for sample_position, index in enumerate(indices):
            time = index / analyzer.fps
            try:
                observation = analyzer.analyze(time)
            except EOFError:
                last_decoded = getattr(analyzer, 'frame_index', None)
                gap = index - last_decoded if type(last_decoded) is int else None
                terminal_sample = sample_position == len(indices) - 1
                if (not terminal_sample or not isinstance(gap, int)
                        or not 1 <= gap <= MAX_TRAILING_DECODE_GAP_FRAMES or not observations):
                    raise
                trailing_decode = {'decoded_frame_count': last_decoded + 1,
                                   'trailing_unreadable_frames': gap}
                break
            while event_index < len(reselections) and reselections[event_index]['time'] <= time:
                event = reselections[event_index]
                analyzer.select_target(*event['click'])  # CURRENT frame, never a stale first-frame box.
                observation = analyzer.analyze(time)  # Explicit selection resets feature history.
                applied_reselections.append({'requested_time': event['time'], 'applied_time': time,
                                             'click': event['click']})
                event_index += 1
            observations.append(observation)
        if event_index != len(reselections):
            raise ValueError('A target reselection falls inside unreadable trailing video frames')
        # Hash again so source changes during a long run cannot be mislabeled.
        if file_sha256(video) != digest:
            raise ValueError('Source changed during export; no output was written')
        source = {'name': video.name, 'sha256': digest, 'duration': analyzer.duration,
                  'fps': analyzer.fps, 'frame_count': analyzer.frame_count,
                  'analysis_mode': 'precomputed'}
        if trailing_decode is not None:
            source.update(trailing_decode)
        result = {'schema_version': 1, 'mode': 'precomputed', 'source': source,
                  'provenance': {'source_name': video.name, 'sha256': digest,
                                 'video_duration': analyzer.duration, 'analysis_mode': 'precomputed',
                                 'adapter': 'aba_demo.vision.VideoAnalyzer',
                                 'weights': config.get('weights', 'yolo11n-pose.pt'),
                                 'tracker': Path(resolve_tracker_profile(config)).name,
                                 'config': config,
                                 'applied_target_reselections': applied_reselections,
                                 'decode_completion': ('complete' if trailing_decode is None
                                                       else 'trailing_eof_within_tolerance'),
                                 'timestamp_basis': 'nominal_fps_cfr_only',
                                 'camera_stability': 'unchecked_fixed_camera_required',
                                 'clinical_validation': False},
                  'observations': observations}
    finally:
        analyzer.close()
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output.parent,
                                         suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
        os.replace(temporary, output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video', required=True, help='Approved local fixed-camera CFR video')
    parser.add_argument('--config', required=True, help='JSON with target_click and calibrated normalized ROIs')
    parser.add_argument('--output', required=True, help='Precomputed observations JSON (contains no video frames)')
    args = parser.parse_args(argv)
    try:
        if Path(args.config).resolve() == Path(args.output).resolve():
            raise ValueError('Output must not overwrite the configuration')
        config = json.loads(Path(args.config).read_text(encoding='utf-8'))
        if not isinstance(config, dict):
            raise ValueError('Config must be a JSON object')
        data = export_video(args.video, config, args.output)
    except (ValueError, OSError, RuntimeError, ImportError, EOFError) as exc:
        parser.exit(1, f'Export failed: {exc}\nNo successful inference output was generated by this run.\n')
    print(f"Exported {len(data['observations'])} precomputed observations to {args.output}")
    print('Experimental proxies only; fixed-camera assumption unchecked; no clinical validation.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
