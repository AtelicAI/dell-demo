"""Lossless raw prediction artifacts, separate from disposable display pixels."""
import hashlib
import json
import time
from pathlib import Path
import numpy as np
from PIL import Image


def clean(value):
    if isinstance(value,np.ndarray):raise TypeError('Array must be saved as an explicit artifact, not JSON')
    if isinstance(value,np.generic):return value.item()
    if isinstance(value,dict):return {k:clean(v) for k,v in value.items() if not k.startswith('_') and k not in {'mask','logits','native_logits','sam_mask','detector_mask','validated_mask','temporal_candidate_mask'}}
    if isinstance(value,(list,tuple)):return [clean(v) for v in value]
    return value


def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


class Artifacts:
    def __init__(self,root,*,compression='compressed'):
        if compression not in {'compressed','stored'}:
            raise ValueError('Raw archive compression must be compressed or stored')
        self.compression=compression
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=False)
        (self.root/'raw').mkdir();self.count=0
        self.log=(self.root/'frames.jsonl').open('x')

    def write(self,frame,result,*,overlay=None):
        started=time.perf_counter()
        identity=f'{self.count:06d}';self.count+=1
        record=clean(result)
        record.update(input_rgb_sha256=hashlib.sha256(frame.rgb.tobytes()).hexdigest(),decode_ms=frame.decode_ms)
        arrays={}
        for key in ('mask','detector_mask','sam_mask','validated_mask','temporal_candidate_mask'):
            value=result.get(key)
            if value is not None:
                if value.dtype!=np.bool_ or value.shape!=frame.rgb.shape[:2]:raise ValueError('Raw mask geometry mismatch')
                arrays[key+'_bits']=np.packbits(value.reshape(-1));arrays[key+'_shape']=np.asarray(value.shape,dtype=np.int32)
        native=result.get('native_logits')
        if native is not None:
            if not np.isfinite(native).all():raise ValueError('Nonfinite native model logits')
            arrays['native_logits']=np.asarray(native,dtype=np.float32)
        if arrays:
            path=self.root/'raw'/(identity+'.npz')
            save=np.savez_compressed if self.compression=='compressed' else np.savez
            save(path,**arrays)
            record.update(raw_file=str(path.relative_to(self.root)),raw_sha256=sha(path),
                raw_compression=self.compression,
                score_policy='Exact float32 native decoder logits when exposed; full-source binary masks lossless packed. Full-source interpolated logits are derived and not retained.')
        else:record.update(raw_file=None,raw_sha256=None)
        if overlay is not None:
            folder=self.root/'display';folder.mkdir(exist_ok=True)
            path=folder/(identity+'.jpg');Image.fromarray(overlay).save(path,quality=92)
            record['display_file']=str(path.relative_to(self.root));record['display_sha256']=sha(path)
            comparison=folder/(identity+'-comparison.jpg')
            Image.fromarray(np.concatenate([frame.rgb,overlay],axis=1)).save(comparison,quality=92)
            record['comparison_file']=str(comparison.relative_to(self.root));record['comparison_sha256']=sha(comparison)
        record.setdefault('timings_ms',{})['artifact_persistence']=1000*(time.perf_counter()-started)
        self.log.write(json.dumps(record,allow_nan=False)+'\n');self.log.flush()
        return record

    def close(self):self.log.close()


def read_mask(root,record,key='mask'):
    if record.get('raw_file') is None:return None
    path=Path(root)/record['raw_file']
    if sha(path)!=record['raw_sha256']:raise ValueError('Raw artifact hash mismatch')
    with np.load(path,allow_pickle=False) as f:
        if key+'_bits' not in f:return None
        shape=tuple(int(x) for x in f[key+'_shape'])
        return np.unpackbits(f[key+'_bits'],count=int(np.prod(shape))).reshape(shape).astype(bool)
