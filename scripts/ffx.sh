#!/usr/bin/env bash
# ffx.sh —— 用一个"真的能跑"的 ffmpeg / ffprobe 执行命令
#
# 为什么需要它：Windows 上 winget 的 Links\ffmpeg.exe 是 0 字节别名壳，
#   直接跑 `ffmpeg ...` 会 **exit 0 但什么都不做**（不报错、不产出文件）。
#   拼接、抽末帧、抽音轨这些命令如果静默失败，后面全是空跑。
#
# 用法（把平时写的 ffmpeg 直接搬到 ffx.sh 后面）：
#   bash scripts/ffx.sh ffmpeg  -y -i a.mp4 -vn -ar 16000 -ac 1 a.wav
#   bash scripts/ffx.sh ffprobe -v error -show_entries format=duration -of csv=p=0 a.mp4
#
# 成功时透传被调用程序的退出码；找不到可用程序则退出码 3。

set -uo pipefail

TOOL="${1:-}"
shift || true

case "$TOOL" in
  ffmpeg|ffprobe) ;;
  -h|--help|"")
    sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  *)
    echo "用法: bash scripts/ffx.sh <ffmpeg|ffprobe> <参数...>" >&2
    exit 2
    ;;
esac

# 候选必须真的能跑起来才算数（0 字节壳跑 -version 不会有输出）
usable() {
  local c="$1"
  [ -n "$c" ] || return 1
  case "$c" in */*|*\\*) [ -f "$c" ] || return 1 ;; esac
  local out
  out=$("$c" -version 2>/dev/null | head -1 || true)
  [ -n "$out" ]
}

resolve_tool() {
  local name="$1" c d pkgroot found
  for c in "$(command -v "$name" 2>/dev/null || true)" "$(command -v "$name.exe" 2>/dev/null || true)"; do
    if usable "$c"; then echo "$c"; return 0; fi
  done
  # 回落到 winget 包目录里逐个验证（Links 里是壳，Packages 里才是真 exe）
  if [ -n "${LOCALAPPDATA:-}" ]; then
    d=$(cygpath -u "$LOCALAPPDATA" 2>/dev/null || echo "$LOCALAPPDATA")
    [ -d "$d/Microsoft/WinGet/Packages" ] && pkgroot="$d/Microsoft/WinGet/Packages"
  fi
  if [ -z "${pkgroot:-}" ]; then
    for d in /c/Users/*/AppData/Local/Microsoft/WinGet/Packages /mnt/c/Users/*/AppData/Local/Microsoft/WinGet/Packages; do
      [ -d "$d" ] && { pkgroot="$d"; break; }
    done
  fi
  if [ -n "${pkgroot:-}" ]; then
    found=$(find "$pkgroot" -name "${name}.exe" 2>/dev/null | while read -r f; do
      if usable "$f"; then echo "$f"; break; fi
    done)
    [ -n "$found" ] && { echo "$found"; return 0; }
  fi
  # 常见的 Homebrew / 系统路径
  for c in /opt/homebrew/bin/"$name" /usr/local/bin/"$name" /usr/bin/"$name"; do
    if usable "$c"; then echo "$c"; return 0; fi
  done
  return 1
}

BIN=$(resolve_tool "$TOOL") || {
  echo "✗ 找不到可用的 $TOOL" >&2
  echo "  → 先跑 bash scripts/check-env.sh；Windows: winget install Gyan.FFmpeg（装完重开终端）；macOS: brew install ffmpeg" >&2
  echo "  → 注意：PATH 里的 winget 别名可能是 0 字节空壳，必须装真正的 ffmpeg" >&2
  exit 3
}

exec "$BIN" "$@"
