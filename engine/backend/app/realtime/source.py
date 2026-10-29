"""Bounded acquisition with source PTS, explicit drops and camera reconnects."""
from collections import deque
from pathlib import Path
import threading
import time
import numpy as np
from .contracts import Frame


class LatestFrames:
    def __init__(self,capacity=2):
        if not 1<=capacity<=8:raise ValueError('Queue capacity must be 1..8')
        self.capacity=capacity;self.queue=deque();self.condition=threading.Condition()
        self.closed=False;self.acquired=0;self.dropped=0;self.recent_drops=deque(maxlen=64)
        self.state='uninitialized';self.error=None;self.reconnects=0

    def put(self,frame):
        with self.condition:
            if self.closed:return
            self.acquired+=1
            while len(self.queue)>=self.capacity:
                old=self.queue.popleft();self.dropped+=1;self.recent_drops.append([old.epoch,old.frame_id])
            self.queue.append(frame);self.state='connected';self.error=None;self.condition.notify()

    def get(self,timeout=.25):
        with self.condition:
            if not self.queue and not self.closed:self.condition.wait(timeout)
            return self.queue.popleft() if self.queue else None

    def close(self):
        with self.condition:self.closed=True;self.condition.notify_all()

    def stats(self):
        with self.condition:
            return dict(acquired=self.acquired,dropped=self.dropped,queued=len(self.queue),queue_capacity=self.capacity,
                        recent_dropped_source_ids=list(self.recent_drops),state=self.state,error=self.error,reconnects=self.reconnects)


class Capture:
    def __init__(self,source,queue,*,start=0.,end=None,paced=True,reconnect=True,clock=time.perf_counter):
        self.source,self.queue,self.start,self.end=source,queue,start,end
        self.paced,self.reconnect,self.clock=paced,reconnect,clock
        self.stop_event=threading.Event();self.thread=None;self.capture=None

    def start_thread(self):
        self.thread=threading.Thread(target=self.run,daemon=True,name='ogi-acquisition');self.thread.start();return self

    def stop(self):
        self.stop_event.set();self.queue.close()
        if self.thread:self.thread.join(timeout=2)

    def run(self):
        import cv2
        is_file=isinstance(self.source,(str,Path)) and Path(str(self.source)).is_file()
        epoch=0
        try:
            while not self.stop_event.is_set():
                source=int(self.source) if str(self.source).isdigit() else str(self.source)
                cap=cv2.VideoCapture()
                self.capture=cap
                # FFmpeg camera backends can honor these; device support is not assumed.
                cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,2000);cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC,2000)
                if not cap.open(source):
                    cap.release();self.capture=None
                    self.queue.state='disconnected';self.queue.error='camera_open_failed'
                    if is_file or not self.reconnect:break
                    self.stop_event.wait(1);epoch+=1;self.queue.reconnects+=1;continue
                first_pts=wall_start=last_pts=None;frame_id=-1
                while not self.stop_event.is_set():
                    begun=self.clock();ok,bgr=cap.read();decode_ms=1000*(self.clock()-begun)
                    if not ok:break
                    frame_id+=1;pts=float(cap.get(cv2.CAP_PROP_POS_MSEC))/1000
                    kind='source_pts'
                    if not is_file and (not np.isfinite(pts) or pts<0 or last_pts is not None and pts<=last_pts):
                        pts=self.clock();kind='arrival_clock_no_source_pts'
                    if not np.isfinite(pts) or pts<0 or last_pts is not None and pts<=last_pts:
                        raise ValueError('Nonmonotonic file timestamps; no FPS-based timestamp fabrication')
                    last_pts=pts
                    if is_file and pts<self.start:continue
                    if is_file and self.end is not None and pts>=self.end:break
                    if first_pts is None:first_pts=pts;wall_start=self.clock()
                    arrival=wall_start+(pts-first_pts) if is_file and self.paced else self.clock()
                    if is_file and self.paced:
                        if self.stop_event.wait(max(0,arrival-self.clock())):break
                    rgb=cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)
                    self.queue.put(Frame(frame_id,pts,rgb,arrival,epoch,kind,decode_ms))
                cap.release();self.capture=None
                if is_file or not self.reconnect:break
                self.queue.state='disconnected';self.queue.error='camera_read_failed'
                self.stop_event.wait(1);epoch+=1;self.queue.reconnects+=1
        except Exception as exc:
            self.queue.state='failed';self.queue.error=type(exc).__name__+': '+str(exc)
        finally:
            if self.capture is not None:self.capture.release()
            if self.queue.state=='connected':self.queue.state='eof' if is_file else 'disconnected'
            self.queue.close()
