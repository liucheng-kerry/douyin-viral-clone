#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
dub.py —— 给无声成片按时间码注入对白配音（可选叠加环境音），视频流不重编码。

背景：即梦 CLI 的 image2video / text2video / multimodal2video 均无音频参数，
生成的 mp4 没有音轨。因此对白必须在后期按台词时间码注入，这是本技能阶段 5.5 的核心。

用法：
  python dub.py --video 05-成片/clip_01.mp4 --script 05-配音/dialogue.json --out 05-配音/clip_01_dub.mp4
  python dub.py --video a.mp4 --script d.json --out a_dub.mp4 --ambience 环境音.wav --ambience-gain -22

关于 start 的含义：脚本默认会裁掉每句 TTS 的前置静音，所以 lines[].start 直接写
台词在画面里应该开口的那一秒即可（不需要人工补偿偏移）。

dialogue.json 结构：
{
  "voices": {"cat": "zh-CN-YunxiaNeural", "vendor": "zh-CN-YunjianNeural"},
  "default_voice": "zh-CN-YunxiaNeural",
  "lines": [
    {"start": 0.6, "text": "老板，今天要两条", "who": "cat", "rate": "+6%", "pitch": "+2Hz"},
    {"start": 2.5, "text": "今天这么大方?", "who": "vendor", "rate": "-4%"}
  ]
}

耗时提示：每句约 1-3 秒（联网合成），10 句以内通常 20 秒内完成。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile


def _runs(exe):
    """候选可执行文件必须真的能跑起来——WinGet 的 Links\ffmpeg.EXE 是 0 字节别名壳，
    shutil.which 会返回它，直接调用会报 WinError 193。"""
    try:
        if not exe or not os.path.exists(exe) or os.path.getsize(exe) == 0:
            return False
        res = subprocess.run([exe, "-version"], capture_output=True, timeout=20)
        return res.returncode == 0
    except Exception:
        return False


def find_ffmpeg():
    """优先用 PATH 里真正可执行的 ffmpeg，找不到就去 winget 安装目录逐个验证。"""
    for cand in (shutil.which("ffmpeg"), shutil.which("ffmpeg.exe")):
        if _runs(cand):
            return cand
    local = os.environ.get("LOCALAPPDATA", "")
    base = os.path.join(local, "Microsoft", "WinGet", "Packages")
    if os.path.isdir(base):
        for root, _dirs, files in os.walk(base):
            if "ffmpeg.exe" in files:
                cand = os.path.join(root, "ffmpeg.exe")
                if _runs(cand):
                    return cand
    return None


def tts_line(text, voice, out_path, rate=None, pitch=None, volume=None, timeout=90):
    # 注意：edge-tts 的 --rate/-pitch/-volume 用 "=" 连接，否则 "-6%" 会被 argparse
    # 当成选项名而报 "expected one argument"。
    cmd = [sys.executable, "-m", "edge_tts", f"--text={text}", f"--voice={voice}",
           f"--write-media={out_path}"]
    if rate:
        cmd.append(f"--rate={rate}")
    if pitch:
        cmd.append(f"--pitch={pitch}")
    if volume:
        cmd.append(f"--volume={volume}")
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    res = subprocess.run(cmd, capture_output=True, env=env, timeout=timeout)
    if res.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        err = (res.stderr or b"").decode("utf-8", "ignore")[-400:]
        raise RuntimeError(f"TTS 失败：{text[:20]}… {err}")
    return out_path


def trim_leading_silence(ff, src, dst, threshold="-45dB"):
    """裁掉 TTS 句首静音。

    edge-tts 每句自带的前置静音长度并不固定（实测 0.2s ~ 1s 都有），
    若不裁掉，"start 写多少就该在多少响"就不成立，长句会莫名晚半拍甚至跨过镜头切点。
    裁完之后 start 可以直接写台词表里的真实起点。
    """
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", src,
           "-af", f"silenceremove=start_periods=1:start_duration=0:start_threshold={threshold}:detection=peak,"
                  "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo",
           "-c:a", "pcm_s16le", dst]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0 or not os.path.exists(dst) or os.path.getsize(dst) == 0:
        return src  # 裁失败就用原文件，不阻断流程
    return dst


def main():
    ap = argparse.ArgumentParser(description="按时间码给无声视频注入对白配音")
    ap.add_argument("--video", required=True)
    ap.add_argument("--script", required=True, help="dialogue.json 路径")
    ap.add_argument("--out", required=True)
    ap.add_argument("--ambience", help="可选：环境音/BGM 铺底文件")
    ap.add_argument("--ambience-gain", type=float, default=-24.0, help="铺底音量 dB，默认 -24")
    ap.add_argument("--offset", type=float, default=0.0, help="全局时间偏移秒，默认 0")
    ap.add_argument("--voice-gain", type=float, default=0.0, help="人声音量 dB，默认 0")
    ap.add_argument("--keep-leading-silence", action="store_true",
                    help="保留 TTS 句首静音（默认自动裁掉，保证 start 就是真实发声时刻）")
    args = ap.parse_args()

    ff = find_ffmpeg()
    if not ff:
        sys.exit("找不到 ffmpeg，请先安装（winget install Gyan.FFmpeg）")
    if not os.path.exists(args.video):
        sys.exit(f"视频不存在：{args.video}")

    with open(args.script, encoding="utf-8") as f:
        spec = json.load(f)
    lines = spec.get("lines") or []
    voices = spec.get("voices") or {}
    default_voice = spec.get("default_voice") or "zh-CN-YunxiaNeural"
    if not lines:
        sys.exit("dialogue.json 里没有 lines")

    tmpdir = tempfile.mkdtemp(prefix="dub_")
    audio_files = []
    try:
        for i, line in enumerate(lines):
            text = (line.get("text") or "").strip()
            if not text:
                continue
            who = line.get("who") or ""
            voice = line.get("voice") or voices.get(who) or default_voice
            p = os.path.join(tmpdir, f"line_{i:02d}.mp3")
            print(f"[{i+1}/{len(lines)}] {line.get('start', 0):>5.1f}s {who or '-'} {voice} ← {text}")
            tts_line(text, voice, p, rate=line.get("rate"), pitch=line.get("pitch"),
                     volume=line.get("volume"))
            if not args.keep_leading_silence:
                p = trim_leading_silence(ff, p, p.replace(".mp3", ".wav"))
            audio_files.append((p, float(line.get("start", 0)) + args.offset))

        if not audio_files:
            sys.exit("没有可用台词")

        cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", args.video]
        for p, _ in audio_files:
            cmd += ["-i", p]
        amb_index = None
        if args.ambience:
            if not os.path.exists(args.ambience):
                sys.exit(f"环境音不存在：{args.ambience}")
            cmd += ["-i", args.ambience]
            amb_index = len(audio_files) + 1

        parts = []
        mix_labels = []
        for idx, (_, start) in enumerate(audio_files):
            ms = max(0, int(round(start * 1000)))
            label = f"a{idx}"
            parts.append(f"[{idx+1}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                         f"adelay={ms}:all=1,volume={args.voice_gain}dB[{label}]")
            mix_labels.append(f"[{label}]")
        if amb_index is not None:
            parts.append(f"[{amb_index}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                         f"volume={args.ambience_gain}dB[amb]")
            mix_labels.append("[amb]")
        parts.append("".join(mix_labels) + f"amix=inputs={len(mix_labels)}:normalize=0:duration=longest[mixed]")
        parts.append("[mixed]alimiter=limit=0.95,apad[aout]")
        filter_complex = ";".join(parts)

        cmd += ["-filter_complex", filter_complex,
                "-map", "0:v:0", "-map", "[aout]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-shortest", args.out]
        print("\n→ ffmpeg 混音中…")
        res = subprocess.run(cmd, capture_output=True)
        if res.returncode != 0:
            sys.stderr.write((res.stderr or b"").decode("utf-8", "ignore")[-1200:])
            sys.exit("混音失败")
        size = os.path.getsize(args.out) / 1024 / 1024
        print(f"✓ 完成：{args.out}（{size:.1f} MB，{len(audio_files)} 句对白"
              + ("，含铺底" if amb_index is not None else "") + "）")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
