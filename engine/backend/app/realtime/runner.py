"""Continuous acquisition/inference/render loop and transparent timing metrics."""
from collections import Counter,deque
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import numpy as np
from .source import Capture,LatestFrames
from .render import Renderer
from .artifacts import Artifacts,clean
from .live import LiveView,serve


def distribution(values):
    a=np.asarray(values,dtype=float)
    return dict(count=len(a),p50=float(np.median(a)) if len(a) else None,p95=float(np.percentile(a,95)) if len(a) else None,
                maximum=float(a.max()) if len(a) else None)


def run_stream(pipeline,source,output,*,start=0,end=None,duration=60,paced=True,port=None,display_mode='segmentation',device='cpu',raw_compression='compressed'):
    writer=Artifacts(output,compression=raw_compression);queue=LatestFrames(2);renderer=Renderer(display_mode);view=LiveView(pipeline.config.max_age_seconds)
    server=serve(view,port) if port is not None else None
    capture=Capture(source,queue,start=start,end=end,paced=paced)
    frames_processed=inferred=expired_at_publish=0;status_counts=Counter();timings={}
    started=time.perf_counter();capture.start_thread();status='completed';gpu_samples=[]
    stop_monitor=threading.Event()
    def gpu_monitor():
        while not stop_monitor.is_set():
            try:
                text=subprocess.check_output(['nvidia-smi','--query-gpu=utilization.gpu,memory.used','--format=csv,noheader,nounits'],text=True,timeout=2)
                gpu_samples.append([float(v) for v in text.splitlines()[0].split(',')])
                if len(gpu_samples)>3600:del gpu_samples[0]
            except (OSError,subprocess.SubprocessError,ValueError):pass
            stop_monitor.wait(1)
    if device=='cuda':
        import torch
        torch.cuda.reset_peak_memory_stats();threading.Thread(target=gpu_monitor,daemon=True).start()
    observer=None
    if server:
        # Fresh interpreter avoids LeakHunter's intentionally disabled Python networking.
        code="""import json,time,urllib.request,sys
time.sleep(1); start=time.perf_counter(); count=0; ids=set(); states=set()
try:
 with urllib.request.urlopen(sys.argv[1]+'/mjpeg',timeout=8) as r:
  while time.perf_counter()-start<3:
   line=r.readline()
   if line.startswith(b'X-Frame-Id:'):count+=1;ids.add(line.decode().strip())
   if line.startswith(b'X-Inference-State:'):states.add(line.decode().strip())
 print(json.dumps(dict(display_frames=count,distinct_frame_ids=len(ids),elapsed_seconds=time.perf_counter()-start,states=sorted(states))))
except Exception as e: print(json.dumps(dict(error=type(e).__name__)))
"""
        observer=subprocess.Popen([sys.executable,'-c',code,'http://127.0.0.1:'+str(server.server_port)],stdout=subprocess.PIPE,text=True)
    try:
        while duration<=0 or time.perf_counter()-started<duration:
            frame=queue.get()
            if frame is None:
                view.stats=queue.stats()
                if queue.closed:break
                continue
            t=time.perf_counter();result=pipeline.process(frame)
            result['timings_ms']['decoding']=frame.decode_ms
            t_render=time.perf_counter();overlay,display=renderer.render(frame,result)
            result['timings_ms']['rendering']=1000*(time.perf_counter()-t_render);result['display']=display
            view.stats=queue.stats();view.publish(frame,result,overlay)
            result['timings_ms']['consumer_before_persistence']=1000*(time.perf_counter()-t)
            record=writer.write(frame,result,overlay=overlay)
            frames_processed+=1;inferred+=int(result['new_mask_inference']);status_counts[result['status']]+=1
            expired_at_publish+=int(not result['live_display_eligible'])
            for key,value in record['timings_ms'].items():timings.setdefault(key,deque(maxlen=4096)).append(value)
    except KeyboardInterrupt:status='interrupted'
    finally:
        capture.stop();writer.close();view.closed=True;stop_monitor.set()
        if server:server.shutdown();server.server_close()
    elapsed=time.perf_counter()-started
    if not frames_processed or queue.state=='failed':status='failed'
    observation=None
    if observer:
        try:observation=json.loads(observer.communicate(timeout=5)[0])
        except (subprocess.SubprocessError,json.JSONDecodeError):observer.kill();observation=dict(error='observer_failed')
    report=dict(status=status,source_path=str(source),source_start=start,source_end=end,paced=paced,elapsed_seconds=elapsed,
        frames_processed=frames_processed,newly_inferred_masks=inferred,processed_fps=frames_processed/elapsed if elapsed else None,
        newly_inferred_masks_per_second=inferred/elapsed if elapsed else None,
        non_mask_inference_frames=frames_processed-inferred,acquisition=queue.stats(),
        expired_before_live_display=expired_at_publish,
        status_counts=dict(status_counts),timing_ms={k:distribution(v) for k,v in timings.items()},
        timing_distribution_scope='Last 4096 measured values per stage; all per-frame records remain on disk',
        metric_scope='Continuous live-paced prerecorded stream proxy; includes acquisition waits, inference, rendering, encoding, raw writes and queue overhead. Physical camera transport/exposure not measured.',
        capture_to_overlay_metric='Scheduled recorded-frame arrival to encoded overlay availability; excludes network/browser transport, includes queue age',
        display_observer=observation,gpu_samples=gpu_samples,
        gpu_peak_allocated_bytes=int(torch.cuda.max_memory_allocated()) if device=='cuda' else None,
        max_session_frames=90,max_session_source_seconds=6,queue_capacity=2,
        frame_records='frames.jsonl',raw_compression=raw_compression,models_loaded_once=True,training_performed=False,field_accuracy=None)
    # Keep acquisition stats distinct from source path.
    report['source_path']=str(source)
    (Path(output)/'summary.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    return report
