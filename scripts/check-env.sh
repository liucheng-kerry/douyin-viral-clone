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
    # 会员等级：seedance2.5（默认主通道）是 VIP 限定，standard 账号提交会被直接拒
    VIP=$(echo "$CREDIT" | grep -oE '"vip_level": *"[^"]*"' | sed 's/.*: *"//; s/"$//' | head -1)
    if echo "$VIP" | grep -qiE '^(vip|svip|premium|pro|member)'; then
      echo "  ✅ 会员等级 ${VIP}（seedance2.5 可用）"
    else
      echo "  ⚠️  会员等级 ${VIP:-未知}：CLI 标注 seedance2.5 为 VIP 限定（未实测是否拦截）"
      echo "     → 若阶段 5 提交报权限不足，降级 seedance2.0_vip（720p/1080p/4k，图 ≤9）或开会员"
    fi
    # 余额门：图片约 3 积分/张；视频 720p 约 20 积分/秒 → 15 秒成片约 300 积分
    if [ -n "${BAL:-}" ] && [ "$BAL" -lt 100 ] 2>/dev/null; then
      echo "  ⚠️  余额偏低（${BAL} 积分）：一支 15 秒 720p 成片约需 300 积分，出图每张约 3 积分"
      echo "     → 先充值再开工，别等阶段 3 第一张图就失败"
    fi
    echo "  ℹ️  首次使用 seedance2.5 前，需先在即梦 Web 端用该模型生成一次；否则会返回 AigcComplianceConfirmationRequired"
  else
    fail "dreamina 未登录" "dreamina login --headless 拿到 verification_uri / user_code / device_code（打印完即退出），浏览器授权后再 dreamina login checklogin --device_code=<code> --poll=30；已登录过但失效则 dreamina relogin --headless"
  fi
  # 全能参考（multimodal2video）是整个流程的视频主通道，旧版 CLI 可能根本没有这个子命令
  if $DREAMINA multimodal2video --help 2>&1 | grep -q -- '--image'; then
    pass "multimodal2video（全能参考）可用"
  else
    fail "dreamina 不支持 multimodal2video（全能参考）" "CLI 版本过旧，重跑安装命令升级：curl -s https://jimeng.jianying.com/cli | bash，再 dreamina login"
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
