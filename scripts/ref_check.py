#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ref_check.py —— 阶段 5「全能参考」上传清单与 @ 引用校验（零第三方依赖）

为什么需要这个脚本：
  SKILL 里两条硬规则（每次必传图、提示词里逐个 @ 点名）原来只能靠人眼核对。
  人眼核对的结果就是：图传了但提示词没点名（白传）、提示词写了没上传的图（模型硬凑）、
  @Image 编号和命令顺序错位（语义直接反了）、素材路径根本不存在（提交才报错，积分白等）。
  这个脚本把这些全部变成提交前几十毫秒的机械检查，并直接吐出可执行的命令。

用法：
  python scripts/ref_check.py --materials 04-提示词/clip_01.materials.txt \
      --duration 6 --out 04-提示词/clip_01.refs.md --emit-cmd 04-提示词/clip_01.cmd.sh

物料清单格式（04-提示词/clip_<NN>.materials.txt，每行一个素材，顺序 = 命令传参顺序 = @ 编号）：
  @Image1 | 03-资产/char_猫.png          | 锁外观（脸/毛色/体型/服装） | 不取背景与姿势
  @Image2 | 03-资产/scene_烤鱼摊.png     | 锁环境（地点/陈设/空间结构） | 不取其中人物
  @Image3 | 01-拆解/frame_001.jpg        | 锁构图/景别/机位/光线方向   | 主体长相不取
  @Video1 | 01-拆解/src_clips/src_01.mp4 | 锁动作/运镜/节奏/切点       | 不复制主体形象、服装、背景、配色

检查项（✘ = 必须改，退出码 1；⚠ = 建议改）：
  ✘ 素材文件存在、非空、扩展名与 @ 类型一致
  ✘ 编号从 1 起连续（Image1..N / Video1..N / Audio1..N，同类不许跳号）
  ✘ 提示词里的 @ 集合 == 清单集合（少了=白传，多了=幽灵引用）
  ✘ 每个 @ 在本段正文里必须带「不取什么」的否定约束
  ✘ 至少 1 张 Image（本技能的硬规则：不许纯文生跑正式段落）
  ✘ 正文必须有【参考素材】段
  ✘ 正文不许出现「不生成语音 / 后期配音 / 仅保留同期声」等致哑表述
  ✘ .prompt/clip_NN.prompt.txt 与 clip_NN.md 代码块正文逐字一致（两处不一致 = 校验过的和提交的不是同一份）
  ✘ 视频素材单条 2–30s、合计 ≤30s；图片 ≤30 张
  ⚠ 图片超过 6 张（违反最少素材原则，串味概率上升）
  ⚠ 正文长度 >1500 字（Windows 命令行上限约 8000 字符）
  ⚠ 风格句的色调词覆盖不足（给了 --style-anchor 才查）
"""

import argparse
import io
import os
import re
import shutil
import subprocess
import sys

TOKEN_RE = re.compile(r'@(Image|Video|Audio)(\d+)')
NEG_WORDS = ('不取', '不复制', '不使用', '不要', '忽略', '不参考', '不继承',
             '不照搬', '不采用', '不包含', '不跟随', '别抄')
MUTE_WORDS = ('不生成语音', '不生成对白', '后期配音', '仅保留同期声', '模型不负责出声',
              '不需要人声', '不用生成语音', '台词后期', '由后期注入', '不保留同期声')

EXT = {
    'Image': ('.png', '.jpg', '.jpeg', '.webp', '.bmp'),
    'Video': ('.mp4', '.mov', '.webm', '.m4v'),
    'Audio': ('.mp3', '.wav', '.m4a', '.aac'),
}


# ------------------------------------------------------------------ 基础


def read_text(path):
    with io.open(path, encoding='utf-8', errors='replace') as f:
        return f.read()


def runs(cmd):
    try:
        r = subprocess.run([cmd, '-version'], capture_output=True, timeout=15)
        return r.returncode == 0 or bool(r.stdout)
    except Exception:
        return False


def find_bin(name):
    for cand in (shutil.which(name), shutil.which(name + '.exe')):
        if cand and runs(cand):
            return cand
    base = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'WinGet', 'Packages')
    if os.path.isdir(base):
        for root, _d, files in os.walk(base):
            for fn in (name + '.exe', name):
                if fn in files and runs(os.path.join(root, fn)):
                    return os.path.join(root, fn)
    return None


def probe_duration(ffprobe, path):
    if not ffprobe:
        return None
    try:
        r = subprocess.run([ffprobe, '-v', 'error', '-show_entries', 'format=duration',
                            '-of', 'csv=p=0', path], capture_output=True, timeout=30)
        s = r.stdout.decode('utf-8', 'ignore').strip().splitlines()
        return float(s[0]) if s and s[0] else None
    except Exception:
        return None


def norm(s):
    """正文比较用：去掉所有空白，避免换行/缩进差异误判。"""
    return re.sub(r'\s+', '', s)


# ------------------------------------------------------------------ 解析


def parse_materials(path):
    """返回 (materials, errors)。materials = [(token, kind, idx, file, role, neg)]"""
    mats, errs = [], []
    for lineno, raw in enumerate(read_text(path).splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        m = TOKEN_RE.match(line)
        if not m:
            errs.append('清单第 %d 行不是 @Image1/@Video1/@Audio1 开头，无法解析：%s' % (lineno, line[:40]))
            continue
        token = m.group(0)
        rest = line[m.end():].lstrip(' =:：|')
        parts = [p.strip() for p in rest.split('|')]
        file = parts[0].strip().strip('`"\'')
        role = parts[1] if len(parts) > 1 else ''
        neg = parts[2] if len(parts) > 2 else ''
        if not file:
            errs.append('清单第 %d 行 %s 没有给文件路径' % (lineno, token))
            continue
        # 兼容 3 段写法：把「锁…/不取…」里带否定词的半句拆出来
        if not neg and re.search('不取|不复制|不使用|忽略', role):
            mm = re.search(r'[，,；;]\s*(不[^，,；;]*)', role)
            if mm:
                neg = mm.group(1)
                role = role[:mm.start()].strip()
        mats.append((token, m.group(1), int(m.group(2)), file, role, neg))
    if not mats and not errs:
        errs.append('清单是空的，至少要有 @Image1')
    return mats, errs


def parse_md(path):
    """从 clip_NN.md 取出元信息 + 提示词代码块正文。"""
    text = read_text(path)
    meta = {}
    for key in ('段号', '档位', '生成模式', '模型参数'):
        m = re.search(r'^\s*%s\s*[：:]\s*(.+)$' % key, text, re.M)
        if m:
            meta[key] = m.group(1).strip()
    m = re.search(r'```(?:text|txt)?[ \t]*\r?\n(.*?)\r?\n```', text, re.S)
    body = m.group(1) if m else ''
    return meta, body


def extract_style(style_md):
    """从 style-anchor.md 取风格句与负向词。"""
    text = read_text(style_md)
    blocks = re.findall(r'```(?:text)?[ \t]*\r?\n(.*?)\r?\n```', text, re.S)
    style = neg = ''
    for b in blocks:
        t = b.strip()
        if t.startswith('避免'):
            neg = t
        elif t and not style:
            style = t
    return style, neg


# ------------------------------------------------------------------ 检查


def project_root(materials, override=''):
    """清单里的路径都相对项目根（04-提示词/../）；脚本可能在任何目录被调用，必须自己推。"""
    if override:
        return os.path.abspath(override)
    p = os.path.abspath(materials)
    parent = os.path.dirname(p)
    if os.path.basename(parent) in ('04-提示词', '04-prompts', 'prompts'):
        return os.path.dirname(parent)
    return parent


def resolve(root, file):
    return file if os.path.isabs(file) else os.path.normpath(os.path.join(root, file))


def clause_after(body, pos, token):
    """取出 @Token 之后、到下一个 @ 或句末标点为止的那一小句。

    不能直接用固定窗口：多素材挤在一段时，窗口会跨进下一个 @ 的"不取"，导致
    只写"锁定"、没写"不取"的素材被误判为合格。
    """
    seg = body[pos:]
    tail = seg[len(token):]
    end = len(tail)
    for pat in (r'@(?:Image|Video|Audio)\d+', r'[。；;\n]'):
        m = re.search(pat, tail)
        if m and m.start() < end:
            end = m.start()
    return tail[:end]


def check(args):
    errs, warns, infos = [], [], []
    mats, perrs = parse_materials(args.materials)
    errs.extend(perrs)
    root = project_root(args.materials, args.root)
    infos.append('项目根：%s' % root)

    stem = os.path.basename(args.materials)
    stem = re.sub(r'\.materials\.txt$', '', stem)

    # 提示词正文：优先 .prompt 纯正文，其次 md 代码块
    body, body_src = '', ''
    if args.prompt:
        if os.path.isfile(args.prompt):
            body, body_src = read_text(args.prompt), args.prompt
        else:
            errs.append('--prompt 指向的纯正文文件不存在：%s' % args.prompt)
    clip_md = args.clip
    if not clip_md:
        guess = os.path.join(os.path.dirname(os.path.abspath(args.materials)), stem + '.md')
        clip_md = guess if os.path.isfile(guess) else ''
    meta = {}
    if clip_md and os.path.isfile(clip_md):
        meta, md_body = parse_md(clip_md)
        if not body:
            body, body_src = md_body, clip_md
            if not md_body:
                errs.append('%s 里找不到 ```text 提示词代码块' % os.path.basename(clip_md))
        # 两处正文一致性
        pdir = os.path.join(os.path.dirname(os.path.abspath(clip_md)), '.prompt')
        ptxt = os.path.join(pdir, stem + '.prompt.txt')
        if os.path.isfile(ptxt) and md_body:
            if norm(read_text(ptxt)) != norm(md_body):
                errs.append('.prompt/%s.prompt.txt 与 %s 的正文不一致——校验的和提交的不是同一份'
                            % (stem, os.path.basename(clip_md)))
            else:
                infos.append('纯正文与 md 代码块逐字一致')
    elif not body:
        errs.append('找不到提示词文件（%s.md），也没给 --prompt' % stem)

    if not body:
        return errs, warns, infos, mats, meta, stem

    # 1) 素材文件
    img_n = vid_n = 0
    vid_total = 0.0
    ffprobe = find_bin('ffprobe')
    for token, kind, idx, file, role, neg in mats:
        if kind == 'Image':
            img_n += 1
        real = resolve(root, file)
        if not os.path.isfile(real):
            errs.append('%s 文件不存在：%s（按项目根解析为 %s）' % (token, file, real))
            continue
        size = os.path.getsize(real)
        if size < 1024:
            errs.append('%s 文件只有 %d 字节，像是坏文件：%s' % (token, size, file))
        ext = os.path.splitext(real)[1].lower()
        if ext not in EXT[kind]:
            errs.append('%s 是 %s 类素材，但文件扩展名是 %s（%s）'
                        % (token, kind, ext or '(无)', os.path.basename(file)))
        if kind == 'Video':
            vid_n += 1
            d = probe_duration(ffprobe, real)
            if d is None:
                warns.append('%s 读不出时长（ffprobe 缺失或文件异常）：%s' % (token, file))
            else:
                vid_total += d
                if d < 1.95 or d > 30.05:
                    errs.append('%s 时长 %.2fs，超出单条 2–30s 限制：%s' % (token, d, file))
                else:
                    infos.append('%s 时长 %.2fs ✓' % (token, d))
        if not role:
            warns.append('%s 没写「锁什么」——模型会自己猜职责' % token)
        if not neg:
            warns.append('%s 没写「不取什么」——串味风险翻倍' % token)

    # 2) 编号连续性
    for kind in ('Image', 'Video', 'Audio'):
        idxs = sorted(i for _t, k, i, _f, _r, _n in mats if k == kind)
        if idxs and idxs != list(range(1, len(idxs) + 1)):
            errs.append('%s 编号不连续：%s（必须从 1 开始、逐个 +1，否则 @ 编号与命令顺序对不上）'
                        % (kind, idxs))

    # 3) 硬规则：至少一张图
    if img_n == 0:
        errs.append('清单里没有任何 @Image——本技能不许纯文生跑正式段落，每次必传图')

    # 4) 提示词里的 @ 集合 vs 清单
    refs = TOKEN_RE.findall(body)
    ref_tokens = set('@%s%s' % (k, i) for k, i in refs)
    mat_tokens = set(t for t, _k, _i, _f, _r, _n in mats)
    missing_in_prompt = sorted(mat_tokens - ref_tokens)
    ghost = sorted(ref_tokens - mat_tokens)
    if missing_in_prompt:
        errs.append('这些素材传了但提示词里没点名（白传）：%s' % '、'.join(missing_in_prompt))
    if ghost:
        errs.append('提示词引用了没上传的素材（模型会硬凑）：%s' % '、'.join(ghost))

    # 5) 每个 @ 是否带「不取什么」（只看它自己那一小句，不能蹭到下一个 @ 的否定词）
    for token, kind, idx, file, role, neg in mats:
        hit = [m.start() for m in TOKEN_RE.finditer(body) if m.group(0) == token]
        if not hit:
            continue
        ok = False
        for pos in hit:
            if any(w in clause_after(body, pos, token) for w in NEG_WORDS):
                ok = True
                break
        if not ok:
            errs.append('%s 在提示词里只写了「锁什么」、没写「不取什么」——串味就出在这里' % token)

    # 6) 【参考素材】段
    if '【参考素材】' not in body and '参考素材：' not in body:
        errs.append('正文缺少【参考素材】段（必须在正文最前，逐个 @ 写锁什么/不取什么）')

    # 7) 致哑表述
    mute = [w for w in MUTE_WORDS if w in body]
    if mute:
        errs.append('正文出现致哑表述：%s——模型会严格执行，成片就没有台词了'
                    % '、'.join(sorted(set(mute))))

    # 8) 素材数量与正文长度
    if img_n > 30:
        errs.append('图片 %d 张，超过 seedance2.5 的 30 张上限' % img_n)
    elif img_n > 6:
        warns.append('图片 %d 张，超出「最少素材原则」（4–6 张最佳），串味概率会上升' % img_n)
    if vid_total > 30.05:
        errs.append('视频素材合计 %.2fs，超过 30s 上限——只传本段用得到的那一条' % vid_total)

    chars = len(body)
    if chars > 3000:
        errs.append('正文 %d 字，超出命令行传参安全范围，先精简画面描述（别删 @ 与负向约束）' % chars)
    elif chars > 1500:
        warns.append('正文 %d 字，偏长（建议 1500 字内），注意 Windows 命令行上限' % chars)

    # 9) 风格句覆盖（可选）
    if args.style_anchor and os.path.isfile(args.style_anchor):
        style, neg = extract_style(args.style_anchor)
        if style:
            segs = [s.strip() for s in re.split(r'[，,]', style) if len(s.strip()) >= 2]
            miss = [s for s in segs if s.split('（')[0] not in body]
            if segs and len(miss) > len(segs) // 2:
                warns.append('风格句的色调词覆盖不足，缺：%s' % '、'.join(miss[:6]))
            elif miss:
                infos.append('风格句基本覆盖（缺 %d 项：%s）' % (len(miss), '、'.join(miss[:4])))
            else:
                infos.append('风格句色调词逐字全覆盖')
        if neg and '避免' not in body and not any(w in body for w in ('不得', '禁止', '不要')):
            warns.append('正文末尾没看到负向词/禁止项（%s）' % os.path.basename(args.style_anchor))

    # 10) 元信息
    if meta.get('生成模式') and '全能参考' not in meta['生成模式']:
        warns.append('外壳里「生成模式：%s」不是全能参考——例外通道必须在 gate-log 写原因'
                     % meta['生成模式'])
    return errs, warns, infos, mats, meta, stem


# ------------------------------------------------------------------ 输出


def build_cmd(args, mats, stem):
    lines = ['dreamina multimodal2video \\']
    for token, kind, _i, file, _r, _n in mats:
        flag = {'Image': '--image', 'Video': '--video', 'Audio': '--audio'}[kind]
        lines.append('  %s "%s" \\' % (flag, file))
    lines.append('  --prompt "$(cat "04-提示词/.prompt/%s.prompt.txt")" \\' % stem)
    lines.append('  --model_version=%s --duration=%d --ratio=%s \\'
                 % (args.model, args.duration, args.ratio))
    lines.append('  --video_resolution=%s --poll=120' % args.resolution)
    return '\n'.join(lines)


def write_report(path, errs, warns, infos, mats, meta, stem, cmd):
    L = ['# %s 上传清单与 @ 引用校验\n' % stem,
         '> 由 `scripts/ref_check.py` 机械生成。**✘ 未清零不许提交**（那是白花钱）。\n',
         '结论：%s\n' % ('✘ 不通过（%d 项必须改）' % len(errs) if errs else
                        ('⚠ 可提交，但有 %d 条建议' % len(warns) if warns else '✅ 全部通过'))]
    if meta:
        L.append('## 元信息\n')
        for k, v in meta.items():
            L.append('- %s：%s' % (k, v))
        L.append('')
    L.append('## 上传清单（顺序 = @ 编号 = 命令传参顺序）\n')
    L.append('| @ | 文件 | 锁什么 | 不取什么 |')
    L.append('|---|---|---|---|')
    for token, _k, _i, file, role, neg in mats:
        L.append('| %s | `%s` | %s | %s |' % (token, file, role or '—', neg or '—'))
    L.append('')
    if errs:
        L.append('## ✘ 必须改（%d）\n' % len(errs))
        L.extend('- %s' % e for e in errs)
        L.append('')
    if warns:
        L.append('## ⚠ 建议改（%d）\n' % len(warns))
        L.extend('- %s' % w for w in warns)
        L.append('')
    if infos:
        L.append('## ✓ 已核对\n')
        L.extend('- %s' % i for i in infos)
        L.append('')
    if cmd:
        L.append('## 提交命令（校验通过后原样执行）\n')
        L.append('```bash')
        L.append(cmd)
        L.append('```')
        L.append('')
    with io.open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')


def main():
    ap = argparse.ArgumentParser(description='全能参考上传清单与 @ 引用校验')
    ap.add_argument('--materials', required=True, help='04-提示词/clip_NN.materials.txt')
    ap.add_argument('--root', default='', help='项目根目录（默认由 materials 路径推导：04-提示词 的上一级）')
    ap.add_argument('--clip', default='', help='clip_NN.md（默认按 materials 同目录同名推导）')
    ap.add_argument('--prompt', default='', help='直接指定纯正文文件（默认取 .prompt/clip_NN.prompt.txt 或 md 代码块）')
    ap.add_argument('--style-anchor', default='', help='01-拆解/style-anchor.md，给了就顺带查风格句覆盖')
    ap.add_argument('--duration', type=int, default=5, help='该段时长秒数（写进生成的命令）')
    ap.add_argument('--model', default='seedance2.5', help='模型版本')
    ap.add_argument('--ratio', default='9:16', help='画幅')
    ap.add_argument('--resolution', default='720p', help='分辨率')
    ap.add_argument('--out', default='', help='报告输出路径（xxx.refs.md）')
    ap.add_argument('--emit-cmd', default='', help='把提交命令写成 .cmd.sh（不写则只打印）')
    args = ap.parse_args()

    if not os.path.isfile(args.materials):
        sys.exit('✘ 找不到物料清单：%s\n   先按 SKILL 阶段 5.0.2 的格式写一份' % args.materials)

    errs, warns, infos, mats, meta, stem = check(args)
    cmd = build_cmd(args, mats, stem) if mats and not errs else ''

    print('=== %s 上传清单校验 ===' % stem)
    for i in infos:
        print('  ✓ %s' % i)
    for w in warns:
        print('  ⚠ %s' % w)
    for e in errs:
        print('  ✘ %s' % e)

    if errs:
        print('\n✘ 不通过：%d 项必须改，改完重跑。未清零不许提交（这是花钱的通道）。' % len(errs))
    else:
        print('\n✅ 通过（%d 条建议可选）。' % len(warns))
        print('\n提交命令：\n' + cmd)
        if args.emit_cmd:
            head = ('#!/usr/bin/env bash\n# %s 提交命令（ref_check.py 生成，勿手改顺序）\n'
                    '# 前提：已获学员对该段的授权，且 gate-log.md 已有该行\n' % stem)
            with io.open(args.emit_cmd, 'w', encoding='utf-8', newline='\n') as f:
                f.write(head + cmd + '\n')
            print('\n已写出：%s' % args.emit_cmd)

    if args.out:
        write_report(args.out, errs, warns, infos, mats, meta, stem, cmd)
        print('报告：%s' % args.out)

    sys.exit(1 if errs else 0)


if __name__ == '__main__':
    main()
