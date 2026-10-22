"""Capture the station's native synchronized HLS video independently of policy steps."""

import signal
import subprocess
import time
from pathlib import Path

from orchestrator.files import write_json


class VideoRecorder:
    def __init__(self, run, url):
        import imageio_ffmpeg

        self.directory = Path(run) / "video"
        self.directory.mkdir()
        self.output = self.directory / "synchronized.mp4"
        self.progress = self.directory / "progress.txt"
        self.ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        self.url = url
        self.process = self.log = None
        self.started_at = None

    def start(self):
        self.log = (self.directory / "ffmpeg.log").open("wb")
        self.started_at = time.time()
        self.process = subprocess.Popen([
            self.ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostdin",
            "-rw_timeout", "15000000", "-i", self.url, "-map", "0:v:0",
            "-an", "-c:v", "copy", "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
            "-progress", str(self.progress), str(self.output),
        ], stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
        write_json(self.directory / "capture.json", {
            "source": self.url, "started_at": self.started_at,
            "method": "Native HLS video, remuxed without image synthesis or frame interpolation",
            "source_may_be_delayed": True, "file": self.output.name,
        })
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("Video recorder exited before robot admission; see video/ffmpeg.log")
            if self.output.exists() and self.output.stat().st_size > 16000:
                return
            time.sleep(0.2)
        raise RuntimeError("Video capture did not become ready; no robot session should be started")

    def check(self):
        if self.process.poll() is not None:
            raise RuntimeError("Video capture stopped during the run; stopping robot execution")
        if self.progress.exists() and time.time() - self.progress.stat().st_mtime > 25:
            raise RuntimeError("Video capture stalled; stopping robot execution")

    def close(self):
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.log:
            self.log.close()
        write_json(self.directory / "result.json", {
            "started_at": self.started_at, "ended_at": time.time(),
            "exit_code": self.process.returncode,
            "bytes": self.output.stat().st_size if self.output.exists() else 0,
            "file": self.output.name,
        })
