#!/usr/bin/env python3
"""猫耳直播录音 demo，Python 3.9+，仅依赖 FFmpeg。"""
import argparse
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path


def room_info(room_id):
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': f'https://fm.missevan.com/live/{room_id}'}
    cookie = os.environ.get('MISSEVAN_COOKIE')
    if cookie:
        headers['Cookie'] = cookie
    req = urllib.request.Request(f'https://fm.missevan.com/api/v2/live/{room_id}', headers=headers)
    with urllib.request.urlopen(req, timeout=20) as response:
        data = json.load(response)
    if data.get('code') != 0 or not isinstance(data.get('info', {}).get('room'), dict):
        raise RuntimeError(f"直播间接口失败，code={data.get('code')}")
    return data['info']['room']


def summary(room):
    return {'room_id': room['room_id'], '主播': room.get('creator_username'),
            '标题': room.get('name'), '开播': bool(room.get('status', {}).get('open')),
            '正在推流': room.get('status', {}).get('broadcasting'),
            '可用流': [k for k, v in room.get('channel', {}).items() if v and k.endswith('_pull_url')]}


def stop(proc):
    if proc.poll() is None:
        try:
            proc.communicate(input='q\n', timeout=10)
        except (subprocess.TimeoutExpired, BrokenPipeError):
            proc.kill()
            proc.wait()


def convert_closed_segments(folder, attempted, all_closed=False):
    """只处理已关闭的分段；失败时保留原始录音。"""
    sources = sorted(folder.glob('part_*.mka'))
    candidates = sources if all_closed else sources[:-1]
    for source in candidates:
        if source.name in attempted:
            continue
        attempted.add(source.name)
        target = source.with_suffix('.m4a')
        temporary = source.with_suffix('.partial.m4a')
        try:
            result = subprocess.run(
                ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                 '-i', str(source), '-map', '0:a:0', '-c:a', 'copy',
                 '-movflags', '+faststart', str(temporary)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
            if result.returncode or not temporary.exists() or temporary.stat().st_size == 0:
                raise RuntimeError('M4A 封装失败')
            temporary.replace(target)
            source.unlink()
            print(f'已自动转换：{target.name}', flush=True)
        except (OSError, subprocess.TimeoutExpired, RuntimeError):
            temporary.unlink(missing_ok=True)
            print(f'转换失败，保留原文件：{source.name}', flush=True)


def record(room, args):
    channel = room.get('channel', {})
    url = channel.get(f'{args.stream}_pull_url')
    if not url:
        raise RuntimeError(f'当前没有 {args.stream} 播放地址')
    if url.startswith('http://'):
        url = 'https://' + url[7:]
    root = Path(args.output) / str(room['room_id'])
    # 每次重连用独立目录，绝不覆盖之前的音频。
    folder = root / datetime.now().astimezone().strftime('%Y%m%d_%H%M%S_%f')
    folder.mkdir(parents=True)
    metadata = {'started_at': datetime.now().astimezone().isoformat(),
                'room': summary(room), 'source_open_time': room.get('status', {}).get('open_time'),
                'note': '网络中断可能产生缺口；分段连续性未经保证。'}
    log = (folder / 'ffmpeg.log').open('w', encoding='utf-8')
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'warning', '-rw_timeout', '15000000',
           '-user_agent', 'Mozilla/5.0', '-referer', f"https://fm.missevan.com/live/{room['room_id']}"]
    cookie = os.environ.get('MISSEVAN_COOKIE')
    if cookie:
        cmd += ['-headers', f'Cookie: {cookie}\r\n']
    cmd += ['-i', url]
    if args.seconds:
        cmd += ['-t', str(args.seconds)]
    cmd += ['-map', '0:a:0', '-vn', '-c:a', 'copy', '-f', 'segment',
            '-segment_time', str(args.segment), '-reset_timestamps', '1',
            '-segment_format', 'matroska', str(folder / 'part_%05d.mka')]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log, text=True)
    print(f'录音开始：{folder}', flush=True)
    last_bytes, last_growth, last_check, offline_count = 0, time.monotonic(), time.monotonic(), 0
    reason = 'ffmpeg_exit'
    attempted = set()
    try:
        while proc.poll() is None:
            time.sleep(1)
            now = time.monotonic()
            size = sum(p.stat().st_size for p in folder.iterdir() if p.suffix in ('.mka', '.m4a'))
            if size > last_bytes:
                last_bytes, last_growth = size, now
            convert_closed_segments(folder, attempted)
            if now - last_growth > 60:
                reason = 'no_data_60s'
                break
            if now - last_check >= 30:
                last_check = now
                try:
                    latest = room_info(room['room_id'])
                    offline_count = offline_count + 1 if not latest.get('status', {}).get('open') else 0
                    if offline_count >= 3:
                        reason = 'offline_confirmed'
                        break
                except Exception as exc:
                    print(f'状态查询暂时失败：{type(exc).__name__}', flush=True)
    except KeyboardInterrupt:
        reason = 'user_stop'
        raise
    finally:
        stop(proc)
        log.close()
        convert_closed_segments(folder, attempted, all_closed=True)
        metadata.update(ended_at=datetime.now().astimezone().isoformat(),
                        stop_reason=reason, returncode=proc.returncode,
                        files=[p.name for p in sorted(folder.iterdir()) if p.suffix in ('.mka', '.m4a')])
        (folder / 'manifest.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    if proc.returncode != 0 or not any(p.stat().st_size > 0 for p in folder.iterdir() if p.suffix in ('.mka', '.m4a')):
        raise RuntimeError(f'录音失败，请查看 {folder / "ffmpeg.log"}')
    print(f'录音结束：{folder}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('room', help='房间 ID 或直播链接')
    parser.add_argument('--check', action='store_true', help='只检查当前状态')
    parser.add_argument('--watch', action='store_true', help='持续监测，开播后自动录音')
    parser.add_argument('--seconds', type=int, default=0, help='单次限时录音；0 表示直到下播')
    parser.add_argument('--interval', type=int, default=45, help='未开播和重试检查间隔')
    parser.add_argument('--segment', type=int, default=300, help='每段秒数')
    parser.add_argument('--stream', choices=['flv', 'hls'], default='flv')
    parser.add_argument('--output', default='recordings')
    args = parser.parse_args()
    match = re.fullmatch(r'\d+', args.room) or re.search(r'/live/(\d+)', args.room)
    if not match:
        parser.error('请输入数字房间 ID 或 /live/数字 链接')
    room_id = match.group(1) if match.lastindex else match.group()
    if args.seconds < 0 or args.interval < 5 or args.segment < 1:
        parser.error('seconds >= 0，interval >= 5，segment >= 1')
    if args.watch and args.seconds:
        parser.error('--watch 不与 --seconds 同时使用；试录请单独使用 --seconds')
    if not args.check and not shutil.which('ffmpeg'):
        parser.error('请先安装 FFmpeg 并加入 PATH')
    try:
        while True:
            try:
                room = room_info(room_id)
                print(json.dumps(summary(room), ensure_ascii=False), flush=True)
                if args.check:
                    return
                if room.get('status', {}).get('open'):
                    record(room, args)
                elif not args.watch:
                    print('当前未开播；用 --watch 自动等待开播。')
                if not args.watch:
                    return
            except Exception as exc:
                # 不输出请求对象/播放 URL，避免泄露 cookie 和签名。
                print(f'错误：{type(exc).__name__}: {exc}' if isinstance(exc, RuntimeError)
                      else f'网络或文件错误：{type(exc).__name__}', flush=True)
                if not args.watch:
                    raise SystemExit(1)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print('\n已停止。')


if __name__ == '__main__':
    main()
