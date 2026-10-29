"""Image-derived infrared visibility for display only; no model/physical inference."""
from dataclasses import dataclass,asdict
import math
import numpy as np
from scipy.ndimage import gaussian_filter,distance_transform_edt
from .plume_heatmap import OverlayStyle,support_colors,membership_support


@dataclass(frozen=True)
class VisibilityStyle:
    strong_signal:float=18.
    minimum_noise_floor:float=1.5
    noise_sigma:float=2.5
    local_contrast_weight:float=.12
    motion_weight:float=.08
    spatial_sigma:float=.6
    temporal_strength:float=.15
    opacity_gamma:float=1.35
    maximum_opacity:float=.78
    feather_pixels:float=1.5
    maximum_frame_gap_seconds:float=.25
    static_brightness_threshold:float=170.
    static_noise_threshold:float=3.
    static_persistence_fraction:float=.95

    def __post_init__(self):
        if any(not isinstance(v,(float,int)) or not math.isfinite(v) for v in asdict(self).values()):raise ValueError('Finite visibility parameters required')
        if not 0<self.strong_signal<=255 or not 0<=self.minimum_noise_floor<self.strong_signal or not 0<=self.noise_sigma<=10:raise ValueError('Invalid fixed visibility calibration')
        if not 0<=self.local_contrast_weight<=1 or not 0<=self.motion_weight<=1:raise ValueError('Invalid supplementary evidence weights')
        if not 0<=self.spatial_sigma<=2 or not 0<=self.temporal_strength<1 or not .5<=self.opacity_gamma<=4:raise ValueError('Invalid visibility smoothing')
        if not 0<self.maximum_opacity<=1 or not 0<self.feather_pixels<=5 or not 0<self.maximum_frame_gap_seconds<=1:raise ValueError('Invalid opacity/temporal parameters')
        if not 0<=self.static_brightness_threshold<=255 or not 0<=self.static_noise_threshold<=10 or not .5<=self.static_persistence_fraction<=1:raise ValueError('Invalid reference evidence thresholds')


def grayscale(rgb):
    a=np.asarray(rgb)
    if a.dtype!=np.uint8 or a.ndim!=3 or a.shape[2]!=3:raise ValueError('uint8 RGB image required')
    return (a.astype(np.float32)*np.array([.2126,.7152,.0722],dtype=np.float32)).sum(axis=-1)


def reference_statistics(frames,style=None):
    """Input frames must already be registered to a common reference coordinate frame."""
    style=style or VisibilityStyle();stack=np.asarray(frames,dtype=np.float32)
    if stack.ndim!=3 or len(stack)<8 or not np.isfinite(stack).all():raise ValueError('At least8finite registered reference frames required')
    background=np.median(stack,axis=0)
    noise=1.4826*np.median(np.abs(stack-background),axis=0)
    persistence=(stack>=style.static_brightness_threshold).mean(axis=0)
    # Exclusion is justified by persistent brightness AND temporal stability, not size.
    equipment=(background>=style.static_brightness_threshold)&(noise<=style.static_noise_threshold)&(persistence>=style.static_persistence_fraction)
    return background.astype(np.float32),noise.astype(np.float32),equipment


class InfraredVisibilityOverlay:
    def __init__(self,style=None,palette=None):
        self.style=style or VisibilityStyle();self.palette=palette or OverlayStyle()
        self.previous_visibility=self.previous_pts=None
        self.last_rgba=self.last_visibility=self.last_evidence=None

    def render(self,rgb,logits,mask,background,noise,equipment,*,previous_registered=None,previous_visibility_registered=None,pts_seconds=None,registration_valid=True):
        gray=grayscale(rgb);binary=np.asarray(mask);support=membership_support(logits)
        arrays=[np.asarray(x) for x in (background,noise,equipment)]
        if binary.dtype!=np.bool_ or binary.shape!=gray.shape or support.shape!=gray.shape or any(a.shape!=gray.shape for a in arrays):raise ValueError('Visibility arrays must share source geometry')
        if any(not np.isfinite(a).all() for a in arrays):raise ValueError('Finite reference evidence required')
        if pts_seconds is not None and (not math.isfinite(pts_seconds) or pts_seconds<0):raise ValueError('Invalid source PTS')
        s=self.style;equipment=np.asarray(equipment,dtype=bool)
        if not registration_valid or not binary.any():
            self.previous_visibility=self.previous_pts=None
            self.last_rgba=np.zeros((*gray.shape,4),dtype=np.uint8)
            self.last_visibility=np.zeros_like(gray);self.last_evidence=np.zeros_like(gray)
            return np.asarray(rgb).copy()
        floor=np.maximum(s.minimum_noise_floor,s.noise_sigma*np.asarray(noise))
        positive=np.maximum(gray-np.asarray(background)-floor,0)
        contrast=np.maximum(gray-gaussian_filter(gray,4.,mode='nearest'),0)
        motion=np.zeros_like(gray)
        if previous_registered is not None:
            previous=np.asarray(previous_registered,dtype=np.float32)
            if previous.shape!=gray.shape or not np.isfinite(previous).all():raise ValueError('Invalid registered previous frame')
            motion=np.maximum(np.abs(gray-previous)-floor,0)
        # Supplementary evidence never creates visibility where the reference residual is absent.
        evidence=positive+s.local_contrast_weight*np.minimum(contrast,positive)+s.motion_weight*np.minimum(motion,positive)
        active=binary&~equipment&(positive>0)
        visible=np.clip(evidence/s.strong_signal,0,1)
        if s.spatial_sigma:visible=gaussian_filter(visible,s.spatial_sigma,mode='constant',truncate=3)
        visible=np.where(active,visible,0)
        continuous=(pts_seconds is not None and self.previous_pts is not None and self.previous_visibility is not None
                    and self.previous_visibility.shape==gray.shape and 0<pts_seconds-self.previous_pts<=s.maximum_frame_gap_seconds)
        if continuous:
            history=self.previous_visibility if previous_visibility_registered is None else np.asarray(previous_visibility_registered)
            if history.shape!=gray.shape or not np.isfinite(history).all():raise ValueError('Invalid registered visibility history')
            visible=np.where(active,(1-s.temporal_strength)*visible+s.temporal_strength*history,0)
        self.previous_visibility=visible.copy();self.previous_pts=pts_seconds
        distance=distance_transform_edt(np.pad(binary,1))[1:-1,1:-1]
        edge=np.clip((distance-.5)/s.feather_pixels,0,1);edge=edge*edge*(3-2*edge)
        alpha=s.maximum_opacity*np.power(visible,s.opacity_gamma)*(.5+.5*support)*edge
        # Exact current-evidence gating keeps genuine transparent gaps and no-equipment regions.
        alpha=np.where(active,alpha,0)
        colors=support_colors(visible,self.palette).astype(np.float32)
        self.last_rgba=np.concatenate([colors,alpha[...,None]*255],axis=-1).round().astype(np.uint8)
        self.last_rgba[~active]=0
        self.last_visibility=visible.astype(np.float32);self.last_evidence=evidence.astype(np.float32)
        return np.round(np.asarray(rgb)*(1-alpha[...,None])+colors*alpha[...,None]).clip(0,255).astype(np.uint8)
