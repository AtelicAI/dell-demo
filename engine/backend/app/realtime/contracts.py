"""Source-coordinate contracts shared by experimental models and stream workers."""
from dataclasses import dataclass, field
import math
import time
import numpy as np


@dataclass
class Frame:
    frame_id: int
    source_pts: float
    rgb: np.ndarray
    captured_at: float = field(default_factory=time.perf_counter)
    epoch: int = 0
    clock_kind: str = 'source_pts'
    decode_ms: float = 0.

    def __post_init__(self):
        if (type(self.frame_id) is not int or self.frame_id < 0 or not math.isfinite(self.source_pts)
                or self.source_pts < 0 or not math.isfinite(self.captured_at) or type(self.epoch) is not int or self.epoch<0
                or not math.isfinite(self.decode_ms) or self.decode_ms<0
                or self.rgb.dtype != np.uint8 or self.rgb.ndim != 3 or self.rgb.shape[2] != 3 or min(self.rgb.shape[:2])<1):
            raise ValueError('Source ID, finite PTS and uint8 RGB pixels required')


@dataclass
class Detection:
    boxes: list = field(default_factory=list)
    scores: list = field(default_factory=list)
    raw_mask: np.ndarray | None = None
    raw_logits: np.ndarray | None = None
    details: dict = field(default_factory=dict)

    def validate(self, shape):
        h,w=shape
        if len(self.boxes)!=len(self.scores):
            raise ValueError('Every detection box needs its original score')
        for box,score in zip(self.boxes,self.scores):
            if (len(box)!=4 or not all(math.isfinite(v) for v in box)
                    or not 0<=box[0]<box[2]<=w or not 0<=box[1]<box[3]<=h
                    or not math.isfinite(score) or not 0<=score<=1):
                raise ValueError('Boxes must be finite source-coordinate xyxy and scores in [0,1]')
        if self.raw_mask is not None and (self.raw_mask.dtype!=np.bool_ or self.raw_mask.shape!=(h,w)):
            raise ValueError('Detector binary mask must match source pixels')
        return self


@dataclass
class Segmentation:
    mask: np.ndarray
    logits: np.ndarray | None = None
    scores: list = field(default_factory=list)
    details: dict = field(default_factory=dict)
    native_logits: np.ndarray | None = None

    def validate(self, shape):
        if self.mask.dtype!=np.bool_ or self.mask.shape!=shape:
            raise ValueError('Segmentation must be native-size binary pixels')
        if self.logits is not None:
            if self.logits.shape[-2:]!=shape or not np.isfinite(self.logits).all():
                raise ValueError('Finite source-resolution logits required')
            union=(self.logits>0).reshape(-1,*shape).any(axis=0)
            if not np.array_equal(union,self.mask):
                raise ValueError('Raw model mask differs from zero-threshold logits')
        return self
