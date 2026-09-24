"""Optional local YOLO-pose adapter. Experimental proxies, not clinical labels.

Pure feature logic uses only the standard library. Heavy dependencies are lazy.
Coordinates for keypoints are pixels; ROIs/clicks/returned boxes are normalized.
"""
import math
from pathlib import Path

SIGNALS = ('orientation', 'body_motion', 'out_of_seat', 'hand_motion', 'posture_change')
TRACKER_PROFILES = {
    'bytetrack': 'bytetrack.yaml',
    'botsort': 'botsort.yaml',
    'botsort_reid': str(Path(__file__).with_name('botsort_reid.yaml')),
}


def resolve_tracker_profile(config):
    """Resolve only reviewed tracker profiles; never accept arbitrary paths."""
    profile = config.get('tracker_profile', 'bytetrack')
    if profile not in TRACKER_PROFILES:
        raise ValueError('tracker_profile must be bytetrack, botsort, or botsort_reid')
    return TRACKER_PROFILES[profile]


def validate_config(config):
    """Validate geometry before loading a model or opening a recording."""
    result = dict(config)
    targets = result.get('task_targets', [])
    if not isinstance(targets, (list, tuple)):
        raise ValueError('task_targets must be a list of normalized rectangles')
    rectangles = list(targets)
    if result.get('seat_roi') is not None:
        rectangles.append(result['seat_roi'])
    for rectangle in rectangles:
        if (not isinstance(rectangle, (list, tuple)) or len(rectangle) != 4
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                           and math.isfinite(v) and 0 <= v <= 1 for v in rectangle)
                or rectangle[0] >= rectangle[2] or rectangle[1] >= rectangle[3]):
            raise ValueError('ROIs must be finite normalized [x1, y1, x2, y2] rectangles')
    resolve_tracker_profile(result)
    return result


class PoseFeatures:
    def __init__(self, config):
        self.config = validate_config(config)
        self.previous = None
        self.last_time = None
        self.posture_candidate = None
        self.posture_since = None
        self.stable_posture = None
        self.stable_posture_time = None
        self.seated_baseline = False
        self.posture_transition_count = 0

    def observe(self, points, time, dimensions):
        if not math.isfinite(time) or time < 0 or (self.last_time is not None and time <= self.last_time):
            raise ValueError('Feature timestamps must be finite and strictly increasing')
        if self.last_time is not None and time - self.last_time > self.config.get('max_motion_gap_s', 1.0):
            self.posture_candidate = self.posture_since = self.stable_posture = None
            self.seated_baseline = False
        self.last_time = time
        signals, values = dict.fromkeys(SIGNALS), {}
        def point(index):
            if points is None or len(points) != 17:
                return None
            p = points[index]
            if len(p) < 3 or not all(math.isfinite(v) for v in p[:3]):
                return None
            return p[:2] if p[2] >= self.config.get('keypoint_confidence', .5) else None
        joints = [point(i) for i in range(17)]
        # Image-plane head-turn proxy only: never shoulder-derived gaze.
        nose, left_ear, right_ear = (joints[i] for i in (0, 3, 4))
        if all(p is not None for p in (nose, left_ear, right_ear)):
            ears = sorted((left_ear, right_ear))
            span = ears[1][0] - ears[0][0]
            mid = midpoint(*ears)
            if (span >= self.config.get('min_face_pixels', 12)
                    and abs(ears[1][1] - ears[0][1]) <= span * .35
                    and abs(nose[1] - mid[1]) <= span * .6):
                offset = (nose[0] - mid[0]) / span
                values['head_turn_offset'] = offset
                values['orientation_basis'] = 'nose_ear_image_plane_proxy_not_gaze'
                if self.config.get('head_turn_min_offset', .15) <= abs(offset) <= .65:
                    nx, ny = nose[0] / dimensions[0], nose[1] / dimensions[1]
                    sides = []
                    for x1, y1, x2, y2 in self.config.get('task_targets', []):
                        if not y1 <= ny <= y2 or x1 <= nx <= x2:
                            sides = []  # Depth/vertical target geometry is unsupported.
                            break
                        sides.append(1 if x1 > nx else -1)
                    if sides:
                        signals['orientation'] = not any(offset * side > 0 for side in sides)
        torso = [joints[i] for i in (5, 6, 11, 12)]
        current = None
        if all(p is not None for p in torso):
            shoulder = midpoint(torso[0], torso[1])
            hip = midpoint(torso[2], torso[3])
            scale = math.dist(shoulder, hip)
            if scale >= self.config.get('min_torso_pixels', 10):
                center = midpoint(shoulder, hip)
                hands = [None if joints[i] is None else
                         ((joints[i][0] - center[0]) / scale, (joints[i][1] - center[1]) / scale)
                         for i in (9, 10)]
                body = [None if joints[i] is None else tuple(joints[i]) for i in (5, 6, 7, 8, 11, 12, 13, 14)]
                current = (time, center, scale, hands, body)
                if self.previous is not None:
                    pt, pc, ps, ph, pb = self.previous
                    dt = time - pt
                    if dt <= self.config.get('max_motion_gap_s', 1.0):
                        velocities = [math.dist(p, old) / ((scale + ps) / 2) / dt
                                      for p, old in zip(body, pb) if p is not None and old is not None]
                        if len(velocities) >= self.config.get('min_body_joints', 6):
                            speed = math.sqrt(sum(v * v for v in velocities) / len(velocities))
                            values['body_speed'] = speed
                            values['body_visible_joints'] = len(velocities)
                            signals['body_motion'] = speed > self.config.get('body_speed_threshold', .35)
                        if all(h is not None for h in hands + ph):
                            speed = max(math.dist(h, old) / dt for h, old in zip(hands, ph))
                            values['hand_speed'] = speed
                            signals['hand_motion'] = speed > self.config.get('hand_speed_threshold', .8)
        posture = None
        legs_visible = current is not None and all(joints[i] is not None for i in range(11, 17))
        if legs_visible:
            leg_states = []
            for h, k, a in ((11, 13, 15), (12, 14, 16)):
                hp, kp, ap = joints[h], joints[k], joints[a]
                thigh, shin = math.dist(hp, kp), math.dist(kp, ap)
                if min(thigh, shin) < scale * .2:
                    leg_states.append(None)
                    continue
                cosine = sum((hp[d] - kp[d]) * (ap[d] - kp[d]) for d in (0, 1)) / (thigh * shin)
                angle = math.degrees(math.acos(max(-1, min(1, cosine))))
                if (angle >= self.config.get('standing_knee_degrees', 155)
                        and kp[1] - hp[1] > thigh * .6 and ap[1] - kp[1] > shin * .6):
                    leg_states.append('standing')
                elif (self.config.get('sitting_knee_min_degrees', 60) <= angle
                      <= self.config.get('sitting_knee_max_degrees', 120)
                      and abs(kp[1] - hp[1]) < thigh * .5
                      and ap[1] - kp[1] > shin * .6):
                    leg_states.append('sitting')
                else:
                    leg_states.append(None)
            if leg_states[0] == leg_states[1]:
                posture = leg_states[0]
        if (not legs_visible or (self.stable_posture_time is not None
                and time - self.stable_posture_time > self.config.get('posture_transition_max_s', 2.0))):
            self.stable_posture = None
            self.seated_baseline = False
        if posture is None:
            self.posture_candidate = self.posture_since = None
        else:
            if posture != self.posture_candidate:
                self.posture_candidate, self.posture_since = posture, time
            if time - self.posture_since >= self.config.get('posture_stability_s', .5):
                signals['posture_change'] = (self.stable_posture is not None and self.stable_posture != posture)
                if signals['posture_change']:
                    self.posture_transition_count += 1
                values['posture_transition_count'] = self.posture_transition_count
                self.stable_posture = posture
                self.stable_posture_time = time
                values['posture'] = posture
                roi = self.config.get('seat_roi')
                if roi is not None:
                    inside = contains(roi, hip[0] / dimensions[0], hip[1] / dimensions[1])
                    values['hip_in_seat_roi'] = inside
                    if posture == 'sitting' and inside:
                        self.seated_baseline = True
                        signals['out_of_seat'] = False
                    elif posture == 'standing' and self.seated_baseline:
                        signals['out_of_seat'] = True
        self.previous = current
        for indicator, detail in (('body_motion', 'body_speed'), ('hand_motion', 'hand_speed'),
                                  ('orientation', 'head_turn_offset'), ('out_of_seat', 'posture'),
                                  ('posture_change', 'posture_transition_count')):
            values[indicator] = values.get(detail)
        return {'signals': signals, 'values': values}


def contains(box, x, y):
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


class TargetLock:
    """Fail-closed identity gate; tracker IDs are not person identities.

    A brief detector dropout may recover only the exact same tracker ID within
    the configured bounded window and with a spatially continuous return.
    Missing frames remain uncertain. Overlap, a different/reassigned ID, a
    discontinuous return, or a longer loss still requires explicit human
    reselection. The default remains 0.3 seconds; reviewed demo configurations
    may extend it to at most 1.0 second for short occlusions.
    """
    def __init__(self, config):
        self.config = validate_config(config)
        recovery_gap = self.config.get('identity_recovery_max_gap_s', .3)
        if (type(recovery_gap) not in (int, float) or not math.isfinite(recovery_gap)
                or not 0 <= recovery_gap <= 1.0):
            raise ValueError('identity_recovery_max_gap_s must be finite and in [0, 1.0]')
        self.recovery_gap = float(recovery_gap)
        self.identity = 'unselected'
        self.target_id = None
        self.last_box = None
        self.last_seen_time = None
        self.recovery_pending = False
        self.reason = 'select_target'

    def select(self, x, y, boxes):
        if not all(math.isfinite(v) and 0 <= v <= 1 for v in (x, y)):
            raise ValueError('Target click must be normalized to [0, 1]')
        candidates = [b for b in boxes if b['id'] is not None and contains(b['xyxy'], x, y)]
        if len(candidates) != 1:
            raise ValueError('Click exactly one tracked person; overlap or no detection is ambiguous')
        candidate = candidates[0]
        if self._overlaps(candidate, boxes):
            raise ValueError('Target overlaps another person; wait and reselect')
        self.target_id = candidate['id']
        self.last_box = list(candidate['xyxy'])
        self.last_seen_time = None
        self.recovery_pending = False
        self.identity, self.reason = 'confirmed', None

    def _overlaps(self, candidate, boxes):
        a = candidate['xyxy']
        for other in boxes:
            if other is candidate:
                continue
            b = other['xyxy']
            intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
            area = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
            if area > 0 and intersection / area > self.config.get('identity_overlap_threshold', .2):
                return True
        return False

    def _is_continuous(self, candidate):
        old, new = self.last_box, candidate['xyxy']
        if old is None:
            return False
        scale = math.dist(old[:2], old[2:])
        displacement = math.dist(midpoint(old[:2], old[2:]), midpoint(new[:2], new[2:]))
        return displacement <= scale * self.config.get('identity_max_jump_ratio', .75)

    def _latch(self, reason):
        self.identity, self.reason = 'uncertain', reason
        self.recovery_pending = False
        return None

    def update(self, boxes, time=None):
        if time is not None and (type(time) not in (int, float)
                                 or not math.isfinite(time) or time < 0):
            raise ValueError('Identity timestamps must be finite and nonnegative')
        if self.identity != 'confirmed' and not self.recovery_pending:
            return None
        candidates = [b for b in boxes if b['id'] == self.target_id]
        if self.recovery_pending:
            if (time is None or self.last_seen_time is None or time < self.last_seen_time
                    or time - self.last_seen_time > self.recovery_gap + 1e-9):
                return self._latch('target_lost_reselect')
            if len(candidates) != 1:
                self.reason = 'target_temporarily_missing'
                return None
            candidate = candidates[0]
            if self._overlaps(candidate, boxes):
                return self._latch('crossing_overlap_reselect')
            if not self._is_continuous(candidate):
                return self._latch('discontinuous_track_reselect')
            self.identity, self.reason = 'confirmed', None
            self.recovery_pending = False
            self.last_box = list(candidate['xyxy'])
            self.last_seen_time = time
            return candidate

        if len(candidates) != 1:
            if (time is not None and self.last_seen_time is not None
                    and self.last_seen_time <= time
                    and time - self.last_seen_time <= self.recovery_gap + 1e-9):
                self.identity = 'uncertain'
                self.reason = 'target_temporarily_missing'
                self.recovery_pending = True
                return None
            return self._latch('target_lost_reselect')
        candidate = candidates[0]
        if self._overlaps(candidate, boxes):
            return self._latch('crossing_overlap_reselect')
        if not self._is_continuous(candidate):
            return self._latch('discontinuous_track_reselect')
        self.last_box = list(candidate['xyxy'])
        if time is not None:
            if self.last_seen_time is not None and time < self.last_seen_time:
                raise ValueError('Identity timestamps must be forward-only')
            self.last_seen_time = time
        self.reason = None
        return candidate


def midpoint(a, b):
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)


class VideoAnalyzer:
    """Causal local-file replay with real Ultralytics YOLO11 pose/ByteTrack.

    ``preview`` tracks the first/current frame, ``select_target`` confirms one
    displayed box, and ``analyze`` advances forward only. Every intervening frame
    is tracked; no seeking or automatic rebinding. Time uses nominal FPS (CFR
    recordings only). Camera stability is UNCHECKED: use a fixed camera and
    reopen/recalibrate after any camera movement. No compensation is claimed.
    """
    def __init__(self, path, config: dict):
        from pathlib import Path
        self.config = validate_config(config)
        self.path = Path(path)
        if not self.path.is_file():
            raise ValueError('Video must be an existing local file, not a URL or camera')
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError('Optional video dependencies required: install requirements-demo-vision.txt') from exc
        self.cv2 = cv2
        self.capture = cv2.VideoCapture(str(self.path))
        self.model = None
        self.closed = False
        try:
            if not self.capture.isOpened():
                raise ValueError('OpenCV could not open the video')
            self.fps = float(self.capture.get(cv2.CAP_PROP_FPS))
            frames = float(self.capture.get(cv2.CAP_PROP_FRAME_COUNT))
            if not math.isfinite(self.fps) or self.fps <= 0 or not math.isfinite(frames) or frames <= 0:
                raise ValueError('Video requires finite positive FPS and frame count metadata')
            self.frame_count = int(frames)
            self.duration = self.frame_count / self.fps
        except Exception:
            self.close()
            raise
        self.features = PoseFeatures(self.config)
        self.lock = TargetLock(self.config)
        self.frame_index = -1
        self.frame = None
        self.boxes = []
        self.poses = []
        self.last_request = None
        self.last_result = None
        self.width = self.height = 0

    def _read_frame(self):
        if self.closed:
            raise RuntimeError('Analyzer is closed')
        ok, frame = self.capture.read()
        if not ok:
            raise EOFError('End of video or unreadable frame')
        self.frame_index += 1
        self.frame = frame
        self.height, self.width = frame.shape[:2]
        if self.model is None:
            try:
                from ultralytics import YOLO
            except ImportError as exc:
                raise RuntimeError('Optional video dependencies required: install requirements-demo-vision.txt') from exc
            self.model = YOLO(self.config.get('weights', 'yolo11n-pose.pt'))
        options = dict(persist=True, tracker=resolve_tracker_profile(self.config), verbose=False,
                       conf=self.config.get('detection_confidence', .25),
                       imgsz=self.config.get('imgsz', 640))
        if self.config.get('device') is not None:
            options['device'] = self.config['device']
        results = self.model.track(frame, **options)
        self.boxes, self.poses = [], []
        if results:
            result = results[0]
            if result.boxes is not None:
                rectangles = result.boxes.xyxy.cpu().tolist()
                ids = result.boxes.id.cpu().tolist() if result.boxes.id is not None else [None] * len(rectangles)
                pose = result.keypoints.data.cpu().tolist() if result.keypoints is not None else []
                for i, (rect, track_id) in enumerate(zip(rectangles, ids)):
                    normalized = [max(0.0, min(1.0, float(v) / d))
                                  for v, d in zip(rect, (self.width, self.height, self.width, self.height))]
                    if normalized[0] >= normalized[2] or normalized[1] >= normalized[3]:
                        continue
                    self.boxes.append({'id': None if track_id is None else int(track_id), 'xyxy': normalized})
                    self.poses.append(pose[i] if i < len(pose) else None)
        self.last_result = None

    def _jpeg(self):
        import base64
        ok, encoded = self.cv2.imencode('.jpg', self.frame)
        if not ok:
            raise RuntimeError('Could not encode preview JPEG')
        return base64.b64encode(encoded.tobytes()).decode('ascii')

    def preview(self) -> dict:
        if self.closed:
            raise RuntimeError('Analyzer is closed')
        if self.frame is None:
            self._read_frame()
        image = self._jpeg()
        return {'image': image, 'frame': image, 'width': self.width, 'height': self.height,
                'duration': self.duration, 'fps': self.fps, 'time': self.frame_index / self.fps,
                'boxes': [dict(b) for b in self.boxes]}

    def select_target(self, x: float, y: float):
        if self.closed:
            raise RuntimeError('Analyzer is closed')
        if self.frame is None:
            self.preview()
        self.lock.select(x, y, self.boxes)
        self.features = PoseFeatures(self.config)
        self.last_result = None
        return {'identity': self.lock.identity, 'target_id': self.lock.target_id}

    def _observe_frame(self):
        if self.last_result is not None:
            return self.last_result
        time = self.frame_index / self.fps
        target = self.lock.update(self.boxes, time=time)
        points = self.poses[self.boxes.index(target)] if target is not None else None
        features = self.features.observe(points, time, (self.width, self.height))
        features['values'].update(camera_stability='unchecked_fixed_camera_required',
                                  timestamp_basis='nominal_fps_cfr_only',
                                  identity_reason=self.lock.reason,
                                  provisional_thresholds=True)
        self.last_result = dict(time=time, identity='confirmed' if target is not None else 'uncertain', **features,
                                boxes=[dict(b) for b in self.boxes],
                                target_box=None if target is None else target['xyxy'])
        return self.last_result

    def analyze(self, time: float) -> dict:
        import copy
        if self.closed:
            raise RuntimeError('Analyzer is closed')
        if not math.isfinite(time) or time < 0 or (self.last_request is not None and time < self.last_request):
            raise ValueError('Analysis times must be finite, nonnegative, and forward-only')
        desired = int(math.floor(time * self.fps + 1e-8))
        if desired >= self.frame_count:
            raise EOFError('Requested time is outside the video')
        self.last_request = time
        if self.frame is None:
            self._read_frame()
        transition = False
        result = self._observe_frame()
        while self.frame_index < desired:
            self._read_frame()
            result = self._observe_frame()
            transition = transition or result['signals']['posture_change'] is True
        result = copy.deepcopy(result)
        if transition and result['identity'] == 'confirmed':
            result['signals']['posture_change'] = True
        if self.config.get('include_frame', False):
            result['frame'] = self._jpeg()
        return result

    def close(self):
        if getattr(self, 'capture', None) is not None:
            self.capture.release()
        self.model = None
        self.closed = True
