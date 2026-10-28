#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
style_anchor.py —— 从对标视频里量化出「风格锚」（本技能阶段 1.6）

为什么需要它：内容拆解得再细，只要色调/质感/光线不对，成片一眼就假。
这个脚本把原片的视觉风格变成**可执行、可质检的数据**，而不是形容词。

零第三方依赖（纯标准库 + ffmpeg/ffprobe）：
  1. 全片均匀抽 N 帧（等比缩到 96px 宽的 rawvideo，直接读字节）
  2. 逐帧统计：平均亮度 / 对比度 / 饱和度 / 色温 / 主色板 / 锐度与颗粒 /
     高光溢出比 / 暗部闷死比 / 主色相分布
  3. 规则引擎把数值翻成可直接粘贴的「风格句」+「负向风格词」
  4. 产出 style-anchor.md（数据表 + 风格句 + 质检三问）与 style-swatch.svg（主色板可视化）

用法：
  python scripts/style_anchor.py --video 01-拆解/video/对标.mp4 --out 01-拆解
  python scripts/style_anchor.py --video ... --out ... --frames 14 --width 128

脚本只给"数值事实"。画风定性（实拍/3D/动画/黏土…）必须由 agent 用 ViewImage
看帧后补进 style-anchor.md 的"画风判定"栏，否则风格句不完整。
"""
import argparse
import colorsys
import json
import os
import shutil
import subprocess
import sys

# ---------------------------------------------------------------- ffmpeg 定位


def _runs(exe):
    """候选可执行文件必须真的能跑起来——WinGet 的 Links\\ffmpeg.EXE 是 0 字节别名壳，
    shutil.which 会返回它，直接调用会报 WinError 193。"""
    try:
        if not exe or not os.path.exists(exe) or os.path.getsize(exe) == 0:
            return False
        res = subprocess.run([exe, "-version"], capture_output=True, timeout=20)
        return res.returncode == 0
    except Exception:
        return False


def find_bin(name):
    for cand in (shutil.which(name), shutil.which(name + ".exe")):
        if _runs(cand):
            return cand
    local = os.environ.get("LOCALAPPDATA", "")
    base = os.path.join(local, "Microsoft", "WinGet", "Packages")
    if os.path.isdir(base):
        for root, _dirs, files in os.walk(base):
            for fn in (name + ".exe", name):
                if fn in files:
                    cand = os.path.join(root, fn)
                    if _runs(cand):
                        return cand
    return None


# ---------------------------------------------------------------- 视频信息


def probe(video):
    ffprobe = find_bin("ffprobe")
    if not ffprobe:
        sys.exit("找不到 ffprobe，先跑 scripts/check-env.sh 装依赖")
    cmd = [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
           "stream=width,height,r_frame_rate:format=duration", "-of", "json", video]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0:
        sys.exit("ffprobe 读取失败：%s" % res.stderr.decode("utf-8", "ignore")[:500])
    data = json.loads(res.stdout.decode("utf-8", "ignore") or "{}")
    st = (data.get("streams") or [{}])[0]
    dur = float(data.get("format", {}).get("duration") or 0)
    return int(st.get("width") or 0), int(st.get("height") or 0), dur


# ---------------------------------------------------------------- 抽帧读像素


def grab_frames(video, count, width):
    """均匀抽 count 帧，等比缩到 width 宽，返回 (帧字节列表, 输出宽, 输出高, 时长)。

    也支持静态图片输入（生成图质检时对照用）：时长缺失就只抽 1 帧。
    """
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        sys.exit("找不到 ffmpeg，先跑 scripts/check-env.sh 装依赖")
    w, h, dur = probe(video)
    if not w or not h:
        sys.exit("媒体信息异常：%dx%d" % (w, h))
    out_w = max(32, min(width, w))
    out_h = int(round(h * out_w / float(w) / 2.0)) * 2  # 保持偶数
    if dur and dur > 0:
        fps = max(count / dur, 0.02)
        vf = "fps=%.6f,scale=%d:%d" % (fps, out_w, out_h)
        extra = []
    else:
        vf = "scale=%d:%d" % (out_w, out_h)   # 静态图片
        extra = ["-frames:v", "1"]
    cmd = [ffmpeg, "-v", "error", "-i", video, "-vf", vf] + extra + [
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0:
        sys.exit("ffmpeg 抽帧失败：%s" % res.stderr.decode("utf-8", "ignore")[:500])
    frame_size = out_w * out_h * 3
    blob = res.stdout
    frames = [blob[i:i + frame_size] for i in range(0, len(blob) - frame_size + 1, frame_size)]
    if not frames:
        sys.exit("抽帧结果为空，检查文件是否可解码")
    return frames[:max(1, count)], out_w, out_h, dur


# ---------------------------------------------------------------- 像素统计


def analyze_frame(buf, w, h):
    n = w * h
    sum_y = 0
    sum_y2 = 0
    sum_r = sum_g = sum_b = 0
    sat_sum = 0.0
    hi = lo = 0
    hue_bins = [0] * 12
    buckets = {}
    ys = [0.0] * n          # 先存亮度，第二遍算邻域差（锐度/颗粒）

    for i in range(n):
        o = i * 3
        r, g, b = buf[o], buf[o + 1], buf[o + 2]
        sum_r += r
        sum_g += g
        sum_b += b
        y = 0.299 * r + 0.587 * g + 0.114 * b
        ys[i] = y
        sum_y += y
        sum_y2 += y * y
        if y > 235:
            hi += 1
        elif y < 20:
            lo += 1
        mx = max(r, g, b)
        mn = min(r, g, b)
        sat = 0.0 if mx == 0 else (mx - mn) / float(mx)
        sat_sum += sat
        if sat > 0.15 and mx > 24:
            hh = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)[0] * 360.0
            hue_bins[min(11, int(hh // 30))] += 1
        # 主色板：每通道量化到 5bit 分桶，累加真实均值
        key = ((r >> 3) << 10) | ((g >> 3) << 5) | (b >> 3)
        acc = buckets.get(key)
        if acc is None:
            buckets[key] = [1, r, g, b]
        else:
            acc[0] += 1
            acc[1] += r
            acc[2] += g
            acc[3] += b

    # 锐度/颗粒：与左邻、上邻的亮度差绝对均值
    edge = 0.0
    pairs = 0
    for i in range(n):
        if i % w:
            edge += abs(ys[i] - ys[i - 1])
            pairs += 1
        if i >= w:
            edge += abs(ys[i] - ys[i - w])
            pairs += 1

    mean_y = sum_y / n
    var = max(0.0, sum_y2 / n - mean_y * mean_y)
    return {
        "mean_y": mean_y,
        "contrast": var ** 0.5,
        "sat": sat_sum / n,
        "r": sum_r / n,
        "g": sum_g / n,
        "b": sum_b / n,
        "hi": hi / float(n),
        "lo": lo / float(n),
        "edge": edge / float(max(1, pairs)),
        "hue_bins": hue_bins,
        "buckets": buckets,
    }


def merge(stats, top_colors=6):
    n = len(stats)
    out = {
        "mean_y": sum(s["mean_y"] for s in stats) / n,
        "contrast": sum(s["contrast"] for s in stats) / n,
        "sat": sum(s["sat"] for s in stats) / n,
        "r": sum(s["r"] for s in stats) / n,
        "g": sum(s["g"] for s in stats) / n,
        "b": sum(s["b"] for s in stats) / n,
        "hi": sum(s["hi"] for s in stats) / n,
        "lo": sum(s["lo"] for s in stats) / n,
        "edge": sum(s["edge"] for s in stats) / n,
    }
    hue = [0] * 12
    for s in stats:
        for i, v in enumerate(s["hue_bins"]):
            hue[i] += v
    out["hue"] = hue
    # 合并所有帧的色桶
    total = {}
    for s in stats:
        for k, v in s["buckets"].items():
            acc = total.get(k)
            if acc is None:
                total[k] = list(v)
            else:
                for j in range(4):
                    acc[j] += v[j]
    pixels = sum(v[0] for v in total.values()) or 1
    ranked = sorted(total.values(), key=lambda x: -x[0])
    colors = []
    for cnt, sr, sg, sb in ranked:
        c = (int(sr / cnt), int(sg / cnt), int(sb / cnt))
        colors.append({"hex": "#%02X%02X%02X" % c, "rgb": c, "pct": cnt / float(pixels)})
    # 合并近似色（欧氏距离 < 28 视为同色）
    merged = []
    for c in colors:
        for m in merged:
            if sum((a - b) ** 2 for a, b in zip(c["rgb"], m["rgb"])) ** 0.5 < 28:
                m["pct"] += c["pct"]
                break
        else:
            merged.append(dict(c))
    merged.sort(key=lambda x: -x["pct"])
    out["colors"] = merged[:top_colors]
    return out


# ---------------------------------------------------------------- 颜色命名

HUE_NAMES = ["红", "橙", "黄", "黄绿", "绿", "青绿", "青", "蓝", "蓝紫", "紫", "品红", "粉红"]


def color_name(rgb):
    r, g, b = [v / 255.0 for v in rgb]
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    hd = h * 360.0
    if s < 0.10:
        if v > 0.86:
            return "白/高光"
        if v < 0.16:
            return "黑/暗部"
        return "灰"
    if v < 0.12:
        return "近黑"
    # 棕/褐：暖色相 + 中低明度——夜市、木头、食物、皮肤最常见的那一族
    if (hd <= 48 or hd >= 352) and s > 0.14 and v < 0.78:
        if v < 0.22:
            return "暗褐"
        if v < 0.38:
            return "深褐"
        if v < 0.55:
            return "棕"
        return "浅棕" if v < 0.68 else "米棕"
    if v < 0.22:
        return "深灰" if s < 0.30 else ("暗" + HUE_NAMES[min(11, int(hd // 30))])
    # 低饱和带冷暖倾向的灰
    if s < 0.16:
        if r > b + 0.04:
            return "暖灰"
        if b > r + 0.04:
            return "冷灰"
        return "灰"
    nm = HUE_NAMES[min(11, int(hd // 30))]
    if s > 0.55 and v > 0.75:
        return "亮" + nm
    if s < 0.28:
        return "灰" + nm
    if v > 0.7:
        return "明" + nm
    return nm


def dominant_hue(hue_bins):
    total = sum(hue_bins) or 1
    idx = sorted(range(12), key=lambda i: -hue_bins[i])[:3]
    return [(HUE_NAMES[i], hue_bins[i] / float(total)) for i in idx if hue_bins[i] > 0]


# ---------------------------------------------------------------- 规则引擎


def verdict(a):
    """把统计值翻成人话。阈值来自实拍/短视频的常见分布区间。"""
    ly = a["mean_y"]
    if ly < 70:
        tone = "低调（画面偏暗，大量暗部）"
    elif ly < 145:
        tone = "中间调（曝光常规）"
    else:
        tone = "高调（画面明亮，白多）"

    c = a["contrast"]
    if c < 34:
        cband = "低对比（发灰、软）"
    elif c < 58:
        cband = "中对比"
    else:
        cband = "高对比（硬朗、黑白分明）"

    s = a["sat"]
    if s < 0.20:
        sband = "低饱和（灰调、几乎无彩色）"
    elif s < 0.32:
        sband = "中饱和（自然）"
    elif s < 0.46:
        sband = "饱和偏高（色彩鲜明）"
    else:
        sband = "高饱和（浓艳、网感强）"

    d = a["r"] - a["b"]
    if d > 14:
        temp = "暖调（明显偏黄橙）"
    elif d > 5:
        temp = "偏暖"
    elif d < -14:
        temp = "冷调（明显偏青蓝）"
    elif d < -5:
        temp = "偏冷"
    else:
        temp = "中性色温"

    e = a["edge"]
    if e < 5.0:
        eband = "极柔和（浅景深/虚化重/低清）"
    elif e < 9.0:
        eband = "柔和（手机景深或轻微虚化）"
    elif e < 14.0:
        eband = "清晰（常规锐度）"
    else:
        eband = "锐利且颗粒感强（高锐化/噪点/强纹理）"

    expo = []
    if a["hi"] > 0.06:
        expo.append("高光溢出偏多（%.0f%% 像素接近纯白）" % (a["hi"] * 100))
    if a["lo"] > 0.16:
        expo.append("暗部压得重（%.0f%% 像素接近纯黑）" % (a["lo"] * 100))
    if not expo:
        expo.append("曝光分布正常，无明显死白死黑")

    return {"tone": tone, "contrast": cband, "sat": sband, "temp": temp,
            "edge": eband, "expo": expo}


def style_string(a, v, hues, style_word=""):
    """拼一句可直接粘进每张图片/视频提示词的风格句。"""
    parts = [style_word or "（待补画风：实拍/3D渲染/2D动画…）"]
    parts.append(v["temp"].split("（")[0])
    parts.append(v["sat"].split("（")[0])
    parts.append(v["contrast"].split("（")[0])
    parts.append(v["tone"].split("（")[0])
    parts.append(v["edge"].split("（")[0])
    top = a["colors"][:3]
    if top:
        names = []
        for c in top:
            nm = color_name(c["rgb"])
            if nm not in names:
                names.append(nm)
        if names:
            parts.append("%s为主色调" % "、".join(names[:2]))
        parts.append("主色 %s" % " / ".join("%s（%s）" % (c["hex"], color_name(c["rgb"])) for c in top))
    elif hues:
        parts.append("%s为主色调" % "、".join(h for h, _ in hues[:2]))
    return "，".join(parts)


def negative_string(a, v):
    """按实测偏离生成负向词——只否掉原片明确没有的东西。"""
    neg = []
    if a["sat"] < 0.32:
        neg.append("浓艳高饱和")
    if a["r"] - a["b"] < 5:
        neg.append("暖黄滤镜")
    if a["r"] - a["b"] > 5:
        neg.append("冷青色调")
    if a["contrast"] < 58:
        neg.append("强对比硬光")
    if a["contrast"] > 34:
        neg.append("灰蒙蒙低对比")
    if a["edge"] < 9:
        neg.append("锐利商业片质感")
    neg.append("电影感调色")
    neg.append("3D 塑料感/过曝磨皮")
    return "、".join(neg)


# ---------------------------------------------------------------- 对照质检


def compare_report(base, other, base_name, other_name):
    """把「感觉不一样」变成数字：色温/饱和/对比/亮度/锐度的差值 + 判定。"""
    rows = []

    def band(delta, ok, mild):
        a = abs(delta)
        return "✅ 同族" if a <= ok else ("⚠️ 轻微偏" if a <= mild else "❌ 明显偏")

    d = (other["r"] - other["b"]) - (base["r"] - base["b"])
    rows.append(("色温 Δ(R−B)", "%+.1f" % d, band(d, 12, 25),
                 "生成图偏暖" if d > 0 else ("生成图偏冷" if d < 0 else "一致")))
    d = other["sat"] - base["sat"]
    rows.append(("饱和度 Δ", "%+.3f" % d, band(d, 0.06, 0.12),
                 "生成图更艳" if d > 0 else ("生成图更灰" if d < 0 else "一致")))
    d = other["contrast"] - base["contrast"]
    rows.append(("对比度 Δ", "%+.1f" % d, band(d, 8, 15),
                 "生成图更硬" if d > 0 else ("生成图更平" if d < 0 else "一致")))
    d = other["mean_y"] - base["mean_y"]
    rows.append(("亮度 Δ", "%+.1f" % d, band(d, 12, 25),
                 "生成图更亮" if d > 0 else ("生成图更暗" if d < 0 else "一致")))
    ratio = (other["edge"] / base["edge"]) if base["edge"] else 1.0
    ok = 0.7 <= ratio <= 1.4
    rows.append(("锐度比", "%.2fx" % ratio, "✅ 同档" if ok else "⚠️ 不同档",
                 "生成图更锐/颗粒更重" if ratio > 1 else "生成图更糊/更干净"))
    bad = sum(1 for r in rows if r[2].startswith("❌"))
    warn = sum(1 for r in rows if r[2].startswith("⚠️"))
    if bad:
        concl = "❌ 不同族：有 %d 项明显偏离，必须改提示词重生成" % bad
    elif warn:
        concl = "⚠️ 边缘：有 %d 项轻微偏离，可接受但建议微调风格句" % warn
    else:
        concl = "✅ 同族：五项全部在容差内"

    md = []
    md.append("## 七、对照质检（%s vs %s）\n" % (other_name, base_name))
    md.append("| 指标 | 差值 | 判定 | 说明 |\n|---|---|---|---|")
    for name, val, jd, note in rows:
        md.append("| %s | %s | %s | %s |" % (name, val, jd, note))
    md.append("")
    md.append("**结论**：%s\n" % concl)
    md.append("容差：色温 ±12、饱和 ±0.06、对比 ±8、亮度 ±12、锐度比 0.7–1.4。（超 2 倍容差记「明显偏」，需重写风格句而不是重抽图片。）\n")

    print("=== 对照质检：%s vs %s ===" % (other_name, base_name))
    for name, val, jd, note in rows:
        print("  %-12s %-9s %-8s %s" % (name, val, jd, note))
    print("  → %s" % concl)
    return md, concl


# ---------------------------------------------------------------- 输出


def write_svg(path, colors, w_total=680, h=140):
    blocks = []
    x = 0.0
    n = max(1, len(colors))
    for c in colors:
        bw = w_total * (c["pct"] if c["pct"] > 0.06 else 0.06)
        blocks.append((x, bw, c))
        x += bw
    scale = w_total / max(x, 1.0)
    parts = []
    x = 0.0
    for bx, bw, c in blocks:
        bw *= scale
        lum = 0.299 * c["rgb"][0] + 0.587 * c["rgb"][1] + 0.114 * c["rgb"][2]
        fg = "#111111" if lum > 145 else "#FFFFFF"
        parts.append('<rect x="%.1f" y="0" width="%.1f" height="%d" fill="%s"/>' % (x, bw, h, c["hex"]))
        if bw > 74:
            parts.append('<text x="%.1f" y="%d" font-family="monospace" font-size="12" fill="%s">%s</text>'
                         % (x + 8, h - 24, fg, c["hex"].lstrip("#")))
            parts.append('<text x="%.1f" y="%d" font-family="sans-serif" font-size="12" fill="%s">%.0f%%</text>'
                         % (x + 8, h - 8, fg, c["pct"] * 100))
        x += bw
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d">'
           '%s</svg>' % (w_total, h, w_total, h, "".join(parts)))
    with open(path, "w", encoding="utf-8") as f:
        f.write(svg)


def main():
    ap = argparse.ArgumentParser(description="从对标视频量化出风格锚")
    ap.add_argument("--video", required=True, help="对标视频路径")
    ap.add_argument("--out", required=True, help="输出目录（一般是 01-拆解）")
    ap.add_argument("--frames", type=int, default=12, help="抽帧数量，默认 12")
    ap.add_argument("--width", type=int, default=96, help="分析用缩略宽度，默认 96")
    ap.add_argument("--style-word", default="", help="画风短语（实拍手机随手拍/3D渲染/2D动画…）")
    ap.add_argument("--compare", default="", help="对照文件（生成的图片或视频），输出风格差异质检报告")
    ap.add_argument("--qc-only", action="store_true",
                    help="只出对照质检报告 qc-report.md（基准传原片同秒帧、对照传生成图），不覆盖 style-anchor.md")
    args = ap.parse_args()

    if args.qc_only and not args.compare:
        sys.exit("--qc-only 需要同时指定 --compare（基准=原片同秒帧，对照=生成的图）")

    if not os.path.isfile(args.video):
        sys.exit("视频不存在：%s" % args.video)
    os.makedirs(args.out, exist_ok=True)

    frames, w, h, dur = grab_frames(args.video, args.frames, args.width)
    stats = [analyze_frame(f, w, h) for f in frames]
    a = merge(stats)
    v = verdict(a)
    hues = dominant_hue(a["hue"])
    sstr = style_string(a, v, hues, args.style_word)
    nstr = negative_string(a, v)

    cmp_md, cmp_concl = [], ""
    if args.compare:
        if not os.path.isfile(args.compare):
            sys.exit("对照文件不存在：%s" % args.compare)
        cframes, cw, ch, _ = grab_frames(args.compare, args.frames, args.width)
        b = merge([analyze_frame(f, cw, ch) for f in cframes])
        cmp_md, cmp_concl = compare_report(a, b, os.path.basename(args.video),
                                           os.path.basename(args.compare))

    md = os.path.join(args.out, "style-anchor.md")
    svg = os.path.join(args.out, "style-swatch.svg")

    # 质检模式：只出对照报告，不动 style-anchor.md（基准=原片同秒帧，对照=生成图）
    if args.qc_only:
        head = ["# 风格质检报告\n",
                "基准：`%s`（原片同秒帧）　｜　对照：`%s`（生成结果）\n" % (
                    os.path.basename(args.video), os.path.basename(args.compare))]
        qc_path = os.path.join(args.out, "qc-report.md")
        with open(qc_path, "w", encoding="utf-8") as f:
            f.write("\n".join(head + cmp_md))
        print("已写出：%s" % qc_path)
        return

    write_svg(svg, a["colors"])

    lines = []
    lines.append("# 风格锚（style-anchor）\n")
    lines.append("> 由 `scripts/style_anchor.py` 从对标视频抽 %d 帧统计得出，是阶段 3 图片提示词与阶段 4 视频提示词的**硬性风格前缀**。\n" % len(frames))
    lines.append("源视频：`%s`　｜　%.2fs　｜　采样 %d 帧\n" % (os.path.basename(args.video), dur, len(frames)))
    lines.append("## 一、风格句（原样粘进每条提示词）\n")
    lines.append("```text\n%s\n```\n" % sstr)
    lines.append("## 二、负向风格词（原样粘在提示词末尾的禁止项）\n")
    lines.append("```text\n避免：%s\n```\n" % nstr)
    lines.append("## 三、量化数据\n")
    lines.append("| 指标 | 数值 | 判读 |\n|---|---|---|")
    lines.append("| 平均亮度 | %.1f / 255 | %s |" % (a["mean_y"], v["tone"]))
    lines.append("| 对比度（亮度标准差） | %.1f | %s |" % (a["contrast"], v["contrast"]))
    lines.append("| 平均饱和度 | %.3f | %s |" % (a["sat"], v["sat"]))
    lines.append("| 色温（R−B） | %+.1f | %s |" % (a["r"] - a["b"], v["temp"]))
    lines.append("| 锐度/颗粒（邻域亮度差） | %.2f | %s |" % (a["edge"], v["edge"]))
    lines.append("| 高光溢出比 | %.1f%% | %s |" % (a["hi"] * 100, v["expo"][0]))
    if len(v["expo"]) > 1:
        lines.append("| 暗部占比 | %.1f%% | %s |" % (a["lo"] * 100, v["expo"][1]))
    else:
        lines.append("| 暗部占比 | %.1f%% | —— |" % (a["lo"] * 100))
    if hues:
        lines.append("| 主色相 | %s | 画面色彩倾向 |" % "、".join("%s %.0f%%" % (n, p * 100) for n, p in hues))
    lines.append("")
    lines.append("## 四、主色板（`style-swatch.svg` 可视化）\n")
    lines.append("| 序 | 色值 | 名称 | 占比 |\n|---|---|---|---|")
    for i, c in enumerate(a["colors"], 1):
        lines.append("| %d | `%s` | %s | %.1f%% |" % (i, c["hex"], color_name(c["rgb"]), c["pct"] * 100))
    lines.append("")
    lines.append("## 五、画风判定（agent 必填：用 ViewImage 看帧后补）\n")
    lines.append("| 判定项 | 结论 |\n|---|---|")
    lines.append("| 画风 | ⬜ 待填（实拍手机随手拍 / 实拍精致打光 / 3D 渲染 / 2D 动画 / 黏土定格 / 拼贴 / 混合） |")
    lines.append("| 镜头质感 | ⬜ 待填（手机广角直拍 / 单反浅景深 / 长焦压缩 / 微距） |")
    lines.append("| 光源 | ⬜ 待填（自然日光 / 夜市暖光 / 室内顶光 / 霓虹混色 / 逆光轮廓 / 屏幕光） |")
    lines.append("| 与数值是否矛盾 | ⬜ 待填（矛盾时以肉眼判断为准，并说明原因） |")
    lines.append("")
    lines.append("## 六、用法（阶段 3/4 的硬规则）\n")
    lines.append("1. **阶段 3 每一张图片提示词** = 内容描述 + `风格句` + `避免：负向词`，三段缺一不可。")
    lines.append("2. **阶段 4 每一段视频提示词的【影像基调】** 照抄风格句，不许自己另写形容词（写「电影感/4K」直接判不合格）。")
    lines.append("3. **生成后质检三问**（对着原片同秒帧看）：")
    lines.append("   - 色调：冷暖、饱和度、主色是否同一族？（色差明显 → 重写）")
    lines.append("   - 质感：锐度/景深/噪点是否同一档？（生成的太干净或太糊 → 重写）")
    lines.append("   - 光线：光源方向与软硬是否一致？（光位不对 → 重写）")
    lines.append("4. 三问全过才算资产合格；不过就改风格句重生成，不要靠「多试几张」碰运气。")
    lines.append("")
    if cmp_md:
        lines.extend(cmp_md)
    with open(md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # 控制台回显（agent 直接把这段报给学员）
    print("=== 风格锚 ===")
    print("风格句：%s" % sstr)
    print("负向词：%s" % nstr)
    print("亮度 %.1f ｜ 对比 %.1f ｜ 饱和 %.3f ｜ 色温 R-B %+.1f ｜ 锐度 %.2f"
          % (a["mean_y"], a["contrast"], a["sat"], a["r"] - a["b"], a["edge"]))
    print("主色板：" + " / ".join("%s %s %.0f%%" % (c["hex"], color_name(c["rgb"]), c["pct"] * 100)
                                 for c in a["colors"]))
    print("已写出：%s" % md)
    print("已写出：%s" % svg)
    print("⚠️ 别忘了用 ViewImage 看帧补「画风判定」三行，再拼最终风格句。")


if __name__ == "__main__":
    main()
