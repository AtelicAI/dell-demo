"""Bounded tracking policy for long offline replays; no model dependencies."""
import math


class RefreshPolicy:
    def __init__(self, session_frames=90, session_seconds=6., retry_seconds=1.):
        if not 1 <= session_frames <= 120 or not 0 < session_seconds <= 8 or not 0 < retry_seconds <= 6:
            raise ValueError('Invalid long-video session bounds')
        self.limit, self.seconds, self.retry = session_frames, session_seconds, retry_seconds
        self.reset()

    def reset(self):
        self.start = self.last = self.next_attempt = None
        self.count = 0

    def discontinuity(self, pts):
        if not math.isfinite(pts) or pts < 0:
            raise ValueError('Finite source PTS required')
        return self.last is not None and (pts <= self.last or pts-self.last > .25)

    def expired(self, pts):
        return self.count >= self.limit or (self.start is not None and pts-self.start >= self.seconds)

    def should_detect(self, pts, seeded):
        return not seeded and (self.next_attempt is None or pts >= self.next_attempt)

    def attempted(self, pts):
        self.next_attempt = pts+self.retry

    def accepted(self, pts):
        if self.start is None:
            self.start = pts
        self.last = pts
        self.count += 1
