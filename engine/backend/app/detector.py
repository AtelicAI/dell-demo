"""Local, pretrained, LangGas-inspired inference. No upstream script execution.

Imports heavy optional dependencies only when a detector worker is constructed.
Model scores and masks describe visual plume candidates, never gas identity,
source equipment, rate or a calibrated probability.
"""
import hashlib
import json
import math
from pathlib import Path
import time
import numpy as np

OWL_ID='google/owlv2-base-patch16-ensemble'
OWL_REV='cfd3195ba4ea9592eec887ded089f4c08eff231d'
SAM_ID='facebook/sam2.1-hiera-small'
SAM_REV='ee5bba1d82bb8749febdf90f45e84b687142ba03'
METHOD='langgas-inspired-owlv2-sam2-v1'


def file_sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def box_iou(a,b):
    x=max(a[0],b[0]);y=max(a[1],b[1]);xx=min(a[2],b[2]);yy=min(a[3],b[3])
    area=max(0,xx-x)*max(0,yy-y)
    total=max(0,a[2]-a[0])*max(0,a[3]-a[1])+max(0,b[2]-b[0])*max(0,b[3]-b[1])-area
    return area/total if total else 0.


class TemporalFilter:
    def __init__(self):self.history=[]
    def update(self,boxes,width,height):
        good=[b for b in boxes if (b[2]-b[0])*(b[3]-b[1])<width*height*.9]
        accepted=[b for b in good if any(box_iou(b,old)>.3 or max(abs(x-y) for x,y in zip(b,old))<40 for frame in self.history for old in frame)]
        self.history.append(good)
        self.history=self.history[-10:]
        return accepted


class PretrainedDetector:
    def __init__(self,device='auto',threshold=.12,temporal=True):
        if not 0<threshold<1:raise ValueError('threshold must be in (0,1)')
        import torch
        from transformers import Owlv2Processor,Owlv2ForObjectDetection,Sam2Processor,Sam2Model
        self.torch=torch
        self.device=('mps' if torch.backends.mps.is_available() else 'cuda' if torch.cuda.is_available() else 'cpu') if device=='auto' else device
        if self.device not in ('cpu','mps','cuda'):raise ValueError('Unsupported inference device')
        torch.set_num_threads(4)
        self.threshold=threshold
        self.implementation_sha256=file_sha256(__file__)
        self.temporal=temporal
        started=time.perf_counter()
        self.processor=Owlv2Processor.from_pretrained(OWL_ID,revision=OWL_REV)
        self.owl=Owlv2ForObjectDetection.from_pretrained(OWL_ID,revision=OWL_REV,use_safetensors=True).to(self.device).eval()
        self.sam_processor=Sam2Processor.from_pretrained(SAM_ID,revision=SAM_REV)
        self.sam=Sam2Model.from_pretrained(SAM_ID,revision=SAM_REV,use_safetensors=True).to(self.device).eval()
        self.load_seconds=time.perf_counter()-started
        self.texts=[['white steam','white human, car, bird, bike, and other objects']]
        self.reset()

    def sync(self):
        if self.device=='mps':self.torch.mps.synchronize()
        elif self.device=='cuda':self.torch.cuda.synchronize()

    def reset(self):
        import cv2
        self.background=cv2.createBackgroundSubtractorMOG2(history=30,detectShadows=True)
        self.tracker=TemporalFilter()
        self.background_frames=0

    def observe(self,bgr):
        self.background.apply(bgr)
        self.background_frames+=1

    def infer(self,bgr,valid_region=None):
        import cv2
        import torchvision
        from PIL import Image
        self.sync();started=time.perf_counter()
        background=self.background.getBackgroundImage()
        if background is None:raise ValueError('Background must be causally initialized before inference')
        diff=cv2.absdiff(bgr.astype(np.float32),background.astype(np.float32))
        high=float(diff.mean()+diff.std())
        gain=min(15.,255/max(high,1e-6))
        diff=np.clip(diff*gain,0,255).astype(np.uint8)
        if valid_region is not None:diff[~valid_region]=0
        image=Image.fromarray(cv2.cvtColor(diff,cv2.COLOR_BGR2RGB))
        inputs=self.processor(text=self.texts,images=image,return_tensors='pt',padding=True).to(self.device)
        with self.torch.inference_mode():outputs=self.owl(**inputs)
        raw_score=float(outputs.logits[0,:,0].sigmoid().max().cpu())
        post=getattr(self.processor,'post_process_grounded_object_detection',None)
        if post is None:post=self.processor.image_processor.post_process_object_detection
        result=post(outputs=outputs,threshold=self.threshold,target_sizes=[(bgr.shape[0],bgr.shape[1])])[0]
        boxes=result['boxes'].detach().cpu();scores=result['scores'].detach().cpu();labels=result['labels'].detach().cpu()
        positive=labels==0
        boxes=boxes[positive];scores=scores[positive]
        keep=torchvision.ops.nms(boxes,scores,.3)[:4]
        boxes=boxes[keep].numpy().tolist()
        h,w=bgr.shape[:2]
        boxes=[[max(0.,min(w,float(b[0]))),max(0.,min(h,float(b[1]))),max(0.,min(w,float(b[2]))),max(0.,min(h,float(b[3])))] for b in boxes]
        boxes=[b for b in boxes if b[2]>b[0] and b[3]>b[1]]
        accepted=self.tracker.update(boxes,w,h) if self.temporal else boxes
        mask=np.zeros((h,w),dtype=bool)
        if accepted:
            sam_inputs=self.sam_processor(images=image,input_boxes=[accepted],return_tensors='pt').to(self.device)
            with self.torch.inference_mode():sam_outputs=self.sam(**sam_inputs,multimask_output=False)
            masks=self.sam_processor.post_process_masks(sam_outputs.pred_masks.cpu(),sam_inputs['original_sizes'].cpu())[0]
            mask=np.any(masks.numpy().reshape(-1,h,w),axis=0)
        if valid_region is not None:mask &= valid_region
        self.sync();elapsed=(time.perf_counter()-started)*1000
        contours,_=cv2.findContours(mask.astype(np.uint8),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        polygons=[]
        for contour in sorted(contours,key=cv2.contourArea,reverse=True)[:4]:
            if cv2.contourArea(contour)<4:continue
            approximation=cv2.approxPolyDP(contour,max(1.,.01*cv2.arcLength(contour,True)),True).reshape(-1,2)
            if len(approximation)>=3:polygons.append([[float(x/w),float(y/h)] for x,y in approximation])
        return dict(prediction=int(mask.sum()>=4),score=raw_score,
                    score_kind='uncalibrated_open_vocabulary_similarity_not_methane_probability',
                    raw_positive_boxes=boxes,boxes=accepted,normalized_boxes=[[b[0]/w,b[1]/h,b[2]/w,b[3]/h] for b in accepted],
                    plume_extent_polygons=polygons,source_origin_polygon=None,asset_id=None,
                    mask_pixels=int(mask.sum()),latency_ms=elapsed,background_frames=self.background_frames,
                    difference_gain=gain),mask,diff

    def metadata(self):
        import torch,transformers,cv2
        from huggingface_hub import hf_hub_download
        def model_record(id,revision):
            path=Path(hf_hub_download(id,'model.safetensors',revision=revision,local_files_only=True))
            return {'id':id,'revision':revision,'license':'Apache-2.0','weights_sha256':file_sha256(path),'weights_bytes':path.stat().st_size}
        return dict(method=METHOD,implementation_sha256=self.implementation_sha256,min_alert_mask_pixels=4,owl=model_record(OWL_ID,OWL_REV),
                    sam=model_record(SAM_ID,SAM_REV),
                    device=self.device,hardware='local workstation; not Dell/NVIDIA deployment',
                    pretrained=True,training_performed=False,threshold=self.threshold,
                    threshold_source=('fixed upstream owl_gasvid.py threshold; not fitted to our footage' if self.threshold==.06 else 'fixed LangGas SimGas reference default; not fitted to evaluation recordings' if self.threshold==.12 else 'explicit run configuration; no accuracy claim implied'),
                    temporal_filter=self.temporal,model_load_seconds=self.load_seconds,
                    versions={'torch':torch.__version__,'transformers':transformers.__version__,'opencv':cv2.__version__},
                    differences_from_upstream=['float32 local inference, no CUDA/bitsandbytes requirement',
                    'Hugging Face SAM2 implementation and causal positive-box matching',
                    'bounded prompt boxes; raw masks saved before visual labels',
                    'sample interval and temporal context recorded per run; not a claimed paper reproduction'],
                    limitations=['Not a methane-specific trained detector or calibrated gas classifier',
                    'A visual plume candidate cannot establish gas identity, exact equipment or kg/h',
                    'Masks are predictions, not annotation ground truth'])
