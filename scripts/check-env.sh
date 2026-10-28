#!/usr/bin/env bash
# check-env.sh —— 学员首次使用前的环境自检（阶段 0）
#
# 用法: bash scripts/check-env.sh
# 退出码: 0 = 必需项齐全；1 = 缺必需项
#
# 检查项分级：
#   必需（缺了跑不通）：git / ffmpeg / ffprobe / python / whisper / dreamina（已安装 + 已登录）
#   可选（缺了降级）：edge-tts（仅兜底配音用）/ node（npx 安装方式）/ docker（本地抖音下载 API 通道）/ yt-dlp（链接下载通道）

set -uo pipefail

OK=0; MISS=0
pass() { echo "  ✅ $1"; }
fail() { echo "  ❌ $1"; echo "     → $2"; MISS=$((MISS+1)); }
warn() { echo "  ⚠️  $1（可选，缺失时自动降级）"; echo "     → $2"; }

echo "━━━ 1. 基础工具 ━━━"
if command -v git >/dev/null 2>&1; then pass "git $(git --version | awk '{print $3}')"; else
  fail "git 未安装" "Windows: winget install Git.Git ｜ macOS: brew install git"; fi

# ffmpeg：PATH 里的 winget 别名有时是 0 字节空壳，必须以 -version 有输出为准
resolve_bin() {
  local name="$1" v
  if command -v "$name" >/dev/null 2>&1; then
    v=$("$name" -version 2>/dev/null | head -1 || true)
    [ -n "$v" ] && { echo "$(command -v "$name")"; return 0; }
  fi
  local pkgroot=""
  if [ -n "${LOCALAPPDATA:-}" ]; then
    pkgroot="$(cygpath -u "$LOCALAPPDATA" 2>/dev/null || echo "$LOCALAPPDATA")/Microsoft/WinGet/Packages"
    [ -d "$pkgroot" ] || pkgroot=""
  fi
  if [ -n "$pkgroot" ]; then
    local f; f=$(find "$pkgroot" -name "${name}.exe" 2>/dev/null | head -1 || true)
    [ -n "$f" ] && { echo "$f"; return 0; }
  fi
  return 1
}
for b in ffmpeg ffprobe; do
  if p=$(resolve_bin "$b"); then pass "$b → $p"; else
    fail "$b 未安装" "Windows: winget install Gyan.FFmpeg（装完重开终端）｜ macOS: brew install ffmpeg"; fi
done

echo "━━━ 2. 音频能力（转写 + 配音）━━━"
PY=$(command -v python || command -v python3 || true)
if [ -n "$PY" ]; then
  pass "python $($PY --version 2>&1 | awk '{print $2}')"
  if "$PY" -c "import whisper" 2>/dev/null; then pass "openai-whisper 已安装（口播转写）"; else
    fail "openai-whisper 未安装" "pip install -U openai-whisper（首次转写会下载 medium 模型，约 1.5GB）"; fi
  # 即梦 seedance2.5 原生音画同步，输出 mp4 自带人声音轨；edge-tts 只在模型不出人声时兜底
  if "$PY" -c "import edge_tts" 2>/dev/null; then pass "edge-tts 已安装（兜底配音可用）"; else
    warn "edge-tts 未安装" "仅在模型没生成人声时才需要：pip install -U edge-tts"; fi
else
  fail "python 未安装" "Windows: winget install Python.Python.3.12 ｜ macOS: brew install python"
fi

echo "━━━ 3. 即梦 CLI（生图生视频通道）━━━"
DREAMINA=""
if command -v dreamina >/dev/null 2>&1; then DREAMINA="dreamina"
elif [ -x "$HOME/bin/dreamina.exe" ]; then DREAMINA="$HOME/bin/dreamina.exe"
elif [ -x "$HOME/bin/dreamina" ]; then DREAMINA="$HOME/bin/dreamina"
fi
if [ -n "$DREAMINA" ]; then
  DV=$($DREAMINA --version 2>&1 | tr -d '\n' | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
  pass "dreamina 已安装 → $DREAMINA${DV:+（v$DV）}"
  CREDIT=$($DREAMINA user_credit 2>&1)
  if echo "$CREDIT" | grep -qE 'total_credit'; then
    BAL=$(echo "$CREDIT" | grep -oE '"total_credit": *[0-9]+' | grep -oE '[0-9]+' | head -1)
    echo "  ✅ 已登录即梦，积分余额 ${BAL:-见 user_credit 输出}"
  else
    fail "dreamina 未登录" "运行 dreamina login，按提示在浏览器完成授权（5 分钟内有效），或问 agent 帮你发起登录"
  fi
else
  fail "dreamina 未安装" "curl -s https://jimeng.jianying.com/cli | bash（需 Git Bash/macOS/Linux 终端；装完重开终端，再跑 dreamina login）"
fi

echo "━━━ 4. 可选增强 ━━━"
if command -v node >/dev/null 2>&1; then
  NV=$(node --version | tr -d v); pass "node $NV（npx 安装技能方式可用）"
else
  warn "node 未安装" "仅影响 npx 安装方式；用 git clone 安装可忽略"
fi
if command -v docker >/dev/null 2>&1 && docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
  pass "docker 运行中（本地抖音下载 API 可用）"
else
  warn "docker 未安装或未启动" "抖音链接自动下载通道不可用；改用「手机保存视频 → 拖进对话」或 yt-dlp 通道"
fi
if command -v yt-dlp >/dev/null 2>&1; then pass "yt-dlp $(yt-dlp --version 2>&1 | head -1)"; else
  warn "yt-dlp 未安装" "B站/TikTok/YouTube 链接下载会失败；pip install -U yt-dlp"
fi

echo
echo "━━━ 结果 ━━━"
if [ "$MISS" -eq 0 ]; then
  echo "🎉 必需项全部就绪，可以直接开始复刻流程。"
  exit 0
else
  echo "❌ 还有 $MISS 项必需依赖未就绪，按上面 → 后的命令装好后重跑本脚本。"
  exit 1
fi
