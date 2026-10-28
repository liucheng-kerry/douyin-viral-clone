#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""台词语义对照表机械校验（阶段 2 用）

只查**形式**，不查语义：句数对不对、字数偏差会不会撑破口型、时间码有没有叠、
判定列全不全、原句是不是真出自 audio.txt。
"意思有没有跑"必须人读——脚本 pass 不等于意思对。

用法：
    python scripts/dialogue_check.py --map "02-脚本/dialogue-map.md" \
        --audio "01-拆解/audio.txt" [--out "02-脚本/dialogue-check.md"]

退出码：0 = 无 error（可能有 warn）；1 = 有 error，不许进阶段 3。
"""

from __future__ import print_function

import argparse
import io
import os
import re
import sys

VALID_VERDICT = ("✅", "⚠️", "❌")
PUNCT = "，。！？、；：,.!?;: \t\"'“”‘’（）()【】[]…—-~～「」"


def read_text(path):
    with io.open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def clean(s):
    """去标点空白，用于字数统计与相似度比较。"""
    return "".join(ch for ch in s if ch not in PUNCT)


def parse_time(s):
    """'0.2' / '0.2s' / '2.5秒' / '00:02.5' / '1:05' -> float 秒。失败返回 None。"""
    s = s.strip().replace("秒", "").replace("s", "").strip()
    if not s:
        return None
    m = re.match(r"^(\d+):(\d+(?:\.\d+)?)$", s)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))
    try:
        return float(s)
    except ValueError:
        return None


def split_row(line):
    """把一行 markdown 表格拆成单元格。"""
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def is_sep_row(cells):
    return all(re.match(r"^:?-{2,}:?$", c.replace(" ", "")) for c in cells if c)


def parse_map(text):
    """返回 (rows, header)。rows = [{...}]"""
    rows = []
    header = None
    for raw in text.splitlines():
        if "|" not in raw:
            continue
        cells = split_row(raw)
        if is_sep_row(cells):
            continue
        if header is None:
            header = cells
            continue
        if len(cells) < 4:
            continue
        # 列序固定：# | 时间码 | 原片台词 | 改写台词 | 改了什么 | 判定
        def cell(i):
            return cells[i] if i < len(cells) else ""

        rows.append({
            "raw": raw.strip(),
            "no": cell(0),
            "time_raw": cell(1),
            "orig": cell(2),
            "rewrite": cell(3),
            "change": cell(4),
            "verdict": cell(5) if len(cells) > 5 else "",
            "ncell": len(cells),
        })
    return rows, header


def parse_tier(text):
    """从文件里读「档位：A/B/C」，没有则返回 None。"""
    m = re.search(r"档位[：:]\s*([ABCabc])", text)
    return m.group(1).upper() if m else None


def parse_audio(path):
    """audio.txt 每行一句；返回原句列表（已去空行）。"""
    if not path or not os.path.isfile(path):
        return None
    lines = [l.strip() for l in read_text(path).splitlines()]
    return [l for l in lines if l]


def sim(a, b):
    """粗糙相似度：clean 后取字符级编辑距离的归一化。"""
    a, b = clean(a), clean(b)
    if not a or not b:
        return 0.0
    la, lb = len(a), len(b)
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * lb
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    dist = prev[lb]
    return 1.0 - dist / float(max(la, lb))


def main():
    ap = argparse.ArgumentParser(description="台词语义对照表机械校验（只查形式，不查语义）")
    ap.add_argument("--map", required=True, help="02-脚本/dialogue-map.md")
    ap.add_argument("--audio", default="", help="01-拆解/audio.txt（用于核对原句是否真实存在）")
    ap.add_argument("--out", default="", help="报告输出路径（.md）；不填只打印")
    ap.add_argument("--max-delta", type=float, default=0.30, help="字数偏差上限，默认 0.30")
    ap.add_argument("--max-len", type=int, default=12, help="单句字数上限，默认 12")
    ap.add_argument("--min-sim", type=float, default=0.55, help="原句与 audio.txt 的最低相似度，默认 0.55")
    args = ap.parse_args()

    errors, warns, infos = [], [], []

    if not os.path.isfile(args.map):
        sys.exit("对照表不存在：%s" % args.map)

    raw_text = read_text(args.map)
    rows, header = parse_map(raw_text)
    tier = parse_tier(raw_text)
    if not rows:
        sys.exit("在 %s 里没解析到表格行。格式必须为：\n"
                 "| # | 时间码 | 原片台词 | 改写台词 | 改了什么 | 判定 |" % args.map)

    audio = parse_audio(args.audio)

    # ---- 逐行检查
    prev_t = None
    for r in rows:
        tag = "#%s" % (r["no"] or "?")
        if r["ncell"] < 6:
            errors.append("%s 列数只有 %d，规定 6 列（#/时间码/原片台词/改写台词/改了什么/判定）"
                          % (tag, r["ncell"]))
        if not r["orig"]:
            errors.append("%s 原片台词列为空——不能凭印象写，必须从 audio.txt 抄原句" % tag)
        if not r["rewrite"]:
            errors.append("%s 改写台词列为空" % tag)
        if not r["change"]:
            infos.append("%s 「改了什么」为空——建议写一句（如「量词：根→条」），审稿时一眼能看懂" % tag)

        v = r["verdict"].strip()
        if not v:
            errors.append("%s 判定列为空，必须填 ✅ / ⚠️ / ❌" % tag)
        elif not any(v.startswith(x) for x in VALID_VERDICT):
            errors.append("%s 判定列写着「%s」，只能是 ✅ / ⚠️ / ❌" % (tag, v))

        t = parse_time(r["time_raw"])
        if t is None:
            errors.append("%s 时间码「%s」解析不出来，写秒数（如 0.4）" % (tag, r["time_raw"]))
        else:
            if prev_t is not None and t < prev_t:
                errors.append("%s 时间码 %.2fs 比上一句的 %.2fs 还早，顺序反了"
                              % (tag, t, prev_t))
            elif prev_t is not None and abs(t - prev_t) < 0.4:
                warns.append("%s 与上一句只隔 %.2fs，两句会叠在一起、口型对不上"
                             % (tag, t - prev_t))
            prev_t = t

        if r["orig"] and r["rewrite"]:
            lo, lr = len(clean(r["orig"])), len(clean(r["rewrite"]))
            if lo:
                delta = (lr - lo) / float(lo)
                if abs(delta) > args.max_delta:
                    warns.append("%s 字数差 %+.0f%%（原 %d 字 → 改写 %d 字），超 %.0f%% 线："
                                 "段落时长固定，太长会挤、太短会提前说完口型空转"
                                 % (tag, delta * 100, lo, lr, args.max_delta * 100))
            if lr > args.max_len:
                warns.append("%s 改写台词 %d 字，超 %d 字线：拆成两句或换短说法（改完要重跑本脚本）"
                             % (tag, lr, args.max_len))
            if clean(r["orig"]) == clean(r["rewrite"]):
                infos.append("%s 未改动（档 A 允许；若是档 B/C 该改而未改，检查一下）" % tag)

        # 原句是否真出自 audio.txt
        if audio and r["orig"]:
            best = max(sim(r["orig"], a) for a in audio)
            if best < args.min_sim:
                errors.append("%s 原片台词在 audio.txt 里找不到近似句（最高相似度 %.2f）："
                              "可能记错了原句，回 audio.txt 核对" % (tag, best))

    # ---- 全局检查
    if audio:
        if len(rows) != len(audio):
            warns.append("对照表 %d 句 vs 原片转写 %d 行：句数不一致。"
                         "拆分/合并句子会改变节奏与落点，必须让用户确认" % (len(rows), len(audio)))

    n_ok = sum(1 for r in rows if r["verdict"].strip().startswith("✅"))
    n_warn = sum(1 for r in rows if r["verdict"].strip().startswith("⚠"))
    n_bad = sum(1 for r in rows if r["verdict"].strip().startswith("❌"))

    if n_bad:
        bad = [r["no"] for r in rows if r["verdict"].strip().startswith("❌")]
        errors.append("有 %d 句判定为 ❌（第 %s 句）：回阶段 2 重写，不许带病进阶段 3"
                      % (n_bad, "、".join(bad)))
    if n_warn:
        warns.append("有 %d 句判定为 ⚠️ 需确认：必须逐句向用户点名说明改了什么，"
                     "不许在报告里一句「已确认」糊过去" % n_warn)
    if tier is None:
        warns.append("对照表没声明「档位：A/B/C」——在文件第二行写一行（如「档位：B（主角替换）」），"
                     "否则无法校验 ⚠️ 是否越权")
    elif tier != "C" and n_warn:
        errors.append("档位 %s 却有 %d 句 ⚠️：只有档 C（只借结构）允许 ⚠️。"
                      "A/B 档出现 ⚠️ 就是意思跑偏，回阶段 2 重写" % (tier, n_warn))
    if tier == "C":
        infos.append("档位 C：⚠️ 行必须逐句向用户点名，用户点头后才能进阶段 3")

    # ---- 输出
    L = []
    L.append("# 台词对照机械校验报告\n")
    L.append("对照表：`%s`　｜　原片转写：`%s`\n" % (args.map, args.audio or "（未提供，跳过原句核对）"))
    L.append("改写档位：**%s**\n" % (tier or "未声明（脚本无法校验 ⚠️ 是否越权）"))
    L.append("| 项 | 数量 |\n|---|---|")
    L.append("| 台词句数 | %d |" % len(rows))
    L.append("| 判定 ✅ 语义一致 | %d |" % n_ok)
    L.append("| 判定 ⚠️ 需确认 | %d |" % n_warn)
    L.append("| 判定 ❌ 不一致 | %d |" % n_bad)
    L.append("| error | %d |" % len(errors))
    L.append("| warn | %d |" % len(warns))
    L.append("")

    def block(title, items, empty="（无）"):
        L.append("## %s\n" % title)
        if items:
            for it in items:
                L.append("- %s" % it)
        else:
            L.append(empty)
        L.append("")

    block("❌ 必须修（error）", errors)
    block("⚠️ 建议检查（warn）", warns)
    block("ℹ️ 提示（info）", infos)

    L.append("## 本脚本查不到的部分（必须人读）\n")
    L.append("- 信息点有没有增减（脚本只看字数，看不出「多了一句承诺」）")
    L.append("- 意图有没有变（请求↔命令、宣布↔疑问）")
    L.append("- 情绪强度有没有换档（得意↔卑微）")
    L.append("- 人称与话语权、以及「这句在原片里干的是不是同一件事」")
    L.append("")

    report = "\n".join(L)
    if args.out:
        d = os.path.dirname(args.out)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with io.open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        print("报告已写出：%s" % args.out)

    print("句数 %d（✅%d / ⚠️%d / ❌%d）｜ error %d ｜ warn %d"
          % (len(rows), n_ok, n_warn, n_bad, len(errors), len(warns)))
    if errors:
        print("\n必须修：")
        for e in errors:
            print("  ❌ " + e)
    if warns:
        print("\n建议检查：")
        for w in warns:
            print("  ⚠️ " + w)

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
