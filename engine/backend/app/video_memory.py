"""Optional bounded SAM2 streaming memory. No chemical/source identification."""
import math
import time
import numpy as np
from .detector import SAM_ID, SAM_REV, file_sha256

MODEL_REVISIONS = {
    SAM_ID: SAM_REV,
    'facebook/sam2.1-hiera-large': '665f8e2ad61cf5f53d65644ff27c8ee525124610',
}


class StreamBounds:
    def __init__(self, max_frames=120, max_gap_seconds=.25, max_duration_seconds=8):
        if not 1 <= max_frames <= 120 or not 0 < max_gap_seconds <= 1 or not 0 < max_duration_seconds <= 8:
            raise ValueError('Invalid streaming bounds')
        self.max_frames, self.max_gap, self.max_duration = max_frames, max_gap_seconds, max_duration_seconds
        self.first = self.last = self.shape = None
        self.count = 0

    def accept(self, timestamp, shape):
        if timestamp is not None and (not math.isfinite(timestamp) or timestamp < 0):
            raise ValueError('Finite nonnegative source PTS required')
        if self.count >= self.max_frames:
            raise ValueError('Frame budget exhausted; reset the session')
        if self.count:
            if (timestamp is None) != (self.last is None):
                raise ValueError('Cannot mix known and unknown source timing')
            if timestamp is not None and (timestamp <= self.last or timestamp - self.last > self.max_gap):
                raise ValueError('Discontinuous source PTS; reset the session')
            if (timestamp is not None and timestamp - self.first > self.max_duration) or shape != self.shape:
                raise ValueError('Session duration/geometry changed; reset the session')
        else:
            self.first, self.shape = timestamp, shape
        self.last = timestamp
        self.count += 1


class VideoMemory:
    def __init__(self, device='auto', capture_logits=False, model_id=SAM_ID, revision=None):
        if model_id not in MODEL_REVISIONS or revision not in (None, MODEL_REVISIONS[model_id]):
            raise ValueError('Use an explicitly pinned supported SAM2 model')
        self.model_id = model_id
        self.model_revision = MODEL_REVISIONS[model_id]
        import torch
        from transformers import Sam2VideoModel, Sam2VideoProcessor
        self.torch = torch
        self.capture_logits = capture_logits
        self.device = ('mps' if torch.backends.mps.is_available() else 'cuda' if torch.cuda.is_available() else 'cpu') if device == 'auto' else device
        if self.device not in ('cpu', 'mps', 'cuda'):
            raise ValueError('Unsupported device')
        torch.set_num_threads(4)
        started = time.perf_counter()
        self.processor = Sam2VideoProcessor.from_pretrained(self.model_id, revision=self.model_revision, local_files_only=True)
        self.model, info = Sam2VideoModel.from_pretrained(
            self.model_id, revision=self.model_revision, local_files_only=True, use_safetensors=True,
            output_loading_info=True)
        if info.get('missing_keys') or info.get('unexpected_keys') or info.get('mismatched_keys'):
            raise ValueError('Video weights did not load exactly')
        self.model = self.model.to(self.device).eval()
        self.load_seconds = time.perf_counter() - started
        self.reset()

    def reset(self):
        self.session = self.processor.init_video_session(
            inference_device=self.device, dtype=self.torch.float32,
            max_vision_features_cache_size=1)
        self.bounds = StreamBounds()
        self.seeded = False
        self.seed_pts = None
        self.last_mask_logits = None

    def sync(self):
        if self.device == 'mps': self.torch.mps.synchronize()
        elif self.device == 'cuda': self.torch.cuda.synchronize()

    def step(self, rgb, source_pts, seed_mask=None):
        """Prompt only with a current/past model mask; never feed reference truth.

        Call reset at known shot boundaries, even when dimensions do not change.
        Bounds reject gaps/seeks and limit accumulated inference state.
        """
        if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
            raise ValueError('Expected uint8 RGB frame')
        if seed_mask is not None:
            if self.seeded or seed_mask.dtype != np.bool_ or seed_mask.shape != rgb.shape[:2] or seed_mask.sum() < 4:
                raise ValueError('A nonempty initial boolean model seed is required')
        self.bounds.accept(source_pts, rgb.shape)
        if not self.seeded and seed_mask is None:
            self.last_mask_logits = None
            return None, dict(status='unseeded', latency_ms=None, seed_pts_seconds=None)
        self.sync(); started = time.perf_counter()
        inputs = self.processor(images=rgb, device='cpu', return_tensors='pt').to(self.device)
        if seed_mask is not None:
            self.session.video_height, self.session.video_width = rgb.shape[:2]
            self.processor.process_new_mask_for_video_frame(
                self.session, frame_idx=0, obj_ids=[1], input_masks=[seed_mask])
            self.seeded, self.seed_pts = True, source_pts
        with self.torch.inference_mode():
            outputs = self.model(inference_session=self.session, frame=inputs.pixel_values[0])
            masks = self.processor.post_process_masks(
                [outputs.pred_masks], original_sizes=inputs.original_sizes, binarize=True)[0]
            if getattr(self, 'capture_logits', False):
                logits = self.processor.post_process_masks(
                    [outputs.pred_masks], original_sizes=inputs.original_sizes, binarize=False)[0]
                self.last_mask_logits = logits.float().detach().cpu().numpy().reshape(-1, *rgb.shape[:2]).max(axis=0)
        mask = masks.detach().cpu().numpy().reshape(-1, *rgb.shape[:2]).any(axis=0)
        self.sync()
        return mask, dict(status='seed_conditioned' if seed_mask is not None else 'memory_propagated',
                         latency_ms=(time.perf_counter() - started) * 1000,
                         seed_pts_seconds=self.seed_pts, mask_pixels=int(mask.sum()))

    def metadata(self):
        from huggingface_hub import hf_hub_download
        return dict(method='sam2-video-memory-experiment-v1', id=self.model_id, revision=self.model_revision,
                    weights_sha256=file_sha256(hf_hub_download(self.model_id, 'model.safetensors', revision=self.model_revision, local_files_only=True)),
                    implementation_sha256=file_sha256(__file__), device=self.device,
                    load_seconds=self.load_seconds, training_performed=False,
                    seed_source='existing model prediction; never ground truth',
                    api_key_used=False, real_time_verified=False,
                    limitations=['Visual propagation does not establish methane identity or source',
                                 'Incorrect seeds can propagate; reset required at shot boundaries',
                                 'Maximum 120 frames / 8 source seconds per session'])
