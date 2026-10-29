"""Display-only SAM2 mask compositing. Never changes predictions or inference."""
from dataclasses import dataclass,asdict
import math
import re
import numpy as np
from scipy.ndimage import distance_transform_edt,gaussian_filter


@dataclass(frozen=True)
class OverlayStyle:
    strong_color:str='#DC2626'
    medium_color:str='#F97316'
    weak_color:str='#FDBA74'
    medium_threshold:float=.45
    strong_threshold:float=.85
    interior_opacity:float=.72
    transition_opacity:float=.48
    feather_pixels:float=2.
    core_width_pixels:float=4.
    gaussian_sigma:float=.65
    brightness_weight:float=.15
    brightness_low:float=0.
    brightness_high:float=255.
    temporal_strength:float=.2
    maximum_frame_gap_seconds:float=.25

    def __post_init__(self):
        values=asdict(self)
        for key,value in values.items():
            if key.endswith('_color'):
                if not isinstance(value,str) or not re.fullmatch(r'#[0-9A-Fa-f]{6}',value):raise ValueError('Expected #RRGGBB palette color')
            elif not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError('Finite display parameters required')
        if not 0<self.medium_threshold<self.strong_threshold<=1:raise ValueError('Ordered color thresholds required')
        if not 0<=self.transition_opacity<=self.interior_opacity<=1:raise ValueError('Opacity must be in [0,1]')
        if not 0<self.feather_pixels<=8 or not 0<self.core_width_pixels<=20 or not 0<=self.gaussian_sigma<=3:raise ValueError('Invalid feather/smoothing strength')
        if not 0<=self.brightness_weight<=1 or not 0<=self.temporal_strength<1:raise ValueError('Invalid display blending weights')
        if not 0<=self.brightness_low<self.brightness_high<=255:raise ValueError('Fixed 8-bit brightness range required')
        if not 0<self.maximum_frame_gap_seconds<=1:raise ValueError('Invalid temporal display reset interval')


def membership_support(logits):
    values=np.asarray(logits)
    if values.ndim!=2 or not np.isfinite(values).all():raise ValueError('Finite 2D mask logits required')
    return 1/(1+np.exp(-np.clip(values.astype(np.float32),-30,30)))


def _smoothstep(value):
    x=np.clip(value,0,1)
    return x*x*(3-2*x)


def support_colors(strength,style=None):
    """Continuous weak-orange → orange → red; no quantized bands or contours."""
    style=style or OverlayStyle();x=np.asarray(strength,dtype=np.float32)
    def color(value):return np.array([int(value[i:i+2],16) for i in (1,3,5)],dtype=np.float32)
    weak,medium,strong=(color(c) for c in (style.weak_color,style.medium_color,style.strong_color))
    lower=_smoothstep(x/style.medium_threshold)[...,None]
    upper=_smoothstep((x-style.medium_threshold)/(style.strong_threshold-style.medium_threshold))[...,None]
    colors=np.where((x<style.medium_threshold)[...,None],weak+(medium-weak)*lower,medium+(strong-medium)*upper)
    return colors.round().astype(np.uint8)


class SegmentationOverlay:
    """Causal display shading; mask geometry is always the current binary prediction.

    Temporal smoothing applies only to color strength on overlapping current/previous
    mask pixels. No past alpha is carried forward; seeks/gaps reset display history.
    No connected-component filtering, automatic equipment exclusion or dilation.
    """
    def __init__(self,style=None):
        self.style=style or OverlayStyle()
        self.previous_strength=self.previous_mask=self.previous_pts=None
        self.last_rgba=None

    def render(self,rgb,logits=None,*,mask=None,pts_seconds=None):
        frame=np.asarray(rgb)
        if frame.dtype!=np.uint8 or frame.ndim!=3 or frame.shape[2]!=3:raise ValueError('uint8 RGB frame required')
        support=membership_support(logits) if logits is not None else None
        if mask is None:
            if support is None:raise ValueError('A binary prediction or actual model logits are required')
            mask=np.asarray(logits)>0
        binary=np.asarray(mask)
        if binary.dtype!=np.bool_ or binary.shape!=frame.shape[:2]:raise ValueError('Boolean source-sized binary mask required')
        if support is not None and support.shape!=binary.shape:raise ValueError('Logits and mask must share geometry')
        if pts_seconds is not None and (not math.isfinite(pts_seconds) or pts_seconds<0):raise ValueError('Invalid source PTS')
        style=self.style
        if not binary.any():
            self.previous_strength=self.previous_mask=self.previous_pts=None
            self.last_rgba=np.zeros((*binary.shape,4),dtype=np.uint8)
            return frame.copy()
        # Padding gives the frame boundary a well-defined distance without dilation.
        distance=distance_transform_edt(np.pad(binary,1))[1:-1,1:-1]
        core=_smoothstep((distance-.5)/style.core_width_pixels)
        intensity=(frame.astype(np.float32)*np.array([.2126,.7152,.0722])).sum(axis=-1)
        brightness=np.clip((intensity-style.brightness_low)/(style.brightness_high-style.brightness_low),0,1)
        evidence=(1-style.brightness_weight)*(support if support is not None else .8)+style.brightness_weight*brightness
        strength=core*evidence
        continuous=(pts_seconds is not None and self.previous_pts is not None and self.previous_mask is not None
                    and self.previous_mask.shape==binary.shape and 0<pts_seconds-self.previous_pts<=style.maximum_frame_gap_seconds)
        if continuous:
            overlap=binary&self.previous_mask
            strength=np.where(overlap,(1-style.temporal_strength)*strength+style.temporal_strength*self.previous_strength,strength)
        self.previous_strength=strength.copy();self.previous_mask=binary.copy();self.previous_pts=pts_seconds
        colors=support_colors(strength,style).astype(np.float32)
        edge=_smoothstep((distance-.5)/style.feather_pixels)
        alpha=(style.transition_opacity+(style.interior_opacity-style.transition_opacity)*core)*edge*binary
        premultiplied=colors*alpha[...,None]
        if style.gaussian_sigma:
            alpha=gaussian_filter(alpha,style.gaussian_sigma,mode='constant',truncate=3)
            premultiplied=np.stack([gaussian_filter(premultiplied[...,i],style.gaussian_sigma,mode='constant',truncate=3) for i in range(3)],axis=-1)
        # Strictly source-mask constrained: no external halo, trails or expanded mask.
        alpha=np.where(binary,np.clip(alpha,0,style.interior_opacity),0)
        colors=np.divide(premultiplied, np.maximum(alpha[...,None],1e-8),out=np.zeros_like(premultiplied),where=alpha[...,None]>1e-8)
        colors=np.clip(colors,0,255)
        self.last_rgba=np.concatenate([colors,alpha[...,None]*255],axis=-1).round().astype(np.uint8)
        composite=frame*(1-alpha[...,None])+colors*alpha[...,None]
        return composite.round().clip(0,255).astype(np.uint8)


def heatmap_overlay(rgb,logits=None,*,mask=None,style=None):
    """Backward-compatible stateless entry point; use SegmentationOverlay for videos."""
    return SegmentationOverlay(style).render(rgb,logits,mask=mask)
