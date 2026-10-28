#!/usr/bin/env bash
# split-source.sh —— 按分段表从原片切出「动作参考片段」（阶段 5 全能参考的 --video 素材）
#
# 为什么需要：全能参考（multimodal2video）可以把原片对应片段当 --video 传入，
# 让动作、运镜、节奏直接照搬，比用文字描述"推镜/摇镜"准得多。但原片整条不能传
# （seedance2.5 单条 2–30s、总时长 30s），必须按段切。
#
# 用法:
#   bash scripts/split-source.sh <原片> <输出目录> <段落清单>
#
# 段落清单（每行一段，两种写法都认，# 开头为注释）:
#   clip_01 0 6.5
#   clip_02 6.5-12
#
# 产出（输出目录内）:
#   src_01.mp4 src_02.mp4 …   每段参考片段（流拷贝，不重编码，画质无损）
#   src-manifest.txt          每段时长/大小/是否降级重编码，给阶段 5 申请单直接引用
#
# 时长硬约束（脚本会检查并告警）:
#   seedance2.5 每个视频素材 2–30s，全片视频素材总时长 ≤30s
#   → 只传「本段」的原片段，不要把全片都传进去

set -uo pipefail

SRC="${1:-}"
OUT="${2:-./01-拆解/src_clips}"
LIST="${3:-}"
if [ -z "$SRC" ] || [ -z "$LIST" ]; then
  echo "用法: bash scripts/split-source.sh <原片> <输出目录> <段落清单>" >&2
  echo "清单格式: clip_01 0 6.5 或 clip_01 0-6.5（每行一段）" >&2
  exit 1
fi
[ -f "$SRC" ] || { echo "✗ 找不到原片: $SRC" >&2; exit 1; }
[ -f "$LIST" ] || { echo "✗ 找不到段落清单: $LIST" >&2; exit 1; }

resolve_ff() {
  local name="$1" v
  if command -v "$name" >/dev/null 2>&1; then
    v=$("$name" -version 2>/dev/null | head -1 || true)
    if [ -n "$v" ]; then echo "$name"; return 0; fi
  fi
  local pkgroot=""
  if [ -n "${LOCALAPPDATA:-}" ]; then
    pkgroot="$LOCALAPPDATA/Microsoft/WinGet/Packages"
    if [ -d "$pkgroot" ]; then
      local hit
      hit=$(find "$pkgroot" -type f -iname "$name.exe" 2>/dev/null | head -1 || true)
      if [ -n "$hit" ]; then
        v=$("$hit" -version 2>/dev/null | head -1 || true)
        [ -n "$v" ] && { echo "$hit"; return 0; }
      fi
    fi
  fi
  return 1
}

FFMPEG=$(resolve_ff ffmpeg) || { echo "✗ 找不到可用的 ffmpeg，先跑 scripts/check-env.sh" >&2; exit 1; }
FFPROBE=$(resolve_ff ffprobe) || FFPROBE=""
echo "→ ffmpeg: $FFMPEG"

mkdir -p "$OUT"
MAN="$OUT/src-manifest.txt"
: > "$MAN"
{
  echo "# 动作参考片段清单（阶段 5 全能参考 --video 素材）"
  echo "# 源片: $SRC"
  echo "# 段号 | 文件 | 起始 | 时长(s) | 大小 | 方式"
} >> "$MAN"

total=0.0
n_ok=0
n_fail=0

dur_of() {
  [ -n "$FFPROBE" ] || { echo ""; return; }
  "$FFPROBE" -v error -show_entries format=duration -of csv=p=0 "$1" 2>/dev/null | tr -d '\r'
}

SRC_DUR=$(dur_of "$SRC")
[ -n "$SRC_DUR" ] && echo "→ 原片时长: ${SRC_DUR}s"

while IFS= read -r line || [ -n "$line" ]; do
  line="$(printf '%s' "$line" | sed 's/[[:space:]]*$//')"
  case "$line" in ''|\#*) continue ;; esac
  # 拆字段：段号 起 止  或  段号 起-止
  name=$(printf '%s' "$line" | awk '{print $1}')
  if printf '%s' "$line" | grep -q -- '-'; then
    range=$(printf '%s' "$line" | awk '{print $2}')
    start="${range%%-*}"
    end="${range##*-}"
  else
    start=$(printf '%s' "$line" | awk '{print $2}')
    end=$(printf '%s' "$line" | awk '{print $3}')
  fi
  if [ -z "$name" ] || [ -z "$start" ] || [ -z "$end" ]; then
    echo "⚠ 跳过无法解析的行: $line"
    continue
  fi
  dur=$(awk -v a="$start" -v b="$end" 'BEGIN{printf "%.3f", b-a}')
  num=$(printf '%s' "$name" | grep -oE '[0-9]+' | tail -1)
  [ -n "$num" ] || num="$name"
  out="$OUT/src_$(printf '%02d' "$num").mp4"

  # 单段硬约束
  awk -v d="$dur" 'BEGIN{exit !(d<2)}' && echo "⚠ $name 只有 ${dur}s，短于 2s，即梦可能拒收（要求 2–30s）"
  awk -v d="$dur" 'BEGIN{exit !(d>30)}' && echo "⚠ $name 有 ${dur}s，超过 30s 上限，请再拆"

  # 起始点超出原片 → 直接判失败（否则 ffmpeg 会吐出空文件骗过下游）
  if [ -n "$SRC_DUR" ]; then
    beyond=$(awk -v s="$start" -v d="$SRC_DUR" 'BEGIN{print (s>=d-0.05) ? 1 : 0}')
    if [ "$beyond" = "1" ]; then
      printf '%s | —— | %ss | 超出原片(%ss) | —— | ——\n' "$name" "$start" "$SRC_DUR" >> "$MAN"
      echo "✗ $name 起始 ${start}s 超出原片时长 ${SRC_DUR}s，检查段落表"
      n_fail=$((n_fail+1))
      continue
    fi
    over_end=$(awk -v e="$end" -v d="$SRC_DUR" 'BEGIN{print (e>d+0.001) ? 1 : 0}')
    if [ "$over_end" = "1" ]; then
      echo "⚠ $name 结束秒 ${end}s 超出原片，已夹到 ${SRC_DUR}s"
      end="$SRC_DUR"
      dur=$(awk -v a="$start" -v b="$end" 'BEGIN{printf "%.3f", b-a}')
    fi
  fi

  # 流拷贝优先（快且无损）；时长偏差大或文件异常则重编码该段
  "$FFMPEG" -y -v error -ss "$start" -i "$SRC" -t "$dur" -c copy -avoid_negative_ts make_zero "$out" 2>/dev/null
  mode="流拷贝"
  got=$(dur_of "$out")
  ok=0
  if [ -n "$got" ] && [ -s "$out" ]; then
    ok=$(awk -v g="$got" -v d="$dur" 'BEGIN{print (g>=d-0.35 && g<=d+0.35) ? 1 : 0}')
  fi
  if [ "$ok" != "1" ]; then
    "$FFMPEG" -y -v error -ss "$start" -i "$SRC" -t "$dur" \
      -c:v libx264 -crf 20 -pix_fmt yuv420p -c:a aac -b:a 128k "$out" 2>/dev/null
    mode="重编码"
    got=$(dur_of "$out")
  fi

  # 成功判定：有内容 + 有时长 + 偏差在容差内，三者全过才算
  sz=$(wc -c < "$out" 2>/dev/null | tr -d ' \r' || echo 0)
  good=0
  if [ -n "$got" ] && [ "${sz:-0}" -gt 2000 ]; then
    good=$(awk -v g="$got" -v d="$dur" 'BEGIN{print (g>=d-0.45 && g<=d+0.45) ? 1 : 0}')
  fi

  if [ "$good" = "1" ]; then
    human=$(du -h "$out" 2>/dev/null | awk '{print $1}')
    printf '%s | %s | %ss | %ss | %s | %s\n' "$name" "$(basename "$out")" "$start" "$got" "${human:-?}" "$mode" >> "$MAN"
    total=$(awk -v t="$total" -v g="$got" 'BEGIN{printf "%.2f", t+g}')
    n_ok=$((n_ok+1))
    echo "✓ $name → $(basename "$out")  ${got}s  ($mode)"
  else
    rm -f "$out"
    printf '%s | —— | %ss | 失败(未切出有效片段) | —— | ——\n' "$name" "$start" >> "$MAN"
    n_fail=$((n_fail+1))
    echo "✗ $name 切分失败（检查起止秒与原片时长）"
  fi
done < "$LIST"

echo
echo "完成：$n_ok 段成功，$n_fail 段失败，参考片段合计 ${total}s"
awk -v t="$total" 'BEGIN{if (t>30) print "⚠ 合计超过 30s：seedance2.5 的全部视频素材总时长上限是 30s，只传当前段用得到的那一条"}'
echo "清单：$MAN"
