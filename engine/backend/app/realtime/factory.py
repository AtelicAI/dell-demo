"""Lazy model construction for a dedicated worker, never the application server."""
from .engine import Pipeline,PipelineConfig


def build(architecture,repo,output,*,device='cuda',prompt='gas plume',video=True,refresh=1.,temporal_guard=False):
    from .models import LeakHunterDetector,OwlDetector,SamImage,SamVideo
    segmenter=tracker=None
    if architecture!='leakhunter':
        if video:tracker=SamVideo(device=device)
        else:segmenter=SamImage(device=device)
    # Restricted LeakHunter loading disables networking process-wide. HF weights are
    # deliberately loaded first and all downloads remain forbidden.
    detector=(OwlDetector(prompt=prompt,device=device) if architecture=='owl-sam'
              else LeakHunterDetector(repo,output,device=device))
    pipeline=Pipeline(detector,segmenter,tracker,PipelineConfig(architecture=architecture,
                 refresh_seconds=refresh,video=video,temporal_guard=temporal_guard))
    metadata=dict(detector=detector.metadata(),sam=(tracker or segmenter).metadata() if tracker or segmenter else None,
                  pipeline=pipeline.metadata())
    return pipeline,metadata
