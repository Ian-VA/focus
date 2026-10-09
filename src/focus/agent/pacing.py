import io
import time

from PIL import Image, ImageChops, ImageStat

from focus.tools import Screenshot

THUMBNAIL_SIZE = (96, 54)


class ScreenWatcher:
    """decides whether the screen changed enough since the last model check-in to need another"""

    def __init__(self, threshold: float = 0.02, force_after: float = 60.0):
        self.threshold = threshold
        self.force_after = force_after
        self._last: tuple[list[str], list[Image.Image]] | None = None
        self._last_checked_at = 0.0

    def needs_check(self, shots: list[Screenshot]) -> bool:
        frame = _fingerprint(shots)
        stale = time.time() - self._last_checked_at >= self.force_after
        if self._last is None or stale or _difference(self._last, frame) > self.threshold:
            self._last = frame
            self._last_checked_at = time.time()
            return True
        return False

    def reset(self) -> None:
        self._last = None


class AdaptiveInterval:
    """backs off while the user stays on task, snaps back to the base interval otherwise"""

    def __init__(self, base: float, maximum: float, patience: int = 3):
        self.base = base
        self.maximum = max(base, maximum)
        self.patience = patience
        self.current = base
        self._calm_streak = 0

    def calm(self) -> None:
        self._calm_streak += 1
        if self._calm_streak >= self.patience:
            self.current = min(self.current * 2, self.maximum)
            self._calm_streak = 0

    def alert(self) -> None:
        self.current = self.base
        self._calm_streak = 0


def _fingerprint(shots: list[Screenshot]) -> tuple[list[str], list[Image.Image]]:
    thumbnails = []
    for shot in shots:
        image = Image.open(io.BytesIO(shot.data))
        image.draft("L", THUMBNAIL_SIZE)
        thumbnails.append(image.convert("L").resize(THUMBNAIL_SIZE))
    return [shot.label for shot in shots], thumbnails


def _difference(a, b) -> float:
    (labels_a, thumbs_a), (labels_b, thumbs_b) = a, b
    if labels_a != labels_b:
        return 1.0
    return max(
        (ImageStat.Stat(ImageChops.difference(x, y)).mean[0] / 255 for x, y in zip(thumbs_a, thumbs_b)),
        default=1.0,
    )
