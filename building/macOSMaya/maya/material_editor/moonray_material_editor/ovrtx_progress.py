"""Progress from renderer operations and completed accumulation frames, never elapsed-time estimates."""
import math
import time


class RenderProgress:
    def __init__(self, settings, count, step=5, emit=print, clock=time.monotonic):
        self.settings, self.count, self.step = settings, count, step
        self.emit, self.clock = emit, clock
        self.started = clock()
        self.last_log = -math.inf
        self.last_percent = 0
        self.index = 0
        self.pt = settings["mode"] == "PathTracing"
        self.mode = "Path tracing" if self.pt else "Real-time path tracing"
        target = f"target {settings['samples_per_pixel']} samples/pixel" if self.pt else f"{count} accumulation frames"
        self.emit(f"ovRTX: {self.mode} · {target}")
        self.emit("Render progress: busy")

    def percent(self, value, force=False):
        value = min(99, int(value))
        if value > self.last_percent and (force or value >= self.last_percent + self.step):
            self.emit(f"Render progress: {value}%")
            self.last_percent = value

    def status(self, status):
        # This SDK may return PENDING/0 with no counters throughout a PT step.
        # Leave the indicator indeterminate until there is measurable progress.
        progress = status.progress
        known = math.isfinite(progress) and 0 < progress <= 1
        if known:
            self.percent((self.index + progress) * 100 / self.count)
        now = self.clock()
        if now - self.last_log < 5:
            return
        self.last_log = now
        counters = ", ".join(f"{c.name}: {c.current}/{c.total or '?'}" for c in status.counters)
        detail = f"renderer operation {progress:.0%}" if known else (
            "sample percentage unavailable" if self.pt else f"accumulating frame {self.index + 1}/{self.count}")
        self.emit(f"ovRTX: {self.mode} · {now - self.started:.1f}s elapsed · {detail}" + (" · " + counters if counters else ""))

    def completed_frame(self):
        self.index += 1
        old = self.last_percent
        self.percent(self.index * 100 / self.count, force=self.index == self.count)
        if old != self.last_percent:
            self.emit(f"ovRTX: {self.mode} · {self.index}/{self.count} "
                      + ("render steps" if self.pt else "accumulation frames")
                      + f" complete · {self.clock() - self.started:.1f}s elapsed")
