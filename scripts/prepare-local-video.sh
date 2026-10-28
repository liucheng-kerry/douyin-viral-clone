#!/usr/bin/env bash
# prepare-local-video.sh —— 本地视频拆解证据包（不依赖 Docker）
#
# 用法:
#   bash scripts/prepare-local-video.sh <视频文件> [输出目录]
#
# 产出（输出目录内）:
#   metadata.json   时长/分辨率/码率等元信息
#   audio.wav       抽出的单声道音轨
#   audio.txt       口播转写稿（whisper）
#   scene_XXX.jpg   场景切换帧
#   frame_XXX.jpg   每秒一帧的逐帧采样（拆解时逐帧全量分析用）
#   sheet_XX.jpg    contact sheet 拼图（全局概览用，不替代逐帧分析）
#   manifest.txt    本次抓到了什么，给拆解阶段先读
#
# 环境变量: WHISPER_MODEL(默认 medium) / SCENE_THRESHOLD(默认 0.35) / SAMPLE_FPS(默认 1，即逐秒逐帧)

set -euo pipefail

VIDEO="${1:-}"
OUT="${2:-./01-拆解}"
case "$VIDEO" in
  -h|--help|help)
    sed -n '3,16p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
esac
if [ -z "$VIDEO" ]; then
  echo "用法: bash scripts/prepare-local-video.sh <视频文件> [输出目录]" >&2
  exit 1
fi
if [ ! -f "$VIDEO" ]; then
  echo "✗ 找不到视频文件: $VIDEO" >&2
  exit 1
fi

# ---- 解析 ffmpeg：优先 PATH，PATH 里的 winget 别名可能是 0 字节空壳 ----
resolve_ff() {
  local name="$1" v d
  if command -v "$name" >/dev/null 2>&1; then
    v=$("$name" -version 2>/dev/null | head -1 || true)
    if [ -n "$v" ]; then echo "$name"; return 0; fi
  fi
  # PATH 里的 winget 别名常常是 0 字节空壳，回落到包目录找真实 exe
  local pkgroot=""
  if [ -n "${LOCALAPPDATA:-}" ]; then
    d=$(cygpath -u "$LOCALAPPDATA" 2>/dev/null || echo "$LOCALAPPDATA")
    [ -d "$d/Microsoft/WinGet/Packages" ] && pkgroot="$d/Microsoft/WinGet/Packages"
  fi
  if [ -z "$pkgroot" ]; then
    for d in /c/Users/*/AppData/Local/Microsoft/WinGet/Packages; do
      [ -d "$d" ] && { pkgroot="$d"; break; }
    done
  fi
  if [ -n "$pkgroot" ]; then
    local found
    found=$(find "$pkgroot" -name "${name}.exe" 2>/dev/null | head -1 || true)
    [ -n "$found" ] && { echo "$found"; return 0; }
  fi
  return 1
}

FFMPEG=$(resolve_ff ffmpeg) || { echo "✗ 未找到可用的 ffmpeg" >&2; exit 1; }
FFPROBE=$(resolve_ff ffprobe) || { echo "✗ 未找到可用的 ffprobe" >&2; exit 1; }
PY=$(command -v python || command -v python3 || true)
[ -n "$PY" ] || { echo "✗ 未找到 python" >&2; exit 1; }

WHISPER_MODEL="${WHISPER_MODEL:-medium}"
SCENE_THRESHOLD="${SCENE_THRESHOLD:-0.25}"
SAMPLE_FPS="${SAMPLE_FPS:-1}"

# 把真实 ffmpeg 所在目录提到 PATH 最前：whisper 内部也调 ffmpeg，
# 否则会命中 winget 的 0 字节空壳导致 [WinError 193] 静默失败
export PATH="$(dirname "$FFMPEG"):$(dirname "$FFPROBE"):$PATH"

mkdir -p "$OUT"
echo "→ ffmpeg: $FFMPEG"
echo "→ 输出目录: $OUT"

# ---- 0. 原片落位: 01-拆解/video/ ----
# 阶段 1.6（风格锚定）和阶段 3（同秒帧比对）都从 video/ 取源；
# fetch-video.sh 天然产出到这里，但用户给本地文件时不会——不复制过去，后续命令就找不到原片。
mkdir -p "$OUT/video"
VIDEO_NAME=$(basename "$VIDEO")
if [ -f "$OUT/video/$VIDEO_NAME" ]; then
  echo "✓ 原片已在 video/ 下（$VIDEO_NAME）"
else
  cp "$VIDEO" "$OUT/video/$VIDEO_NAME"
  echo "✓ 原片已复制到 $OUT/video/$VIDEO_NAME"
fi

# ---- 1. 元信息 ----
"$FFPROBE" -v error -show_entries format=duration,size,bit_rate \
  -show_entries stream=codec_name,width,height,r_frame_rate \
  -of json "$VIDEO" > "$OUT/metadata.json" 2>/dev/null || echo "{}" > "$OUT/metadata.json"
DUR=$(grep -o '"duration": *"[0-9.]*"' "$OUT/metadata.json" | head -1 | grep -o '[0-9.]*' || echo "0")
echo "✓ 元信息已写入 metadata.json（时长 ${DUR}s）"

# ---- 2. 音轨 ----
"$FFMPEG" -y -v error -i "$VIDEO" -vn -ac 1 -ar 16000 "$OUT/audio.wav" 2>/dev/null \
  || echo "! 抽音轨失败（视频可能没有音轨，后续按无口播处理）"
[ -f "$OUT/audio.wav" ] && echo "✓ 音轨已抽出"

# ---- 3. 口播转写 ----
if [ -f "$OUT/audio.wav" ]; then
  echo "→ whisper 转写中（模型 $WHISPER_MODEL，首次会下载模型，较慢，请等）..."
  rm -f "$OUT/audio.txt"
  "$PY" -m whisper "$OUT/audio.wav" --model "$WHISPER_MODEL" --language Chinese \
    --task transcribe --output_format txt --output_dir "$OUT" > "$OUT/whisper.log" 2>&1 || true
  # whisper 内部出错时退出码仍可能是 0，必须以产物判断成败
  if [ -s "$OUT/audio.txt" ]; then
    echo "✓ 转写完成 audio.txt（$(wc -c < "$OUT/audio.txt" | tr -d ' ') 字节）"
  elif grep -q "OSError\|Skipping" "$OUT/whisper.log" 2>/dev/null; then
    echo "! 转写失败（多为 ffmpeg 调用异常），日志见 $OUT/whisper.log；可改用粘贴文案回退"
  else
    echo "! audio.txt 为空：这段音频无人声（纯音乐/音效），按「无口播」处理，请让用户粘贴文案"
  fi
fi

# ---- 4. 场景切换帧（ffmpeg 7+ 用 -fps_mode，-vsync 已废弃会静默失败）----
"$FFMPEG" -y -v error -i "$VIDEO" \
  -vf "select='gt(scene,${SCENE_THRESHOLD})',scale=480:-1" -fps_mode vfr \
  "$OUT/scene_%03d.jpg" 2>/dev/null || true
SCENE_N=$(ls "$OUT"/scene_*.jpg 2>/dev/null | wc -l | tr -d ' ' || true)
echo "✓ 场景切换帧 ${SCENE_N} 张"

# ---- 5. 逐帧采样（每秒一帧，640 宽保证细节可读）+ contact sheet ----
"$FFMPEG" -y -v error -i "$VIDEO" -vf "fps=${SAMPLE_FPS},scale=640:-2" \
  "$OUT/frame_%03d.jpg" 2>/dev/null || true
FRAME_N=$(ls "$OUT"/frame_*.jpg 2>/dev/null | wc -l | tr -d ' ' || true)
echo "✓ 逐帧采样 ${FRAME_N} 张（每秒一帧）"

"$FFMPEG" -y -v error -i "$VIDEO" \
  -vf "fps=${SAMPLE_FPS},scale=320:-1,tile=5x4" -frames:v 6 \
  "$OUT/sheet_%02d.jpg" 2>/dev/null || true
SHEET_N=$(ls "$OUT"/sheet_*.jpg 2>/dev/null | wc -l | tr -d ' ' || true)
echo "✓ contact sheet ${SHEET_N} 张（拆解时用 Read 看图）"

# ---- 6. manifest ----
{
  echo "来源: 本地文件 $VIDEO"
  echo "原片: video/$VIDEO_NAME（阶段 1.6 风格锚 / 阶段 3 同秒帧比对都从这里取）"
  echo "时长: ${DUR}s"
  echo "元信息: metadata.json"
  [ -f "$OUT/audio.wav" ]  && echo "音轨: audio.wav"
  if [ -s "$OUT/audio.txt" ]; then
    echo "转写稿: audio.txt（$(wc -c < "$OUT/audio.txt" | tr -d ' ') 字节）"
  else
    echo "转写稿: 无（音频无人声或转写失败，需粘贴文案）"
  fi
  echo "场景帧: ${SCENE_N} 张 scene_*.jpg"
  echo "逐帧采样: ${FRAME_N} 张 frame_*.jpg（每秒一帧，拆解必须逐张分析）"
  echo "拼图: ${SHEET_N} 张 sheet_*.jpg（仅全局概览）"
} > "$OUT/manifest.txt"

echo "━━━ 证据包就绪 ━━━"
cat "$OUT/manifest.txt"
