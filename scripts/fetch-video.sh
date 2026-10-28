#!/usr/bin/env bash
# fetch-video.sh —— 链接下载通道（yt-dlp，不依赖 Docker/WSL）
#
# 用法:
#   bash scripts/fetch-video.sh "<视频链接>" [输出目录] [--cookies chrome]
#
# 产出: <输出目录>/video/<标题>.mp4 + <输出目录>/metadata.json(yt-dlp 原始信息)
# 下载成功后可直接接着跑 prepare-local-video.sh 做拆解证据包。
#
# 环境变量: YTDLP_PROXY(如 http://127.0.0.1:7892) / COOKIES_FROM(如 chrome)

set -euo pipefail

URL="${1:-}"
OUT="${2:-./01-拆解}"
COOKIES="${COOKIES_FROM:-}"

case "$URL" in
  -h|--help|help|"")
    sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
    echo
    echo "环境变量: YTDLP_PROXY=http://127.0.0.1:7892（网络受限时）/ COOKIES_FROM=chrome（抖音、小红书要登录态）"
    [ -z "$URL" ] && exit 1
    exit 0
    ;;
esac

# 兼容用户从 App 复制的分享文案：从文本里抠出第一个 http(s) 链接
if ! printf '%s' "$URL" | grep -qE '^https?://'; then
  EXTRACTED=$(printf '%s' "$URL" | grep -oE 'https?://[A-Za-z0-9./?=_%:&+-]+' | head -1 || true)
  [ -n "$EXTRACTED" ] && URL="$EXTRACTED"
  echo "→ 从分享文案中提取链接: $URL"
fi

PY=$(command -v python || command -v python3 || true)
[ -n "$PY" ] || { echo "✗ 未找到 python" >&2; exit 1; }

# ---- 解析真实 ffmpeg（winget 的 PATH 别名常是 0 字节空壳，yt-dlp 会找不到）----
resolve_ff() {
  local name="$1" v d pkgroot="" found
  if command -v "$name" >/dev/null 2>&1; then
    v=$("$name" -version 2>/dev/null | head -1 || true)
    [ -n "$v" ] && { echo "$(command -v "$name")"; return 0; }
  fi
  if [ -n "${LOCALAPPDATA:-}" ]; then
    d=$(cygpath -u "$LOCALAPPDATA" 2>/dev/null || echo "$LOCALAPPDATA")
    [ -d "$d/Microsoft/WinGet/Packages" ] && pkgroot="$d/Microsoft/WinGet/Packages"
  fi
  if [ -z "$pkgroot" ]; then
    for d in /c/Users/*/AppData/Local/Microsoft/WinGet/Packages; do
      [ -d "$d" ] && { pkgroot="$d"; break; }
    done
  fi
  [ -n "$pkgroot" ] || return 1
  found=$(find "$pkgroot" -name "${name}.exe" 2>/dev/null | head -1 || true)
  [ -n "$found" ] && { echo "$found"; return 0; }
  return 1
}

FFMPEG=$(resolve_ff ffmpeg || true)
FF_DIR=""
if [ -n "$FFMPEG" ]; then
  FF_DIR=$(dirname "$FFMPEG")
  export PATH="$FF_DIR:$PATH"
  echo "→ ffmpeg: $FFMPEG"
else
  echo "! 未找到可用 ffmpeg：将只能下载无音轨的单文件格式"
fi

mkdir -p "$OUT/video"

YTDLP_ARGS=(
  --no-playlist
  --no-warnings
  --newline
  --merge-output-format mp4
  -o "$OUT/video/%(title).60s.%(ext)s"
  --write-info-json
  --user-agent "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

if [ -n "$FF_DIR" ]; then
  YTDLP_ARGS+=(-f "bv*+ba/b" --ffmpeg-location "$FF_DIR")
else
  YTDLP_ARGS+=(-f "b")
fi

if [ -n "${YTDLP_PROXY:-}" ]; then
  YTDLP_ARGS+=(--proxy "$YTDLP_PROXY")
  echo "→ 走代理: $YTDLP_PROXY"
fi

if [ -n "$COOKIES" ]; then
  YTDLP_ARGS+=(--cookies-from-browser "$COOKIES")
  echo "→ 使用 $COOKIES 的登录态"
fi

echo "→ 开始下载..."
set +e
"$PY" -m yt_dlp "${YTDLP_ARGS[@]}" "$URL" > "$OUT/ytdlp.log" 2>&1
DL_RC=$?
set -e
tail -6 "$OUT/ytdlp.log" || true
if [ $DL_RC -ne 0 ]; then
  echo "! yt-dlp 下载失败。常见解法（按顺序试）:" >&2
  echo "  1) 抖音/小红书需要登录态: 设 COOKIES_FROM=chrome 或 edge 后重跑" >&2
  echo "  2) 网络受限: 设 YTDLP_PROXY=http://127.0.0.1:7892 后重跑" >&2
  echo "  3) 仍失败: 用手机 App 保存视频，改用 prepare-local-video.sh 本地通道" >&2
  exit 1
fi

VIDEO=$(ls -t "$OUT"/video/*.mp4 2>/dev/null | head -1 || true)
if [ -z "$VIDEO" ]; then
  echo "✗ 没找到下载的 mp4，请检查上面的日志" >&2
  exit 1
fi

# 把 yt-dlp 的 info.json 转成拆解用的 metadata.json
INFO=$(ls -t "$OUT"/video/*.info.json 2>/dev/null | head -1 || true)
if [ -n "$INFO" ]; then
  "$PY" - "$INFO" "$OUT/metadata.json" <<'PYEOF'
import json, sys
src, dst = sys.argv[1], sys.argv[2]
d = json.load(open(src, encoding='utf-8'))
keep = {k: d.get(k) for k in ("title","uploader","duration","view_count","like_count",
                              "comment_count","repost_count","upload_date","webpage_url",
                              "width","height","fps","tags","description")}
json.dump({k: v for k, v in keep.items() if v is not None}, open(dst,'w',encoding='utf-8'),
          ensure_ascii=False, indent=2)
print("✓ metadata.json 已生成")
PYEOF
fi

echo "━━━ 下载完成 ━━━"
echo "视频: $VIDEO"
echo "下一步: bash scripts/prepare-local-video.sh \"$VIDEO\" \"$OUT\""
