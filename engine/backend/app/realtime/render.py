"""Reuse verified rendering, with strict source-frame and prediction-age gates."""
import numpy as np
from ..plume_heatmap import SegmentationOverlay
from ..infrared_visibility import InfraredVisibilityOverlay


class Renderer:
    def __init__(self,mode='segmentation'):
        if mode not in {'segmentation','visibility','binary'}:raise ValueError('Unknown display mode')
        self.mode=mode;self.segmentation=SegmentationOverlay();self.visibility=InfraredVisibilityOverlay()

    def render(self,frame,result):
        if (result['frame_id']!=frame.frame_id or result['source_pts']!=frame.source_pts
                or result['epoch']!=frame.epoch):raise ValueError('Overlay cannot cross source-frame identities')
        mask=result.get('validated_mask')
        if result['status'] not in {'model_prediction','no_candidate_detected'} or mask is None:
            self.segmentation=SegmentationOverlay();self.visibility=InfraredVisibilityOverlay()
            return frame.rgb.copy(),dict(mode='none',label='Unknown / no current segmentation',painted_pixels=0)
        if self.mode=='binary':
            raw=result['mask']
            return np.repeat((raw.astype(np.uint8)*255)[...,None],3,axis=2),dict(mode='binary',label='Raw segmentation',painted_pixels=int(raw.sum()))
        logits=result.get('logits')
        if logits is not None:logits=np.asarray(logits).reshape(-1,*mask.shape).max(axis=0)
        evidence=result.get('_display_evidence',{})
        if self.mode=='visibility' and logits is not None and evidence.get('ready') and evidence.get('registration_valid'):
            output=self.visibility.render(frame.rgb,logits,mask,evidence['background'],evidence['noise'],evidence['static'],
                  previous_registered=evidence['previous'],pts_seconds=frame.source_pts)
            rgba=self.visibility.last_rgba
            metadata=dict(mode='visibility',label='Infrared plume visibility',reference='Causal prior frames; not certified gas-free')
        else:
            output=self.segmentation.render(frame.rgb,logits,mask=mask,pts_seconds=frame.source_pts)
            rgba=self.segmentation.last_rgba
            metadata=dict(mode='segmentation',label='Model plume segmentation',reference=None)
        if rgba is not None and np.any(rgba[~mask,3]):raise ValueError('Rendering escaped segmentation support')
        metadata['painted_pixels']=int((rgba[...,3]>0).sum()) if rgba is not None else 0
        return output,metadata
