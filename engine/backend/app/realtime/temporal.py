"""Causal, interpretable supporting evidence, never chemical identification."""
from collections import deque
import numpy as np


class CausalEvidence:
    def __init__(self, history=8):
        if not 4<=history<=16: raise ValueError('Bounded history of 4..16 frames required')
        self.limit=history
        self.reset()

    def reset(self):
        self.history=deque(maxlen=self.limit)
        self.previous=None

    def observe(self,rgb):
        import cv2
        gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY).astype(np.float32)
        info=dict(ready=False,registration_valid=False,scene_cut=False,translation_xy=[0.,0.],
                  background=None,noise=None,static=None,previous=None,residual=None)
        if self.previous is not None and self.previous.shape!=gray.shape:self.reset()
        if self.previous is not None and self.previous.shape==gray.shape:
            info['scene_cut']=float((np.abs(gray-self.previous)>25).mean())>.6
            scale=min(1.,160/gray.shape[1])
            size=(max(8,round(gray.shape[1]*scale)),max(8,round(gray.shape[0]*scale)))
            a=cv2.resize(self.previous,size);b=cv2.resize(gray,size)
            (dx,dy),response=cv2.phaseCorrelate(a,b)
            dx,dy=dx/scale,dy/scale
            valid=bool(np.isfinite([dx,dy,response]).all() and response>=.15 and abs(dx)<=4 and abs(dy)<=4)
            info.update(registration_valid=valid,translation_xy=[float(dx) if np.isfinite(dx) else 0.,float(dy) if np.isfinite(dy) else 0.],registration_response=float(response) if np.isfinite(response) else None)
            if valid:
                matrix=np.array([[1,0,dx],[0,1,dy]],np.float32)
                aligned=[cv2.warpAffine(v,matrix,(gray.shape[1],gray.shape[0]),flags=cv2.INTER_LINEAR,borderMode=cv2.BORDER_REPLICATE) for v in self.history]
                self.history=deque(aligned,maxlen=self.limit)
                info['previous']=cv2.warpAffine(self.previous,matrix,(gray.shape[1],gray.shape[0]),borderMode=cv2.BORDER_REPLICATE)
            else:
                # Do not reuse unregistered backgrounds across camera movement.
                self.history.clear()
        if info['scene_cut']:
            self.history.clear()
        if len(self.history)>=self.limit:
            stack=np.stack(self.history)
            background=np.median(stack,axis=0)
            noise=1.4826*np.median(np.abs(stack-background),axis=0)
            persistent=(stack>=170).mean(axis=0)>=.95
            static=(background>=170)&(noise<=1.5)&persistent
            info.update(ready=True,background=background,noise=noise,static=static,
                        residual=np.abs(gray-background))
        self.history.append(gray.copy());self.previous=gray.copy()
        return info

    @staticmethod
    def describe(info,mask):
        result={k:v for k,v in info.items() if k not in {'background','noise','static','previous','residual'}}
        if info['ready'] and mask is not None and mask.any():
            threshold=np.maximum(1.5,2.5*info['noise'])
            result.update(changing_fraction=float((info['residual'][mask]>threshold[mask]).mean()),
                          static_bright_fraction=float(info['static'][mask].mean()))
        return result

    @staticmethod
    def validated_mask(raw,info,enabled=False):
        # Optional measurable ablation. No small-component or low-motion filtering.
        result=raw.copy()
        if enabled and info['ready'] and info['registration_valid']:
            persistent_unchanged=info['static']&(info['residual']<=np.maximum(1.5,2.5*info['noise']))
            result &= ~persistent_unchanged
        return result
