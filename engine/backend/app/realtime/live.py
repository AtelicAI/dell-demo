"""Read-only loopback MJPEG preview, independent of the application frontend."""
import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import threading
import time
import numpy as np
from .artifacts import clean


class LiveView:
    def __init__(self,max_age=.5):
        self.lock=threading.Lock();self.latest=None;self.max_age=max_age;self.closed=False;self.stats={}

    def publish(self,frame,result,overlay):
        import cv2
        started=time.perf_counter()
        ok,encoded=cv2.imencode('.jpg',overlay[:,:,::-1],[cv2.IMWRITE_JPEG_QUALITY,88])
        if not ok:raise RuntimeError('Overlay JPEG encoding failed')
        encoded=encoded.tobytes()
        result['timings_ms']['encoding']=1000*(time.perf_counter()-started)
        result['timings_ms']['capture_to_overlay_available']=1000*(time.perf_counter()-frame.captured_at)
        result['live_display_eligible']=time.perf_counter()-frame.captured_at<=self.max_age
        with self.lock:self.latest=(frame,result,encoded)

    def snapshot(self):
        import cv2
        with self.lock:
            if self.latest is None:return dict(status='uninitialized',mask_age_ms=None,**self.stats),None,None
            frame,result,encoded=self.latest
            age=max(0,time.perf_counter()-frame.captured_at)
            row=clean(result);row.update(mask_age_ms=age*1000,**self.stats)
            mask=result.get('mask')
            if age>self.max_age:
                row.update(status='stale',reason='latest_frame_exceeded_live_age_budget')
                ok,raw=cv2.imencode('.jpg',frame.rgb[:,:,::-1])
                encoded=raw.tobytes() if ok else None;mask=None
            return row,encoded,mask


def serve(view,port=8091):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            if self.path=='/':
                body=b'<!doctype html><title>OGI inference preview</title><style>body{background:#101827;color:#eee;font:16px system-ui;margin:24px}img{max-width:95vw;image-rendering:auto}pre{white-space:pre-wrap}</style><h2>OGI inference preview</h2><p>Experimental model plume segmentation. No methane concentration or chemical identity is measured.</p><img src="/mjpeg"><pre id="status"></pre><script>setInterval(async()=>{document.getElementById("status").textContent=JSON.stringify(await(await fetch("/status")).json(),null,2)},500)</script>'
                self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(body)
            elif self.path=='/status':
                status,_,_=view.snapshot();self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps(status,allow_nan=False).encode())
            elif self.path=='/raw-mask.png':
                import cv2
                status,_,mask=view.snapshot()
                if mask is None:self.send_error(410,'No current raw segmentation');return
                ok,data=cv2.imencode('.png',mask.astype(np.uint8))
                self.send_response(200);self.send_header('Content-Type','image/png');self.end_headers();self.wfile.write(data.tobytes())
            elif self.path=='/mjpeg':
                self.send_response(200);self.send_header('Content-Type','multipart/x-mixed-replace; boundary=ogi');self.send_header('Cache-Control','no-store');self.end_headers()
                try:
                    next_tick=time.perf_counter()
                    while not view.closed:
                        status,jpeg,_=view.snapshot()
                        if jpeg:
                            headers=(f'--ogi\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n'
                                     f'X-Frame-Id: {status.get("frame_id")}\r\nX-Prediction-Age-Ms: {status.get("mask_age_ms")}\r\n'
                                     f'X-Inference-State: {status["status"]}\r\n\r\n').encode()
                            self.wfile.write(headers+jpeg+b'\r\n');self.wfile.flush()
                        next_tick+=1/15
                        time.sleep(max(0,next_tick-time.perf_counter()))  # IDs expose every display repeat.
                except (BrokenPipeError,ConnectionResetError):pass
            else:self.send_error(404)
    class Server(ThreadingHTTPServer):
        daemon_threads=True;allow_reuse_address=True
    server=Server(('127.0.0.1',port),Handler)
    threading.Thread(target=server.serve_forever,daemon=True,name='ogi-preview').start()
    return server
