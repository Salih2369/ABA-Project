"""Frame sources for the live-session shell. No camera support exists here."""
from dataclasses import dataclass
import math
from pathlib import Path

# Mirrors the export's tolerance for a few undecodable frames at the very end of a file.
MAX_TRAILING_UNREADABLE_FRAMES = 3


class SourceEnded(Exception):
    """A finite source reached its natural end (not a failure)."""


class SourceDisconnected(Exception):
    """A source stopped unexpectedly; results after this point are unknown."""


@dataclass(frozen=True)
class SourceFrame:
    index: int
    video_time: float
    image: object = None


class SyntheticFrameSource:
    """Pixel-free frames at a constant rate. SYNTHETIC: never a camera or recording.

    ``disconnect_at`` injects an unexpected disconnect before that frame index.
    """
    kind = "synthetic"

    def __init__(self, fps: float, frame_count: int, disconnect_at: int | None = None):
        if type(fps) not in (int, float) or not math.isfinite(fps) or fps <= 0:
            raise ValueError("fps must be a finite positive number")
        if type(frame_count) is not int or frame_count <= 0:
            raise ValueError("frame_count must be a positive integer")
        if disconnect_at is not None and (type(disconnect_at) is not int or disconnect_at < 0):
            raise ValueError("disconnect_at must be a nonnegative integer or None")
        self.fps = float(fps)
        self.frame_count = frame_count
        self.disconnect_at = disconnect_at
        self._next = 0
        self.opened = False
        self.closed = False

    def describe(self) -> dict:
        return {"kind": self.kind, "fps": self.fps, "frame_count": self.frame_count}

    def open(self) -> None:
        if self.closed:
            raise RuntimeError("Source is closed")
        self.opened = True

    def read(self) -> SourceFrame:
        if not self.opened or self.closed:
            raise RuntimeError("Source is not open")
        if self.disconnect_at is not None and self._next == self.disconnect_at:
            raise SourceDisconnected("Synthetic disconnect injected")
        if self._next >= self.frame_count:
            raise SourceEnded("End of synthetic source")
        frame = SourceFrame(self._next, self._next / self.fps)
        self._next += 1
        return frame

    def close(self) -> None:
        self.closed = True


class FileAsLiveSource:
    """Decode a local video file frame by frame for real-time pacing by the session.

    Time uses nominal FPS (constant-frame-rate files only), matching the current
    analyzer. This replays an existing file: it is not a camera and it does not
    create a new recording. Requires OpenCV (``cv2``), imported lazily.
    """
    kind = "file"

    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_file():
            raise ValueError("Video must be an existing local file, not a URL or camera")
        self.fps = None
        self.frame_count = None
        self._capture = None
        self._next = 0
        self.opened = False
        self.closed = False

    def describe(self) -> dict:
        return {"kind": self.kind, "name": self.path.name, "fps": self.fps,
                "frame_count": self.frame_count, "timestamp_basis": "nominal_fps_cfr_only"}

    def open(self) -> None:
        if self.closed:
            raise RuntimeError("Source is closed")
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("File-as-live needs OpenCV: pip install opencv-python-headless") from exc
        capture = cv2.VideoCapture(str(self.path))
        try:
            if not capture.isOpened():
                raise OSError("OpenCV could not open the video")
            fps = float(capture.get(cv2.CAP_PROP_FPS))
            frames = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            if not math.isfinite(fps) or fps <= 0 or not math.isfinite(frames) or frames <= 0:
                raise OSError("Video requires finite positive FPS and frame count metadata")
        except Exception:
            capture.release()
            raise
        self._capture = capture
        self.fps = fps
        self.frame_count = int(frames)
        self.opened = True

    def read(self) -> SourceFrame:
        if not self.opened or self.closed:
            raise RuntimeError("Source is not open")
        if self._next >= self.frame_count:
            raise SourceEnded("End of video file")
        ok, image = self._capture.read()
        if not ok:
            if self.frame_count - self._next <= MAX_TRAILING_UNREADABLE_FRAMES:
                raise SourceEnded("End of video file (trailing frames unreadable)")
            raise SourceDisconnected(f"Frame {self._next} could not be decoded")
        frame = SourceFrame(self._next, self._next / self.fps, image)
        self._next += 1
        return frame

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        self.closed = True
