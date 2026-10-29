"""Offline, persistent model adapters for the isolated real-time experiment.

Only RGB pixels and detector boxes enter inference; there is no annotation input.
LeakHunter MUST be loaded in a fresh worker: its audited restricted loader disables
networking and patches torch.load process-wide. Load cached HF models first when
combining them in that worker. No downloads, training, fusion, or mask filtering.

SAM native_logits preserves pred_masks' original decoder shape and values as an
independent float32 copy captured before projection. Source logits are derived;
source binary masks still use their exact zero threshold. No score quantization.
"""
from __future__ import annotations

from contextlib import nullcontext
import importlib.util
import inspect
from pathlib import Path
import sys
import tempfile
import time

import numpy as np

from ..detector import OWL_ID, OWL_REV, SAM_ID, SAM_REV, box_iou, file_sha256
from .contracts import Detection, Segmentation

ROOT = Path(__file__).resolve().parents[3]
WEIGHT_HASHES = {
    SAM_ID: '0a4067b11ce1e23d5229203f11c718a823060d15a4b23fa2372a7d4b77cbbc60',
    OWL_ID: 'e1e130b9e404cf91a75ad45644c1da9d7fa5284085eecc864266a6923efb99e7',
}


def synchronize(device):
    """Finish queued accelerator work; CPU is a no-op. No implicit fallback."""
    name = str(device)
    if name == 'cpu':
        return
    import torch
    if name.startswith('cuda'):
        torch.cuda.synchronize(device)
    elif name == 'mps':
        torch.mps.synchronize()
    else:
        raise ValueError('Unsupported inference device')


def _device(device, torch):
    name = str(device)
    if name not in ('cpu', 'cuda', 'mps'):
        raise ValueError('Use cpu, cuda, or mps explicitly')
    if name == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('CUDA requested but unavailable; no CPU fallback')
    if name == 'mps' and not torch.backends.mps.is_available():
        raise RuntimeError('MPS requested but unavailable; no CPU fallback')
    return name


def _rgb(rgb):
    if (not isinstance(rgb, np.ndarray) or rgb.dtype != np.uint8 or rgb.ndim != 3
            or rgb.shape[2] != 3 or min(rgb.shape[:2]) < 1):
        raise ValueError('Expected nonempty uint8 source RGB pixels')
    return rgb.shape[:2]


def _boxes(boxes, shape, scores=None, limit=4, nms=None):
    """Clip source xyxy, discard invalid boxes and keep original score pairing."""
    h, w = shape
    if scores is None:
        scores = [1.] * len(boxes)
    if len(boxes) != len(scores):
        raise ValueError('Every box requires its original score')
    rows = []
    for box, score in zip(boxes, scores):
        value = np.asarray(box, dtype=float)
        score = float(score)
        if value.shape != (4,) or not np.isfinite(value).all() or not np.isfinite(score):
            raise ValueError('Finite xyxy and scores required')
        if not 0 <= score <= 1:
            raise ValueError('Detection score outside [0,1]')
        x1, y1, x2, y2 = value.tolist()
        clipped = [max(0., min(w, x1)), max(0., min(h, y1)),
                   max(0., min(w, x2)), max(0., min(h, y2))]
        if clipped[0] < clipped[2] and clipped[1] < clipped[3]:
            rows.append((clipped, score))
    rows.sort(key=lambda row: row[1], reverse=True)  # Stable for tied scores.
    kept = []
    for box, score in rows:
        if nms is None or all(box_iou(box, previous) <= nms for previous, _ in kept):
            kept.append((box, score))
            if len(kept) == limit:
                break
    return [b for b, _ in kept], [s for _, s in kept]


def _source_hash(cls):
    return file_sha256(inspect.getfile(cls))


def _metadata(model_id, revision, model, processor, device):
    from huggingface_hub import hf_hub_download
    path = Path(hf_hub_download(model_id, 'model.safetensors', revision=revision,
                              local_files_only=True))
    digest = file_sha256(path)
    if digest != WEIGHT_HASHES[model_id]:
        raise ValueError('Cached weights differ from previously verified pinned checkpoint')
    import torch
    return dict(id=model_id, revision=revision, weights_sha256=digest,
                weights_bytes=path.stat().st_size, device=device,
                device_name=torch.cuda.get_device_name() if device == 'cuda' else device,
                license='Apache-2.0', pretrained=True, training_performed=False,
                adapter_sha256=file_sha256(__file__),
                model_implementation_sha256=_source_hash(type(model)),
                processor_implementation_sha256=_source_hash(type(processor)),
                ground_truth_used=False, downloads_allowed=False,
                score_kind='uncalibrated model support; not methane probability')


def _strict_model(cls, model_id, revision):
    model, info = cls.from_pretrained(model_id, revision=revision, use_safetensors=True,
                                     local_files_only=True, output_loading_info=True)
    if any(info.get(key) for key in ('missing_keys', 'unexpected_keys', 'mismatched_keys', 'error_msgs')):
        raise ValueError('Pinned model weights did not load exactly')
    return model


def _autocast(torch, device):
    return torch.autocast(device_type='cuda', dtype=torch.bfloat16) if device == 'cuda' else nullcontext()


def _array(value):
    return value.detach().float().cpu().numpy().copy()


def _segmentation(logits, shape, scores, details, native_logits=None):
    value = _array(logits)
    if value.shape[-2:] != shape:
        raise ValueError('Native SAM source geometry mismatch; no silent resizing')
    value = value.reshape(-1, *shape)
    mask = (value > 0).any(axis=0)
    return Segmentation(mask=mask, logits=value, scores=scores, details=details,
                        native_logits=native_logits).validate(shape)


def _sibling(name):
    path = ROOT / 'scripts' / 'remote' / (name + '.py')
    spec = importlib.util.spec_from_file_location('atelic_realtime_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LeakHunterDetector:
    """Exact FINALTEST preprocessing/postprocess; full network, never auto-fused."""

    def __init__(self, repo, output_dir, device='cuda'):
        if 'ultralytics' in sys.modules:
            raise RuntimeError('LeakHunter requires a fresh worker before importing ultralytics')
        import torch
        self.device = _device(device, torch)
        finaltest = _sibling('evaluate_leakhunter_mask_candidates')
        self.repo = Path(repo).resolve(strict=True)
        repository = finaltest.baseline.verify_repo(self.repo)
        # The pinned fork strips quote characters from weight-path strings.
        # Verify the real checkout first; alias it without copying/modifying it.
        # Keep the TemporaryDirectory alive as long as this persistent adapter.
        self._safe_reporef = None
        loader_repo = self.repo
        if any(quote in str(self.repo) for quote in ("'", '"')):
            self._safe_reporef = tempfile.TemporaryDirectory(prefix='atelic-leakhunter-', dir='/tmp')
            loader_repo = Path(self._safe_reporef.name) / 'repo'
            loader_repo.symlink_to(self.repo, target_is_directory=True)
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        loader = _sibling('run_leakhunter_benchmark')
        wrapper, metadata = loader.load_demo_model(loader_repo, output)
        self.network = wrapper.model.float().to(self.device).eval()
        for parameter in self.network.parameters():
            parameter.requires_grad_(False)
        self.predict = finaltest.make_predictor(self.network)
        self._metadata = dict(metadata, repository=repository, device=self.device,
                              device_name=torch.cuda.get_device_name() if self.device == 'cuda' else self.device,
                              adapter_sha256=file_sha256(__file__),
                              loader_sha256=file_sha256(loader.__file__),
                              predictor_sha256=file_sha256(finaltest.__file__),
                              letterbox_sha256=file_sha256(finaltest.pilot.__file__),
                              confidence=.25, nms_iou=.7, retina_masks=True,
                              fixed_square_input=320, auto_fuse=False, autocast=False,
                              ground_truth_used=False, max_sam_boxes=4,
                              raw_control_mask='Union of ALL native surviving instance masks',
                              loader_path_alias_used=self._safe_reporef is not None,
                              loader_repository_path=str(loader_repo), original_repository_path=str(self.repo),
                              pinned_repository_modified=False,
                              loader_path_policy='Quote-free temporary symlink to verified original checkout; identical source/weight bytes',
                              process_side_effects='Restricted loader disables network and patches torch.load')

    def metadata(self):
        return dict(self._metadata)

    def detect(self, rgb):
        shape = _rgb(rgb)
        synchronize(self.device)
        started = time.perf_counter()
        mask, raw = self.predict(np.ascontiguousarray(rgb[:, :, ::-1]))
        if mask.dtype != np.bool_ or mask.shape != shape:
            raise ValueError('LeakHunter native mask geometry/dtype mismatch')
        boxes, scores = _boxes(raw['boxes'], shape, raw['scores'])
        synchronize(self.device)
        return Detection(boxes=boxes, scores=scores, raw_mask=mask.copy(), details=dict(
            raw, latency_ms=(time.perf_counter() - started) * 1000,
            all_boxes=raw['boxes'], all_scores=raw['scores'],
            sam_box_limit=4, mask_includes_all_survivors=True,
            input_kind='original_rgb', score_kind='uncalibrated detector confidence')).validate(shape)


class OwlDetector:
    """OWLv2 only; no SAM image model is constructed by this detector."""

    def __init__(self, prompt='gas plume', threshold=.06, device='cuda', difference=False):
        if not isinstance(prompt, str) or not prompt.strip() or not 0 < threshold < 1:
            raise ValueError('Nonempty prompt and threshold in (0,1) required')
        import torch
        from transformers import Owlv2ForObjectDetection, Owlv2Processor
        self.torch, self.device = torch, _device(device, torch)
        self.prompt, self.threshold, self.difference = prompt, float(threshold), bool(difference)
        self.processor = Owlv2Processor.from_pretrained(OWL_ID, revision=OWL_REV, local_files_only=True)
        self.model = _strict_model(Owlv2ForObjectDetection, OWL_ID, OWL_REV).to(self.device).eval()
        self._metadata = _metadata(OWL_ID, OWL_REV, self.model, self.processor, self.device)
        self._metadata.update(prompt=prompt, texts=[[prompt]], threshold=self.threshold,
                              nms_iou=.3, max_boxes=4, difference=self.difference,
                              difference_policy='abs(rgb - preceding causal EMA), fixed gain 1; observe explicitly',
                              background_alpha=.02, per_frame_normalization=False, autocast=False)
        self.reset()

    def metadata(self):
        return dict(self._metadata)

    def reset(self):
        self.background = None
        self.background_frames = 0

    def observe(self, rgb):
        _rgb(rgb)
        if self.background is None:
            self.background = rgb.astype(np.float32).copy()
        else:
            if self.background.shape != rgb.shape:
                raise ValueError('Background geometry changed; reset required')
            self.background = .98 * self.background + .02 * rgb.astype(np.float32)
        self.background_frames += 1

    def detect(self, rgb):
        shape = _rgb(rgb)
        image = rgb
        if self.difference:
            if self.background is None or self.background.shape != rgb.shape:
                raise ValueError('Difference mode requires preceding causal observe frames')
            image = np.clip(np.abs(rgb.astype(np.float32) - self.background), 0, 255).astype(np.uint8)
        synchronize(self.device)
        started = time.perf_counter()
        inputs = self.processor(text=[[self.prompt]], images=image,
                                return_tensors='pt', padding=True).to(self.device)
        with self.torch.inference_mode():
            outputs = self.model(**inputs)
        raw_score = float(outputs.logits[0, :, 0].sigmoid().max().cpu())
        post = getattr(self.processor, 'post_process_grounded_object_detection', None)
        if post is None:
            post = self.processor.image_processor.post_process_object_detection
        result = post(outputs=outputs, threshold=self.threshold, target_sizes=[shape])[0]
        labels = _array(result['labels']).reshape(-1)
        raw_boxes = _array(result['boxes'])[labels == 0].tolist()
        raw_scores = _array(result['scores'])[labels == 0].tolist()
        boxes, scores = _boxes(raw_boxes, shape, raw_scores, nms=.3)
        synchronize(self.device)
        return Detection(boxes=boxes, scores=scores, details=dict(
            latency_ms=(time.perf_counter() - started) * 1000, raw_max_similarity=raw_score,
            maximum_similarity=raw_score,
            all_boxes=raw_boxes, all_scores=raw_scores, texts=[[self.prompt]], threshold=self.threshold,
            score_kind='uncalibrated open vocabulary similarity; not methane probability',
            input_kind='causal_difference_fixed_gain_1' if self.difference else 'original_rgb',
            background_frames=self.background_frames, per_frame_normalization=False)).validate(shape)


class SamImage:
    def __init__(self, device='cuda'):
        import torch
        from transformers import Sam2Model, Sam2Processor
        self.torch, self.device = torch, _device(device, torch)
        self.processor = Sam2Processor.from_pretrained(SAM_ID, revision=SAM_REV, local_files_only=True)
        self.model = _strict_model(Sam2Model, SAM_ID, SAM_REV).to(self.device).eval()
        self._metadata = _metadata(SAM_ID, SAM_REV, self.model, self.processor, self.device)
        self._metadata.update(multimask_output=False, mask_threshold=0., max_boxes=4,
                              native_logits='Original decoder pred_masks, original shape, float32 copy before projection',
                              projected_logits='Derived source-resolution logits; raw binary source mask retained',
                              autocast='bfloat16' if self.device == 'cuda' else None)

    def metadata(self):
        return dict(self._metadata)

    def segment(self, rgb, boxes):
        shape = _rgb(rgb)
        boxes, _ = _boxes(boxes, shape)
        if not boxes:
            return Segmentation(np.zeros(shape, dtype=bool), details=dict(
                status='uninitialized', inference_executed=False, reason='no_detector_boxes'))
        synchronize(self.device)
        started = time.perf_counter()
        inputs = self.processor(images=rgb, input_boxes=[boxes], return_tensors='pt').to(self.device)
        with self.torch.inference_mode(), _autocast(self.torch, self.device):
            outputs = self.model(**inputs, multimask_output=False)
            native_logits = _array(outputs.pred_masks)
            logits = self.processor.post_process_masks(outputs.pred_masks.float().cpu(),
                original_sizes=inputs['original_sizes'].cpu(), binarize=False)[0]
        scores = _array(outputs.iou_scores).reshape(-1).tolist()
        result = _segmentation(logits, shape, scores, dict(status='detector_box_conditioned',
            prompt_boxes=boxes, prompt_source='caller detector boxes; never annotation lookup',
            score_kind='uncalibrated predicted mask IoU', inference_executed=True,
            mask_threshold=0., instance_count=len(boxes),
            native_logits_shape=list(native_logits.shape), projected_logits_derived=True),
            native_logits=native_logits)
        synchronize(self.device)
        result.details['latency_ms'] = (time.perf_counter() - started) * 1000
        return result


class SamVideo:
    """Compose the already verified HF VideoMemory binding with direct box seeds.

    Reset on detector refresh; this discards session state, not model weights.
    Bounds are the existing 120 frames / 8 seconds / .25-second maximum PTS gap.
    No future video or reference mask is ever supplied to the session.
    """

    def __init__(self, device='cuda'):
        import torch
        from ..video_memory import VideoMemory
        self.torch, self.device = torch, _device(device, torch)
        self.memory = VideoMemory(device=self.device, capture_logits=True)
        self._metadata = dict(self.memory.metadata(),
            **_metadata(SAM_ID, SAM_REV, self.memory.model, self.memory.processor, self.device))
        self._metadata.update(prompt_source='direct detector boxes', max_frames=120,
            seed_source='direct detector boxes; never reference masks',
            max_duration_seconds=8, max_gap_seconds=.25, max_vision_features_cache_size=1,
            max_boxes=4, mask_threshold=0., autocast='bfloat16' if self.device == 'cuda' else None,
            native_logits='Original decoder pred_masks, original shape, float32 copy before projection',
            projected_logits='Derived source-resolution logits; raw binary source mask retained',
            score_kind='uncalibrated object presence logits; not mask IoU or methane probability')

    def metadata(self):
        return dict(self._metadata)

    def reset(self):
        self.memory.reset()

    def step(self, rgb, source_pts, boxes=None):
        shape = _rgb(rgb)
        if source_pts is None:
            raise ValueError('Source PTS required for causal video inference')
        prompts = None
        if boxes is not None:
            prompts, _ = _boxes(boxes, shape)
            self.reset()  # Detector refresh always starts a bounded fresh session.
        memory = self.memory
        memory.bounds.accept(source_pts, rgb.shape)
        if not memory.seeded and not prompts:
            return Segmentation(np.zeros(shape, dtype=bool), details=dict(
                status='uninitialized', inference_executed=False, source_pts=source_pts,
                reason='no_detector_boxes; not evidence of methane absence'))
        synchronize(self.device)
        started = time.perf_counter()
        inputs = memory.processor(images=rgb, device='cpu', return_tensors='pt').to(self.device)
        if prompts:
            memory.processor.process_new_points_or_boxes_for_video_frame(
                memory.session, frame_idx=0, obj_ids=list(range(1, len(prompts) + 1)),
                input_boxes=[prompts], original_size=shape)
            memory.seeded, memory.seed_pts = True, source_pts
        with self.torch.inference_mode(), _autocast(self.torch, self.device):
            outputs = memory.model(inference_session=memory.session, frame=inputs.pixel_values[0])
            native_logits = _array(outputs.pred_masks)
            logits = memory.processor.post_process_masks([outputs.pred_masks.float()],
                original_sizes=inputs.original_sizes, binarize=False)[0]
        object_scores = getattr(outputs, 'object_score_logits', None)
        scores = [] if object_scores is None else _array(object_scores).reshape(-1).tolist()
        result = _segmentation(logits, shape, scores, dict(
            status='detector_box_conditioned' if prompts else 'memory_propagated',
            inference_executed=True, source_pts=source_pts, seed_pts=memory.seed_pts,
            frame_idx=getattr(outputs, 'frame_idx', None),
            object_ids=getattr(outputs, 'object_ids', None), prompt_boxes=prompts,
            score_kind='uncalibrated object presence logits', mask_threshold=0.,
            session_frames=memory.bounds.count,
            native_logits_shape=list(native_logits.shape), projected_logits_derived=True),
            native_logits=native_logits)
        # Preserve every instance; do not collapse logits to a max-support display map.
        memory.last_mask_logits = result.logits.copy()
        synchronize(self.device)
        result.details['latency_ms'] = (time.perf_counter() - started) * 1000
        return result
