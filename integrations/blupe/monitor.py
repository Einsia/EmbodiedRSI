"""Loopback UI for one frozen-knowledge run: camera, status, trace and Stop."""

import argparse
import json
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from integrations.blupe.upstream import CHECKOUT
from orchestrator.files import write_json

PAGE = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EmbodiedEvo · YAM 1</title>
<style>
:root{color-scheme:dark;font:15px system-ui;background:#0d121a;color:#e7edf7}body{max-width:1500px;margin:28px auto;padding:0 24px}header{display:flex;justify-content:space-between;align-items:center;gap:24px}h1{font-size:26px;margin:0}p{color:#a6b5ca;line-height:1.6}.badge{border:1px solid #34516c;border-radius:20px;padding:6px 14px;color:#8cd9c6}button{background:#a6283d;color:white;border:0;border-radius:8px;padding:12px 20px;cursor:pointer}button:disabled{opacity:.4;cursor:default}.grid{display:grid;grid-template-columns:1.5fr 1fr;gap:18px;margin-top:20px}.panel{background:#141d29;border:1px solid #253245;border-radius:12px;padding:18px;min-width:0}h2{margin:0 0 12px;font-size:17px}video{width:100%;background:#05070a;min-height:260px}pre{white-space:pre-wrap;word-break:break-word;font:13px/1.6 ui-monospace,monospace;max-height:460px;overflow:auto;margin:0}.metrics{display:flex;gap:30px;flex-wrap:wrap;margin:14px 0}.metrics strong{display:block;font-size:21px}.muted{color:#99a9bf;font-size:13px}#task{margin-bottom:0}a{color:#84c5ff}#videoStatus{font-size:13px;color:#a6b5ca}details{margin-top:16px}summary{cursor:pointer}#error{color:#ffb1bd}@media(max-width:900px){.grid{grid-template-columns:1fr}}
</style>
<header><div><h1>EmbodiedEvo · YAM 1</h1><p>冻结技能迁移 / stack_blocks</p></div><span class="badge" id="state">等待启动</span><button id="stop">停止本次运行</button></header>
<div class="panel"><div class="metrics"><div><span class="muted">模型</span><strong id="model">—</strong></div><div><span class="muted">已用时间</span><strong id="elapsed">—</strong></div><div><span class="muted">已提交动作包</span><strong id="packets">0</strong></div><div><span class="muted">轨迹步数</span><strong id="steps">0</strong></div></div><p id="task"></p><p id="error"></p><span class="muted" id="session"></span></div>
<div class="grid"><section class="panel"><h2>真机画面与录像</h2><video id="video" autoplay muted playsinline controls></video><p id="videoStatus">连接四视角视频流；直播存在传输延迟。</p><p id="recording" class="muted">正在等待录像开始。</p></section><section class="panel"><h2>Agent 实时输出</h2><pre id="log">等待 agent 启动…</pre></section></div>
<div class="panel" style="margin-top:18px"><h2>Agent trace</h2><p class="muted">保留 Codex 原始事件、工具调用和返回结果；同时保存每次提交的代码与机器人反馈。</p><pre id="trace">等待 trace…</pre><details><summary>运行记录</summary><pre id="summary"></pre></details></div>
<script src="/hls.js"></script><script>
const $=id=>document.getElementById(id);const TOKEN='__TOKEN__';let lastLog='',lastTrace='';
async function refresh(){try{const d=await(await fetch('/api/state')).json();const s=d.controller||{};const c=d.config||{};$('state').textContent=s.status||'准备中';$('model').textContent=(c.model||'—')+' / '+(c.reasoning_effort||'');$('task').textContent=c.instruction||'正在校验技能、模型和真机连接。';$('elapsed').textContent=s.run_elapsed_s==null?'—':Math.round(s.run_elapsed_s)+' s';$('packets').textContent=s.packets_submitted||0;$('steps').textContent=s.next_step_id||0;$('error').textContent=s.error||d.failure?.error||'';$('session').textContent='Session: '+(s.session_id||'尚未入队');$('stop').disabled=!['queued','preparing','running'].includes(s.status);$('recording').textContent=d.video_result?'录像已保存 · '+(d.video_result.bytes/1048576).toFixed(1)+' MB':d.video_capture?'正在录制真机同步视频':'正在等待录像开始';$('summary').textContent=JSON.stringify({deployment:d.deployment,knowledge:d.knowledge,video:d.video_result||d.video_capture,parking:d.parking},null,2);if(d.log!==lastLog){$('log').textContent=d.log||'等待 agent 启动…';$('log').scrollTop=$('log').scrollHeight;lastLog=d.log}if(d.trace!==lastTrace){$('trace').textContent=d.trace||'等待 trace…';$('trace').scrollTop=$('trace').scrollHeight;lastTrace=d.trace}}catch(e){$('error').textContent='监控连接中断：'+e.message}}
$('stop').onclick=async()=>{const r=await fetch('/api/stop',{method:'POST',headers:{'X-Run-Token':TOKEN,'Content-Type':'application/json'},body:'{}'});$('error').textContent=r.ok?'已请求停止，等待机器人确认。':'停止请求未接受，请检查运行状态。';await refresh()};
const video=$('video'),source='https://playground.blupe.io/synchronized-hd/index.m3u8';if(window.Hls?.isSupported()){const hls=new Hls({enableWorker:false,lowLatencyMode:false});hls.loadSource(source);hls.attachMedia(video);hls.on(Hls.Events.MANIFEST_PARSED,()=>video.play().catch(()=>{}));hls.on(Hls.Events.ERROR,(_,d)=>{if(d.fatal)$('videoStatus').textContent='浏览器直播暂不可用；本地录像状态请见下方。'})}else if(video.canPlayType('application/vnd.apple.mpegurl'))video.src=source;else $('videoStatus').textContent='此浏览器不支持 HLS，真机录像仍单独保存。';refresh();setInterval(refresh,1500);
</script></html>'''


def tail(path, limit=50000):
    if not path.is_file():
        return ""
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size-limit))
        return stream.read(limit).decode("utf-8", errors="replace")


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def handler_for(run, token):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, code, body, kind="application/json"):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self' https://playground.blupe.io; media-src 'self' blob: https://playground.blupe.io; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.headers.get("Host") not in {f"127.0.0.1:{self.server.server_port}",f"localhost:{self.server.server_port}"}:
                return self.reply(403, {"error":"local host required"})
            if self.path == "/":
                return self.reply(200,PAGE.replace('__TOKEN__',token).encode(),"text/html; charset=utf-8")
            if self.path == "/hls.js":
                return self.reply(200,(CHECKOUT/'codex-runner/static/hls.light.min.js').read_bytes(),"application/javascript")
            if self.path == "/api/state":
                traces=sorted((run/'agent/rollouts').rglob('*.jsonl'))
                knowledge=read_json(run/'knowledge.json') or {}
                parking=read_json(run/'parking-final.json') or {}
                return self.reply(200,{
                    "controller":read_json(run/'controller-status.json'),
                    "config":read_json(run/'deployment-config.json'),
                    "failure":read_json(run/'failure.json'),
                    "deployment":read_json(run/'deployment.json'),
                    "knowledge":{"tasks":knowledge.get('tasks'),"counts":knowledge.get('counts')},
                    "video_capture":read_json(run/'video/capture.json'),
                    "video_result":read_json(run/'video/result.json'),
                    "parking":(parking.get('observation') or {}).get('mode'),
                    "log":tail(run/'agent.log'),
                    "trace":tail(traces[-1]) if traces else '',
                })
            return self.reply(404,{"error":"not found"})

        def do_POST(self):
            expected={f"http://127.0.0.1:{self.server.server_port}",f"http://localhost:{self.server.server_port}"}
            if (self.path!='/api/stop' or self.headers.get('X-Run-Token')!=token
                    or self.headers.get('Origin') not in expected):
                return self.reply(403,{"error":"same-origin run token required"})
            state=read_json(run/'controller-status.json') or {}
            if state.get('status') not in {'queued','preparing','running'}:
                return self.reply(409,{"error":"no active run"})
            write_json(run/'stop-request.json',{"reason":"user_requested","source":"local_ui"})
            return self.reply(202,{"status":"stop_requested"})

        def log_message(self,*args):
            pass
    return Handler


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--port',type=int,default=8788)
    args=p.parse_args()
    run=args.run_dir.resolve()
    if not re.match(r'^\d{12}(?:_|$)',run.name):
        raise ValueError('run directory needs a YYYYMMDDHHMM prefix')
    # It may start before the runner creates the new workspace; never create it here.
    server=ThreadingHTTPServer(('127.0.0.1',args.port),handler_for(run,secrets.token_urlsafe(32)))
    print(json.dumps({'ui':f'http://127.0.0.1:{args.port}/','run_dir':str(run)}),flush=True)
    server.serve_forever()


if __name__=='__main__':
    main()
