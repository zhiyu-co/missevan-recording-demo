# 猫耳直播录播 demo

需要 Python 3.9+ 和 FFmpeg（加入 PATH），无需 pip 依赖。解压后在本目录运行。

## 网页控制台

运行下面的命令会自动打开本机网页，可检查直播状态、开始或停止录音并查看实时日志：

```bash
python web.py
```

默认地址为 `http://127.0.0.1:8765`。网页只监听本机，不会把 Cookie 或录音控制暴露到局域网。

以下命令中的 `ROOM_ID` 请替换为要录制的房间 ID；程序不内置任何默认房间。

```bash
# 查看当前状态，不录音
python record.py ROOM_ID --check

# 录 30 秒，输出到 recordings
python record.py ROOM_ID --seconds 30

# 常驻等待开播，自动录音；Ctrl+C 停止
python record.py ROOM_ID --watch

# 指定其他房间，每 10 分钟分段
python record.py ROOM_ID --watch --segment 600

# 如果 FLV 失败，尝试 HLS
python record.py ROOM_ID --seconds 30 --stream hls
```

Windows 若没有 `python` 命令，可用 `py`；Linux/macOS 可用 `python3`。
Ubuntu/Debian 可用 `sudo apt install ffmpeg` 安装 FFmpeg。

## 行为

- 默认每 45 秒检查开播，默认每 300 秒保存一段音频，并自动转换为 M4A。
- 直接复制源音频，不重新编码，不需要 GPU。每段关闭后自动无损封装为 M4A；转换成功才删除 MKA，失败则保留原文件。最后一段在下播、限时结束或 Ctrl+C 后转换。
- 每次连接有独立目录，包含分段、ffmpeg.log 和 manifest.json。
- FFmpeg 退出或 60 秒文件无增长后，watch 模式重新查询、取流和启动录音。
- 连续三次状态检查确认下播后停止本次连接。
- Ctrl+C 尝试正常关闭 FFmpeg，保存容器尾部和清单。
- 默认 FLV，可手动切换 HLS。文件可用 VLC 播放。

正常录音无需手动转换。若自动转换失败，可手动尝试（源编码为 AAC 时）：

```bash
ffmpeg -i part_00000.mka -c:a copy recording.m4a
```
