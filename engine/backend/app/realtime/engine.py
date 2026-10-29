"""Automatic, source-coordinate inference and recovery. No HTTP or model imports."""
from dataclasses import dataclass,asdict
import time
import numpy as np
from .temporal import CausalEvidence


@dataclass(frozen=True)
class PipelineConfig:
    architecture: str = 'leakhunter'
    refresh_seconds: float = 1.
    retry_seconds: float = .25
    max_age_seconds: float = .5
    max_gap_seconds: float = .25
    max_missed_refreshes: int = 2
    video: bool = True
    temporal_guard: bool = False

    def __post_init__(self):
        if self.architecture not in {'leakhunter','leakhunter-sam','owl-sam'}:
            raise ValueError('Unknown experimental architecture')
        if not .1<=self.refresh_seconds<=6 or not .05<=self.retry_seconds<=2 or not .01<=self.max_age_seconds<=5:
            raise ValueError('Bounded detector refresh/retry/age required')
        if not .01<=self.max_gap_seconds<=.25 or not 1<=self.max_missed_refreshes<=3:
            raise ValueError('Bounded source gaps/missed detections required')


class Pipeline:
    def __init__(self,detector,segmenter=None,tracker=None,config=None,clock=time.perf_counter):
        self.config=config or PipelineConfig();self.detector=detector
        self.segmenter,self.tracker=segmenter,tracker
        if self.config.architecture!='leakhunter' and ((not self.config.video and segmenter is None) or (self.config.video and tracker is None)):
            raise ValueError('SAM architectures require the configured segmenter/tracker')
        self.clock=clock;self.evidence=CausalEvidence();self.resets=0;self.reset()

    def reset(self):
        self.last_pts=self.last_epoch=self.last_mask=self.last_shape=None
        self.last_detect=self.session_start=None
        self.seeded=False;self.session_count=0;self.missed=0;self.drift_streak=0
        self.evidence.reset()
        if self.tracker is not None:self.tracker.reset()
        self.resets+=1

    def process(self,frame,*,check_age=True):
        started=self.clock();shape=frame.rgb.shape[:2];config=self.config
        row=dict(frame_id=frame.frame_id,source_pts=frame.source_pts,epoch=frame.epoch,clock_kind=frame.clock_kind,
                 status='uninitialized',reason=None,mask=None,logits=None,detector_mask=None,sam_mask=None,
                 native_logits=None,temporal_guard=config.temporal_guard,
                 validated_mask=None,new_mask_inference=False,detector_called=False,boxes=[],scores=[],
                 timings_ms={},evidence={},source_shape=list(shape))
        age=max(0,started-frame.captured_at)
        if check_age and age>config.max_age_seconds:
            self.reset();row.update(status='stale',reason='source_frame_exceeded_age_budget',input_age_ms=1000*age)
            return row
        discontinuity=(self.last_pts is not None and (frame.epoch!=self.last_epoch or frame.source_pts<=self.last_pts
                         or frame.source_pts-self.last_pts>config.max_gap_seconds or self.last_shape!=shape))
        if discontinuity:self.reset();row['reason']='source_discontinuity_reset'
        t=self.clock();info=self.evidence.observe(frame.rgb);row['timings_ms']['temporal']=1000*(self.clock()-t)
        if info['scene_cut']:
            self.reset();info=self.evidence.observe(frame.rgb);row['reason']='scene_cut_reset'
        self.last_pts,self.last_epoch,self.last_shape=frame.source_pts,frame.epoch,shape
        try:
            use_sam=config.architecture!='leakhunter'
            due=(not use_sam or not config.video or self.last_detect is None or
                 frame.source_pts-self.last_detect >= (config.refresh_seconds if self.seeded else config.retry_seconds) or
                 self.session_count>=90 or self.session_start is not None and frame.source_pts-self.session_start>=6)
            detection=None;segmentation=None
            if due:
                t=self.clock();detection=self.detector.detect(frame.rgb).validate(shape)
                row['timings_ms']['detection']=1000*(self.clock()-t)
                row.update(detector_called=True,boxes=detection.boxes,scores=detection.scores,
                           detector_mask=detection.raw_mask,detector_details=detection.details)
                self.last_detect=frame.source_pts
            if not use_sam:
                if detection.raw_mask is None:raise ValueError('LeakHunter control requires actual native segmentation')
                row.update(mask=detection.raw_mask,logits=detection.raw_logits,new_mask_inference=True,
                           status='model_prediction' if detection.raw_mask.any() else 'no_candidate_detected')
            elif not config.video:
                if detection.boxes:
                    t=self.clock();segmentation=self.segmenter.segment(frame.rgb,detection.boxes).validate(shape)
                    row['timings_ms']['sam']=1000*(self.clock()-t)
                else:
                    row['status']=self.no_candidate_state(detection)
            else:
                if detection is not None and detection.boxes:
                    self.tracker.reset();self.seeded=True;self.session_start=frame.source_pts;self.session_count=0;self.missed=0
                    t=self.clock();segmentation=self.tracker.step(frame.rgb,frame.source_pts,boxes=detection.boxes).validate(shape)
                    row['timings_ms']['sam']=1000*(self.clock()-t);row['reason']='automatic_detector_box_initialization'
                else:
                    if detection is not None:
                        self.missed+=1
                        if self.missed>=config.max_missed_refreshes:
                            self.tracker.reset();self.seeded=False;self.session_count=0
                            row.update(status=self.no_candidate_state(detection),reason='missed_detector_refresh_budget')
                    if self.seeded:
                        t=self.clock();segmentation=self.tracker.step(frame.rgb,frame.source_pts).validate(shape)
                        row['timings_ms']['sam']=1000*(self.clock()-t)
                        row['reason']='memory_propagated' if not self.missed else 'bounded_missing_detector_grace'
                    elif detection is not None:
                        row['status']=self.no_candidate_state(detection)
            if segmentation is not None:
                self.session_count+=1
                row.update(mask=segmentation.mask,logits=segmentation.logits,sam_mask=segmentation.mask,
                           native_logits=segmentation.native_logits,
                           sam_scores=segmentation.scores,sam_details=segmentation.details,new_mask_inference=True,
                           status='model_prediction' if segmentation.mask.any() else 'no_candidate_detected')
                if not segmentation.mask.any() and config.video:
                    self.tracker.reset();self.seeded=False;self.session_count=0
                if self.last_mask is not None and self.last_mask.any() and segmentation.mask.any():
                    intersection=int((self.last_mask&segmentation.mask).sum());union=int((self.last_mask|segmentation.mask).sum())
                    ratio=segmentation.mask.sum()/self.last_mask.sum()
                    bad=intersection/union<.05 and (ratio>4 or ratio<.05) and self.missed>0
                    self.drift_streak=self.drift_streak+1 if bad else 0
                    if self.drift_streak>=2:
                        # Preserve the rejected raw prediction; suppress only its live display.
                        row.update(status='tracking_failure',reason='two_extreme_disagreements_with_missing_detector')
                        self.tracker.reset();self.seeded=False;self.last_detect=None
            raw=row['mask']
            if raw is not None:
                row['validated_mask']=self.evidence.validated_mask(raw,info,config.temporal_guard)
                self.last_mask=raw.copy()
            row['evidence']=self.evidence.describe(info,raw)
            row['_display_evidence']=info
        except Exception as exc:
            self.reset();row.update(status='tracking_failure',reason=type(exc).__name__+': '+str(exc),new_mask_inference=False)
        row['timings_ms']['pipeline']=1000*(self.clock()-started)
        row['input_age_ms']=1000*age;row['resets']=self.resets
        return row

    @staticmethod
    def no_candidate_state(detection):
        score=detection.details.get('maximum_similarity')
        return 'low_confidence_candidate' if score is not None and score>=.03 else 'no_candidate_detected'

    def metadata(self):
        return dict(config=asdict(self.config),semantic='Visible plume model predictions; methane identity and absence unverified',
                    fallback='No oracle selection; original detector and SAM masks remain separate',resets=self.resets)
