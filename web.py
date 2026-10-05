#!/usr/bin/env python3
"""猫耳录音本地网页控制台，仅监听本机地址。"""
import argparse
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import webbrowser
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / 'web'
TOKEN = secrets.token_urlsafe(24)
ROOM_PATTERN = re.compile(r'^(?:\d+|https?://fm\.missevan\.com/live/\d+)$')
LOGS = deque(maxlen=1200)
LOCK = threading.Lock()
STATE = {'process': None, 'started_at': None, 'returncode': None, 'room': None, 'seq': 0}


def add_log(message):
    with LOCK:
        STATE['seq'] += 1
        LOGS.append({'id': STATE['seq'], 'time': datetime.now().astimezone().strftime('%H:%M:%S'),
                     'text': message.rstrip()})


def read_process(proc):
    try:
        for line in proc.stdout:
            add_log(line)
    finally:
        returncode = proc.wait()
        with LOCK:
            if STATE['process'] is proc:
                STATE['process'] = None
                STATE['returncode'] = returncode
        add_log(f'任务已结束，退出码 {returncode}')


def snapshot(after=0):
    with LOCK:
        proc = STATE['process']
        return {'active': bool(proc and proc.poll() is None),
                'pid': proc.pid if proc and proc.poll() is None else None,
                'room': STATE['room'], 'started_at': STATE['started_at'],
                'returncode': STATE['returncode'],
                'logs': [item for item in LOGS if item['id'] > after]}


class Handler(BaseHTTPRequestHandler):
    server_version = 'MissevanLocal/1.0'

    def log_message(self, format_, *args):
        return

    def reply(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def serve_asset(self, name, content_type):
        path = WEB_ROOT / name
        if not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        if name == 'index.html':
            body = body.replace(b'__LOCAL_TOKEN__', TOKEN.encode())
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store' if name == 'index.html' else 'public, max-age=3600')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        return self.headers.get('X-Local-Token') == TOKEN

    def body_json(self):
        length = int(self.headers.get('Content-Length', '0'))
        if length > 16384:
            raise ValueError('请求内容过大')
        return json.loads(self.rfile.read(length) or b'{}')

    def do_GET(self):
        path = urlparse(self.path)
        if path.path == '/':
            return self.serve_asset('index.html', 'text/html; charset=utf-8')
        if path.path == '/app.css':
            return self.serve_asset('app.css', 'text/css; charset=utf-8')
        if path.path == '/app.js':
            return self.serve_asset('app.js', 'text/javascript; charset=utf-8')
        if path.path == '/api/state':
            if not self.authorized():
                return self.reply(403, {'error': '无效的本机会话'})
            try:
                after = int(dict(item.split('=', 1) for item in path.query.split('&') if '=' in item).get('after', 0))
            except ValueError:
                after = 0
            return self.reply(200, snapshot(after))
        self.send_error(404)

    def do_POST(self):
        if not self.authorized():
            return self.reply(403, {'error': '无效的本机会话'})
        try:
            payload = self.body_json()
            if self.path == '/api/check':
                return self.check_room(payload)
            if self.path == '/api/start':
                return self.start_recording(payload)
            if self.path == '/api/stop':
                return self.stop_recording()
            self.reply(404, {'error': '接口不存在'})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self.reply(400, {'error': str(exc)})

    @staticmethod
    def valid_room(value):
        room = str(value or '').strip()
        if not ROOM_PATTERN.fullmatch(room):
            raise ValueError('请输入数字房间 ID 或完整的猫耳直播链接')
        return room

    def check_room(self, payload):
        room = self.valid_room(payload.get('room'))
        try:
            result = subprocess.run([sys.executable, str(ROOT / 'record.py'), room, '--check'],
                                    cwd=ROOT, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            return self.reply(504, {'error': '状态检查超时，请稍后重试'})
        output = (result.stdout or result.stderr).strip()
        if result.returncode:
            return self.reply(502, {'error': output or '状态检查失败'})
        try:
            info = json.loads(output.splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            return self.reply(502, {'error': '状态返回格式异常'})
        self.reply(200, {'room': info})

    def start_recording(self, payload):
        room = self.valid_room(payload.get('room'))
        watch = bool(payload.get('watch'))
        seconds = int(payload.get('seconds', 0))
        interval = int(payload.get('interval', 45))
        segment = int(payload.get('segment', 300))
        stream = payload.get('stream', 'flv')
        if seconds < 0 or interval < 5 or segment < 1 or stream not in ('flv', 'hls'):
            raise ValueError('录音参数无效')
        if watch and seconds:
            raise ValueError('等待开播与限时录音不能同时启用')
        with LOCK:
            running = STATE['process']
            if running and running.poll() is None:
                return self.reply(409, {'error': '已有录音任务正在运行'})
            cmd = [sys.executable, str(ROOT / 'record.py'), room,
                   '--interval', str(interval), '--segment', str(segment), '--stream', stream]
            if watch:
                cmd.append('--watch')
            if seconds:
                cmd += ['--seconds', str(seconds)]
            proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, bufsize=1,
                                    start_new_session=True)
            STATE.update(process=proc, started_at=datetime.now().astimezone().isoformat(timespec='seconds'),
                         returncode=None, room=room)
        add_log(f'已启动录音任务（PID {proc.pid}）')
        threading.Thread(target=read_process, args=(proc,), daemon=True).start()
        self.reply(202, snapshot())

    def stop_recording(self):
        with LOCK:
            proc = STATE['process']
        if not proc or proc.poll() is not None:
            return self.reply(409, {'error': '当前没有运行中的任务'})
        try:
            os.killpg(proc.pid, signal.SIGINT)
            add_log('正在安全停止录音…')
        except ProcessLookupError:
            pass
        self.reply(202, {'ok': True})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if args.host not in ('127.0.0.1', 'localhost', '::1'):
        parser.error('为保护 Cookie 和录音控制，网页只允许监听本机地址')
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f'http://127.0.0.1:{args.port}'
    print(f'本地控制台：{url}', flush=True)
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
