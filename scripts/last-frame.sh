#!/usr/bin/env bash
# last-frame.sh —— 从上一段成片抽「可用的末帧」，作为下一段的首帧（画面首帧）
#
# 为什么需要它：两段视频之间没有切镜时（第二段延续第一段的画面），第二段必须从上一段
#   结尾的那一帧起步，否则主体跳位置、光线跳变、景别回弹，观众一眼看出穿帮。
#   而"抽最后一帧"这个动作有两个坑：① 必须走 ffx.sh（Windows 裸 ffmpeg 是 0 字节壳，
#   exit 0 但不产出文件）；② 上一段末尾可能正好是黑场、转场、或者还在剧烈运动，
#   抽出来的帧糊得没法当首帧。所以这里抽三帧做自检，给出能用的结论。
#
# 用法：
#   bash scripts/last-frame.sh 05-成片/clip_01.mp4 03-资产/last_01.jpg
#   bash scripts/last-frame.sh 05-成片/clip_01.mp4 03-资产/last_01.jpg --at 0.35
#   bash scripts/last-frame.sh ... --force        # 覆盖已存在的定稿（默认不覆盖）
#
# 参数：
#   --at <秒>   从结尾往回多少秒取帧，默认 0.10（避开容器末帧重复/黑场）
#   --force     输出已存在时覆盖
#
# 退出码：0 可用（含 ⚠ 提醒）｜1 这帧不能用（黑场/过曝，换 --at 重抽）｜2 参数错｜3 环境错｜4 已存在未覆盖
#
# 判定依据：
#   亮度 Y  20–238 之间才算正常画面（<20 黑场、>238 过曝）
#   稳定性  SSIM ≥0.90 视为末尾画面已停住（低于 0.90 = 还在动，抽出来会糊）
#   这两项都过，才拿它当首帧传进下一段。

set -uo pipefail

SELF_DIR=$(cd "$(dirname "$0")" && pwd)
FFX="$SELF_DIR/ffx.sh"
AT=0.10
FORCE=0
SRC=""
OUT=""

usage() { sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --at) AT="${2:-0.10}"; shift 2 ;;
    --force) FORCE=1; shift ;;
    *) if [ -z "$SRC" ]; then SRC="$1"
       elif [ -z "$OUT" ]; then OUT="$1"
       else echo "✗ 多余参数：$1" >&2; exit 2; fi
       shift ;;
  esac
done

[ -n "$SRC" ] && [ -n "$OUT" ] || { usage; exit 2; }
[ -f "$SRC" ] || { echo "✗ 找不到视频：$SRC" >&2; exit 2; }
[ -f "$FFX" ] || { echo "✗ 同目录没有 ffx.sh——所有 ffmpeg 调用必须走它（见 SKILL）" >&2; exit 3; }
if [ -e "$OUT" ] && [ "$FORCE" -ne 1 ]; then
  echo "✗ $OUT 已存在。这可能是上一段的定稿首帧，默认不覆盖；确认要换就加 --force" >&2
  exit 4
fi
mkdir -p "$(dirname "$OUT")" 2>/dev/null || true

# ffmpeg 在 Windows 上对 /c/... 这类 MSYS 路径不认，能转就转
wp() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }

DUR=$("$FFX" ffprobe -v error -show_entries format=duration -of csv=p=0 "$(wp "$SRC")" 2>/dev/null | head -1 | tr -d '\r')
case "$DUR" in
  ''|*[!0-9.]*) echo "✗ 读不到时长（文件损坏，或 ffmpeg 是空壳）：$SRC" >&2
                echo "  → 先跑 bash scripts/check-env.sh 确认 ffmpeg 是真 exe" >&2
                exit 3 ;;
esac

# 时长不够时把偏移拉回来，别抽空
AT=$(awk -v d="$DUR" -v a="$AT" 'BEGIN{ if (d-a < 0.2) a = (d>0.3? d-0.2 : 0); printf "%.3f", a }')

TMPD=$(mktemp -d 2>/dev/null || echo "${TMPDIR:-/tmp}/lastframe.$$")
mkdir -p "$TMPD"
trap 'rm -rf -- "$TMPD" >/dev/null 2>&1 || true' EXIT

grab() { # $1=往回秒数 $2=输出文件
  "$FFX" ffmpeg -v error -y -sseof "-$1" -i "$(wp "$SRC")" -frames:v 1 -q:v 2 "$(wp "$2")" >/dev/null 2>&1
  if [ ! -s "$2" ]; then
    local at
    at=$(awk -v d="$DUR" -v o="$1" 'BEGIN{v=d-o; if(v<0)v=0; printf "%.3f", v}')
    "$FFX" ffmpeg -v error -y -ss "$at" -i "$(wp "$SRC")" -frames:v 1 -q:v 2 "$(wp "$2")" >/dev/null 2>&1
  fi
  [ -s "$2" ]
}

yavg() {
  # metadata=print 走 info 级日志，-v error 会把它吞掉 → 这里只压 banner
  "$FFX" ffmpeg -hide_banner -i "$(wp "$1")" \
    -vf "signalstats,metadata=print:key=lavfi.signalstats.YAVG" -f null - 2>&1 \
    | sed -n 's/.*YAVG=\([0-9.]*\).*/\1/p' | head -1
}

ssim() {
  # 同上：ssim 汇总行也是 info 级
  "$FFX" ffmpeg -hide_banner -i "$(wp "$1")" -i "$(wp "$2")" -lavfi ssim -f null - 2>&1 \
    | sed -n 's/.*All:\([0-9.]*\).*/\1/p' | head -1
}

MAIN="$TMPD/main.jpg"
PREV="$TMPD/prev.jpg"
PREV2="$TMPD/prev2.jpg"
grab "$AT" "$MAIN" || { echo "✗ 抽帧失败（位置 -${AT}s / 时长 ${DUR}s）" >&2; exit 1; }
grab "$(awk -v a="$AT" 'BEGIN{printf "%.3f", a+0.25}')" "$PREV" || true
grab "$(awk -v a="$AT" 'BEGIN{printf "%.3f", a+0.50}')" "$PREV2" || true

Y=$(yavg "$MAIN")
S1=""; S2=""
[ -s "$PREV" ] && S1=$(ssim "$MAIN" "$PREV")
[ -s "$PREV" ] && [ -s "$PREV2" ] && S2=$(ssim "$PREV" "$PREV2")

BAD=""; WARN=""
if [ -n "$Y" ]; then
  awk -v y="$Y" 'BEGIN{exit !(y<20)}' && BAD="黑场（Y=$Y，画面几乎全黑）"
  awk -v y="$Y" 'BEGIN{exit !(y>238)}' && BAD="过曝（Y=$Y，画面几乎全白）"
else
  WARN="读不到亮度，肉眼确认一下"
fi
if [ -z "$BAD" ] && [ -n "$S1" ]; then
  awk -v s="$S1" 'BEGIN{exit !(s<0.90)}' && WARN="末尾画面还在动（SSIM=$S1 < 0.90），抽出来可能糊或正好在切点上"
fi
if [ -z "$BAD" ] && [ -n "$S2" ]; then
  awk -v s="$S2" 'BEGIN{exit !(s<0.90)}' && WARN="${WARN:+$WARN；}上一段最后 0.5 秒一直在变（SSIM=$S2），确认这不是转场段"
fi

if [ -n "$BAD" ]; then
  echo "✗ 抽到的帧不能用：$BAD"
  echo "  源：$SRC（${DUR}s，取帧位置 -${AT}s）"
  echo "  → 换更早的位置重抽：bash scripts/last-frame.sh \"$SRC\" \"$OUT\" --at 0.6"
  echo "  → 或者回看上一段结尾：如果是转场/黑场收尾，这一段其实该按「切镜」处理、用 first_<X>.png"
  exit 1
fi

cp -f "$MAIN" "$OUT" 2>/dev/null || { echo "✗ 写不进 $OUT" >&2; exit 3; }
[ -s "$OUT" ] || { echo "✗ 输出文件是空的：$OUT" >&2; exit 3; }

echo "✅ 末帧已抽出：$OUT"
echo "   源：$SRC（${DUR}s，取帧位置 -${AT}s）"
[ -n "$Y" ] && echo "   亮度 Y=$Y（正常区间 20–238）"
[ -n "$S1" ] && echo "   末尾稳定性 SSIM=$S1（≥0.90 视为画面已停住）"
if [ -n "$WARN" ]; then
  echo "   ⚠ $WARN"
  echo "   → 先肉眼看这张图：主体完整、不糊、不是半截转场。能接受就继续；不行就 --at 0.6 重抽"
fi
echo ""
echo "下一步（这三处必须一致，少一处模型就不把它当首帧）："
echo "   1) 素材清单 clip_NN.materials.txt 里这一行的职责写「画面首帧」，例："
echo "      @Image4 | 03-资产/$(basename "$OUT") | 画面首帧：锁定本段第一帧的构图/景别/机位/主体位置/光线方向 | 不取上一段的表情走向与运动残余"
echo "   2) 提示词【参考素材】段写：@Image4 是本段【画面首帧】，直接以它作为本段第一帧，从这一画面继续动作"
echo "   3) 正文动作时间轴从第 0 秒开始写，且第 0 秒画面必须与 @Image4 一致"
echo "   → 然后跑 python scripts/ref_check.py 校验三处是否真的对上了"
exit 0
