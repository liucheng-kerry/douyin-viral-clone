#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""quote.py —— 生成报价（实测单价反推 + 传/不传参考视频两档对比；零第三方依赖）

为什么需要这个脚本：
  申请单里要写"预计消耗 XX 积分"，但模型给的数字经常是凭印象编的。即梦每次生成都会在
  任务记录里留下 commerce_info.credit_count（真实扣了多少）和 benefit_type（走了哪个计费 SKU），
  拿它反推单价，报价就是实测的，不是估的。

  而 benefit_type 里那句 no_input_video 说明了一件关键的事：**传不传参考视频走的是不同计费档**。
  所以「这段要不要传参考视频」不能凭感觉，得把两档的价格和效果差摆给用户选。

用法：
  # 本段报价（6 秒输出 + 6 秒参考片段，720p）
  python scripts/quote.py --duration 6 --resolution 720p --ref 6

  # 只看实测单价表
  python scripts/quote.py --rate-only

  # 生成完一段带参考视频的片子后，读回它的真实单价（校准后续报价）
  python scripts/quote.py --calibrate

  # 用保存下来的任务清单报价（CLI 不可用时）
  dreamina list_task > /tmp/tasks.json
  python scripts/quote.py --from tasks.json --duration 6 --ref 6

计费规则（本脚本采用的模型）：
  · 无参考视频：积分 = 单价 × 输出时长
  · 有参考视频：积分 = 输入视频档单价 × (参考片段时长 + 输出时长)
    —— 参考片段秒数会被计入计费时长，这是公开资料里几家渠道一致的口径；
       即梦自己的 SKU 名 no_input_video 也印证了"有输入视频"是另一档。
  · 输入视频档单价 < 无参考单价（约 0.55–0.65），但秒数变多，所以：
       参考片段 ≤ 0.54 × 输出时长（按系数 0.65 的保守值；系数 0.55 时可放宽到 0.82）
       → 反而更便宜；  与输出等长 → 贵 10–30%；  比输出更长 → 明显更贵
  · 输入视频档单价**必须**用第一次实测校准：脚本给的是区间，不是承诺。

退出码：0 正常；2 读不到任务清单。
"""

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys

RES_ORDER = ('480p', '720p', '1080p', '4k')
DEFAULT_MODEL = 'seedance2.5'

# 当本地没有「输入视频档」实测样本时的保守系数区间（公开资料口径，待实测校准）
REF_RATE_RANGE = (0.55, 0.65)

# 本账号一条历史任务都没有时（第一次用即梦的新账号）的兜底参考价。
# 这些数字来自同款账号的实测样本，不是本账号实测——所以一律标注"参考价"，
# 且生成第一段后必须跑 --calibrate 换成真实单价。
# 没有它，新学员在阶段 0 就被"报预算"这道门卡死（list_task 是空的）。
REFERENCE_VIDEO_RATE = {'480p': 12.0, '720p': 20.0, '1080p': 30.0}
REFERENCE_IMAGE_RATE = [('5.0 / 2k', 3), ('5.0Pro / 2k', 8)]


# ------------------------------------------------------------------ 取数


def load_tasks(path):
    if path:
        try:
            with io.open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
                return json.load(f)
        except (IOError, OSError):
            sys.exit('✘ 读不到 %s\n   Windows 上 bash 的 /tmp 与 python 的 /tmp 不是同一处，'
                     '传路径请用 Windows 形式（如 C:/Users/你/AppData/Local/Temp/tasks.json）。' % path)
        except ValueError:
            sys.exit('✘ %s 不是有效 JSON。' % path)
    exe = shutil.which('dreamina') or 'dreamina'
    try:
        out = subprocess.run([exe, 'list_task'], capture_output=True, timeout=120)
    except FileNotFoundError:
        sys.exit('✘ 找不到 dreamina 命令。用 --from <tasks.json> 传入 `dreamina list_task` 的保存结果。')
    except subprocess.TimeoutExpired:
        sys.exit('✘ dreamina list_task 超时。')
    txt = (out.stdout or b'').decode('utf-8', 'replace').strip()
    if not txt:
        sys.exit('✘ dreamina list_task 没有输出。先确认 `dreamina user_credit` 能跑通。')
    try:
        return json.loads(txt)
    except ValueError:
        sys.exit('✘ 任务清单不是 JSON。先跑 `dreamina list_task` 看看输出。\n%s' % txt[:300])


def benefit_of(task):
    c = task.get('commerce_info') or {}
    bt = c.get('benefit_type') or ''
    if not bt:
        bt = ' '.join([x.get('benefit_type', '') for x in (c.get('triplets') or [])])
    return c.get('credit_count'), bt


def resolution_of(bt):
    m = re.search(r'(480p|720p|1080p|4k)', bt or '', re.I)
    return m.group(1).lower() if m else ''


def has_input_video(bt):
    """no_input_video 里也含 input_video 子串，所以必须先判 no_。"""
    b = (bt or '').lower()
    if 'no_input_video' in b:
        return False
    return 'input_video' in b or 'with_input_video' in b


def video_duration(task):
    r = task.get('result_json') or {}
    for v in (r.get('videos') or []):
        d = v.get('duration') or v.get('video_duration') or 0
        if d:
            return float(d)
    return 0.0


def billed_seconds(d):
    """产出时长是 4.04s 这种（含首帧偏移），计费按整数秒：贴在整数上的取整，否则原值。"""
    n = int(round(d))
    return float(n) if abs(d - n) <= 0.2 else float(d)


def image_model_of(bt):
    b = (bt or '').lower()
    m = re.search(r'image_basic_v(\d+)(?:_pro)?_(\dk)', b)
    if not m:
        return (bt or '未知')
    ver = m.group(1)
    ver = ver + '.0' if len(ver) == 1 else ver[0] + '.' + ver[1:]
    pro = 'Pro' if '_pro' in b else ''
    return ('%s%s / %s' % (ver, pro, m.group(2))).strip()


def collect(tasks):
    """-> (video_rates, image_credits, ref_samples)

    video_rates: {(resolution, False): [rate,...]}  只有「无输入视频」档能直接反推单价
    image_credits: {model_label: [credits,...]}
    ref_samples: [(submit_id, resolution, credits, output_seconds)]  带参考视频的任务：
                 计费秒数 = 参考片段 + 输出，参考片段秒数不在任务记录里，所以不能直接除。
    """
    video, image, refs = {}, {}, []
    for t in tasks:
        credits, bt = benefit_of(t)
        if not credits:
            continue
        kind = t.get('gen_task_type', '')
        if kind in ('image2image', 'text2image', 'image_upscale'):
            image.setdefault(image_model_of(bt), []).append(int(credits))
            continue
        dur = billed_seconds(video_duration(t))
        if dur <= 0:
            continue
        res = resolution_of(bt) or '未知'
        if has_input_video(bt):
            refs.append((t.get('submit_id', '?'), res, int(credits), dur))
        else:
            video.setdefault((res, False), []).append(float(credits) / dur)
    return video, image, refs


def med(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return 0.0
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


# ------------------------------------------------------------------ 输出


def print_rates(video, image, refs):
    print('=== 实测单价（来自本机历史任务，非估算）===')
    if video:
        print('视频：')
        for res in RES_ORDER + ('未知',):
            for flag in (False, True):
                xs = video.get((res, flag))
                if not xs:
                    continue
                label = '有输入视频' if flag else '无输入视频'
                print('  %-6s %-6s %6.2f 积分/秒（%d 条，区间 %.2f–%.2f）'
                      % (res, label, med(xs), len(xs), min(xs), max(xs)))
    else:
        print('视频：本账号暂无历史样本，下列为**参考价**（同款账号实测经验值，非本账号实测）')
        for res in RES_ORDER:
            if res in REFERENCE_VIDEO_RATE:
                print('  %-6s %-6s %6.2f 积分/秒（参考价，未实测）' % (res, '无输入视频', REFERENCE_VIDEO_RATE[res]))
    if image:
        print('图片：')
        for k in sorted(image):
            xs = image[k]
            print('  %-16s %d 积分/张（%d 条）' % (k, int(med(xs)), len(xs)))
    else:
        print('图片：本账号暂无历史样本，参考价：' + '、'.join('%s %d 积分/张' % (k, v) for k, v in REFERENCE_IMAGE_RATE))
    if refs:
        print('  带参考视频的任务 %d 条：计费秒数含参考片段，单价要用 --calibrate --ref <秒> 反推' % len(refs))
    else:
        print('\n⚠ 本机还没有「带参考视频」的实测样本——带视频档的单价只能给区间，'
              '第一段带参考视频生成后跑一次 --calibrate --ref <参考片段秒数> 校准。')


def quote(video, args):
    res = args.resolution.lower()
    dur = args.duration
    base = video.get((res, False))

    print('\n=== 本段报价：%g 秒输出 / %s / %s ===' % (dur, res, args.model))
    if not base:
        fallback = video.get(('720p', False)) or []
        if fallback and res != '720p':
            print('⚠ 本机没有 %s 档实测，按 720p 单价等比估算——仅供内部判断，不要直接写进申请单' % res)
            base = fallback
        else:
            # 新账号第一次跑：list_task 是空的。不能因此拒绝报价——阶段 0 必须能报预算，
            # 否则学员卡在"先出小样测价"和"先报预算"的死循环里。
            guess = REFERENCE_VIDEO_RATE.get(res) or REFERENCE_VIDEO_RATE['720p']
            base = [guess]
            print('⚠ 本账号还没有视频历史任务，按**参考价 %.0f 积分/秒**估算（同款账号经验值，非本账号实测）' % guess)
            print('   偏差可能 ±30%：第一段生成后立刻跑 `--calibrate` 换成真实单价，再报后面几段')

    unit = med(base)
    no_ref = unit * dur
    print('A. 不传参考视频（只传图 + 文字）：')
    print('   %g 秒 × %.2f = %.0f 积分' % (dur, unit, no_ref))

    if args.ref and args.ref > 0:
        billed = args.ref + dur
        print('B. 传参考视频（@Video1 %gs，计费秒数 = %g + %g = %gs）：'
              % (args.ref, args.ref, dur, billed))
        if args.ref_unit and args.ref_unit > 0:
            with_ref = args.ref_unit * billed
            print('   %g 秒 × %.2f（手填实测单价）= %.0f 积分' % (billed, args.ref_unit, with_ref))
            lo = hi = args.ref_unit
        else:
            lo, hi = args.ref_rate
            a, b = unit * lo * billed, unit * hi * billed
            print('   %g 秒 × %.2f–%.2f（区间估算）= %.0f–%.0f 积分'
                  % (billed, unit * lo, unit * hi, a, b))
            with_ref = (a + b) / 2.0
        diff = with_ref - no_ref
        sign = '+' if diff >= 0 else '−'
        print('   与不传相比：%s%.0f 积分（%s%.0f%%）'
              % (sign, abs(diff), sign, abs(diff) / no_ref * 100))
        if not (args.ref_unit and args.ref_unit > 0):
            print('   ⚠ 输入视频档单价未实测，这是区间。生成后跑 `--calibrate --ref %g` 拿真实单价，'
                  '再用 --ref-unit 重报。' % args.ref)
        tips = sorted(dur * (1.0 / k - 1.0) for k in (lo, hi) if k > 0)
        if tips:
            print('   参考片段长度建议：≤ %.1f 秒时总价不比不传贵（系数 %.2f–%.2f 推算）；'
                  '只切真正需要的 2–6 秒，别整段传' % (tips[0], lo, hi))
    return 0


def calibrate(refs, args):
    print('\n=== 校准：带参考视频的实测样本 ===')
    if not refs:
        print('✘ 还没有带参考视频的成功任务，无从校准。先按区间报价，生成后回来跑这条命令。')
        return
    for sid, res, c, d in refs:
        if args.ref > 0:
            unit = c / (args.ref + d)
            print('  %s  %-6s %4d 积分  输出 %.2fs + 参考 %gs = 计费 %gs  → %.2f 积分/秒'
                  % (str(sid)[:8], res, c, d, args.ref, args.ref + d, unit))
        else:
            print('  %s  %-6s %4d 积分  输出 %.2fs  → 补 --ref <参考片段秒数> 才能反推单价'
                  % (str(sid)[:8], res, c, d))
    if args.ref > 0:
        print('\n把真实单价固定下来，重新报一次价：')
        print('  python scripts/quote.py --duration %g --ref %g --resolution %s --ref-unit %.2f'
              % (args.duration, args.ref, args.resolution.lower(),
                 min(c / (args.ref + d) for _, _, c, d in refs)))
    print('\n并记进 05-成片/quote-calibration.md：')
    print('  | 日期 | 段号 | 分辨率 | 参考片段秒 | 输出秒 | 实际积分 | 反推单价 |')


def main():
    ap = argparse.ArgumentParser(description='生成报价：实测单价反推 + 传/不传参考视频两档对比')
    ap.add_argument('--duration', type=float, default=5, help='本段输出时长（秒）')
    ap.add_argument('--resolution', default='720p', help='480p / 720p / 1080p')
    ap.add_argument('--ref', type=float, default=0, help='参考片段时长（秒）；给了才报"传参考视频"那档')
    ap.add_argument('--ref-unit', type=float, default=0,
                    help='已校准的「输入视频档」单价（积分/秒），给了就用它替代区间')
    ap.add_argument('--model', default=DEFAULT_MODEL, help='模型版本（只用于显示）')
    ap.add_argument('--ref-rate', type=float, nargs=2, default=list(REF_RATE_RANGE),
                    metavar=('LO', 'HI'), help='输入视频档单价相对系数（默认 0.55 0.65，实测后自动改用实测）')
    ap.add_argument('--from', dest='src', default='', help='改用保存的 tasks.json')
    ap.add_argument('--rate-only', action='store_true', help='只打印实测单价表')
    ap.add_argument('--calibrate', action='store_true', help='打印带参考视频任务的真实单价')
    args = ap.parse_args()

    if not args.src.strip() and not os.path.exists('05-成片') and not os.path.exists('03-资产'):
        print('提示：当前目录看起来不是项目目录，命令里的 05-成片/03-资产 是相对项目根的。\n')

    tasks = load_tasks(args.src.strip() or '')
    video, image, refs = collect(tasks)
    print_rates(video, image, refs)

    if args.rate_only:
        return 0
    if args.calibrate:
        calibrate(refs, args)
        return 0
    return quote(video, args)


if __name__ == '__main__':
    sys.exit(main() or 0)
