#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gate_check.py —— 生成许可台账机械校验（图片 + 视频，含重生成；零第三方依赖）

为什么需要这个脚本：
  SKILL 里的「任何一次生成都要先拿授权」原来只是一句嘱咐，而嘱咐的实际结果就是
  "上次授权过了，这次直接跑"——重生成、失败重试、换通道、批量出图，全是最容易偷偷花钱的地方。
  这个脚本把两个台账（图片 03-资产/image-gate.md、视频 05-成片/gate-log.md）变成提交前
  几十毫秒的机械检查：没记录 / 没原话 / 时间倒挂 / 模糊批准 / 参数非法 / 重生成不申请，一律 ✘。

用法：
  # 视频：发申请单之前、执行 cmd.sh 之前
  python scripts/gate_check.py --gate 05-成片/gate-log.md --segment clip_01

  # 图片：任何一张出图之前
  python scripts/gate_check.py --image-gate 03-资产/image-gate.md --batch img_02

  # 两份一起查（阶段交接时用）
  python scripts/gate_check.py --gate 05-成片/gate-log.md --image-gate 03-资产/image-gate.md

  # 打印空表头（照抄进台账，别手打列名）
  python scripts/gate_check.py --template video
  python scripts/gate_check.py --template image

  # 资产交付对账（每张图落盘后、问学员"能用吗"之前；阶段 3 收尾再跑一次）
  python scripts/gate_check.py --image-gate 03-资产/image-gate.md --asset-audit 03-资产

检查项（✘ = 不许提交，退出码 1；⚠ = 建议改）：
  ✘ 台账存在且能解析出表头（缺列 = 台账是自己另写的，无法对账）
  ✘ 每一行提交记录（submit_id 非空 / 结果非空）都有「用户授权原话 + 授权时间」
  ✘ 授权原话不是模糊话（全自动/都行/随便/你看着办…），且没有点名本次段号或批次号
  ✘ 授权时间 ≥ 申请时间（时间倒挂 = 事后补账）
  ✘ 视频：每段第一次提交必须是「首次 / 例外通道 / 测试」，之后必须是「重生成-* / 重试-失败」
  ✘ 视频：已有 success 的段再次提交，动作必须是重生成类（不许"悄悄重跑一遍"）
  ✘ 视频：上传素材列不能为空（空着就说明这段是纯文生，违规）
  ✘ 图片：模型版本与分辨率组合不合法（按 dreamina CLI 实测矩阵）
  ✘ 图片：image2image 用了 3.0/3.1（改图不支持 3.x）
  ✘ 图片：张数不等于 1（每段提示词只出一张图，不出候选、不出多面板多角度）
  ✘ 图片：类型写了已废弃的「批量 / 单张补」
  ✘ 图片：图型与比例不匹配（场景/首帧必须与成片同比例 9:16；主角建议 3:4，方图给 ⚠）
  ✘ 图片：类型写「重生成」但该批次前面没有任何出图记录
  ✘ 资产对账：磁盘资产与台账「产出文件」不是一一对应（多了=漏交付/漏记账，少了=台账记了假账）
  ✘ 资产对账：资产还是下载原名（<submit_id>_image_1.png），没改成 char_/scene_/prop_/first_
  ✘ submit_id 重复（复制粘贴导致的假记录）
  ⚠ 该行没有写预计积分（等于没报价）
  ⚠ 例外通道没写原因（事后无法对账）
  ⚠ 结果值不在约定枚举里
"""

import argparse
import io
import os
import re
import sys
from datetime import datetime

# ------------------------------------------------------------------ 常量

# dreamina CLI 实测矩阵（`dreamina text2image -h` / `image2image -h`）
IMG_MODELS_T2I = {
    '3.0': ('1k', '2k'),
    '3.1': ('1k', '2k'),
    '4.0': ('2k', '4k'),
    '4.1': ('2k', '4k'),
    '4.5': ('2k', '4k'),
    '4.6': ('2k', '4k'),
    '4.7': ('2k', '4k'),
    '5.0': ('2k', '4k'),
    '5.0Pro': ('1.5k', '2k', '4k'),
}
IMG_MODELS_I2I = {k: v for k, v in IMG_MODELS_T2I.items() if not k.startswith('3.')}
IMG_RATIOS = ('21:9', '16:9', '3:2', '4:3', '1:1', '3:4', '2:3', '9:16')

VIDEO_ACTIONS_FIRST = ('首次', '例外通道', '测试')
VIDEO_ACTIONS_REDO = ('重生成-画面', '重生成-音频', '重试-失败')
VIDEO_ACTIONS = VIDEO_ACTIONS_FIRST + VIDEO_ACTIONS_REDO

IMG_ACTIONS = ('样张', '单张', '重生成')          # 一段提示词一张图：样张=本项目第一张
IMG_ACTIONS_LEGACY = ('批量', '单张补')            # 旧台账写法，现在出现即判 ✘
# 图型 -> 允许的比例（每段提示词只出一张图，比例按"这张图要装什么"定，不再有方阵面板）
IMG_TYPE_RATIOS = {
    '主角': ('3:4', '2:3', '9:16'),      # 全身入画不裁头脚；1:1/横图会切掉腿 → ⚠
    '场景': ('9:16', '16:9'),            # 与成片同比例；1:1 → ✘
    '道具': ('1:1', '4:3', '3:4'),
    '首帧': ('9:16', '16:9'),            # 与成片同比例；1:1 → ✘
    '衔接帧': ('9:16', '16:9'),
}
IMG_TYPE_ALIAS = {
    # 现行写法
    '主角': '主角', '主角图': '主角', '主角定妆': '主角', '定妆照': '主角', '角色': '主角', '角色图': '主角',
    '场景': '场景', '场景图': '场景', '场景定场': '场景', '定场图': '场景',
    '道具': '道具', '道具图': '道具', '道具特写': '道具',
    '首帧': '首帧', '首帧图': '首帧', '首帧改绘': '首帧', '改绘': '首帧',
    '衔接帧': '衔接帧', '末帧': '衔接帧',
    # 旧写法（多面板时代），继续认，免得老台账报错；比例另给 ⚠
    '角色sheet': '主角', '角色四面板': '主角', '四面板': '主角', 'charactersheet': '主角',
    '场景9宫格': '场景', '场景九宫格': '场景', '九宫格': '场景',
}
LEGACY_KIND_NAMES = ('角色sheet', '角色四面板', '四面板', 'charactersheet',
                       '场景9宫格', '场景九宫格', '九宫格')

VIDEO_RESULTS = ('待提交', 'querying', 'success', 'fail', '已下载', '已验收')
IMG_RESULTS = ('待提交', 'querying', 'success', 'fail', '已下载')

# 不含具体点名的"批准"其实不是授权（SKILL 铁律二：含糊答复不算授权）
VAGUE_WORDS = ('全自动', '直接跑完', '别问了', '不用问', '你看着办', '看着办',
               '随便', '都行', '都可以', '你决定', '听你的', '一键', '随你',
               '你来定', '跑吧', '继续吧', '你自己定')

VIDEO_COLS = ('段号', '动作', '参考视频', '用户授权原话', '授权时间')
IMG_COLS = ('批次', '类型', '通道', '模型版本', '分辨率', '用户授权原话', '授权时间')

TEMPLATE_VIDEO = (
    '| 序号 | 段号 | 动作 | 通道/模型 | 参数（时长/分辨率/比例） | 参考视频 | 预计积分 | 实际积分 | 上传素材（@ 编号） | 申请时间 | 用户授权原话 | 授权时间 | submit_id | 结果 | 备注 |\n'
    '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n'
    '| 1 | clip_01 | 首次 | multimodal2video / seedance2.5 | 6s / 720p / 9:16 | 传 src_01（6s） | 约132–156 |  | @Image1 char_猫、@Image2 scene_摊、@Image3 frame_001、@Video1 src_01 | 10-28 14:02 | "第1段可以生成，带参考视频" | 10-28 14:05 | — | 待提交 | — |\n'
)
TEMPLATE_IMAGE = (
    '| 批次 | 类型 | 图型 | 通道 | 模型版本 | 分辨率 | 比例 | 张数 | 预计积分 | 实际积分 | 申请时间 | 用户授权原话 | 授权时间 | 结果 | 产出文件 |\n'
    '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n'
    '| img_01 | 样张 | 主角 | text2image | 5.0 | 2k | 3:4 | 1 | 约3 | 3 | 10-28 11:20 | "img_01 可以，5.0 2k 3:4 一张" | 10-28 11:23 | success | 03-资产/char_猫.png |\n'
)


# ------------------------------------------------------------------ 基础


def read_text(path):
    with io.open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
        return f.read()


def norm(s):
    return re.sub(r'[\s*`　]', '', s or '')


def numbers(s):
    """取单元格里的所有数字：「约132–156」-> [132, 156]，「约8」-> [8]，空 -> []。"""
    return [float(x) for x in re.findall(r'\d+(?:\.\d+)?', (s or '').replace(',', ''))]


def parse_float(s):
    n = numbers(s)
    return n[0] if n else None


def parse_tables(path):
    """把 markdown 文件里所有表格解析成 [(header, rows)]，rows 已跳过 |---| 分隔行。"""
    tables, cur = [], []
    for ln in read_text(path).splitlines():
        s = ln.strip()
        if s.startswith('|'):
            cur.append(s)
        else:
            if len(cur) >= 2:
                tables.append(cur)
            cur = []
    if len(cur) >= 2:
        tables.append(cur)

    out = []
    for block in tables:
        cells = [[c.strip() for c in row.strip().strip('|').split('|')] for row in block]
        header = [norm(c) for c in cells[0]]
        rows = []
        for r in cells[1:]:
            if r and all(re.fullmatch(r':?-{2,}:?', c.replace(' ', '')) for c in r if c):
                continue
            rows.append(r)
        out.append((header, rows))
    return out


def pick_table(tables, required, label):
    best = None
    for header, rows in tables:
        hit = sum(1 for c in required if c in header)
        if hit == len(required):
            return header, rows
        if best is None or hit > best[0]:
            best = (hit, header, rows)
    if best:
        missing = [c for c in required if c not in best[1]]
        sys.exit('✘ %s 表头缺列：%s\n   现有表头：%s\n   用 --template 打印标准表头照抄。'
                 % (label, '、'.join(missing), ' | '.join(best[1])))
    sys.exit('✘ %s 里找不到台账表格（一行都没写？）\n   用 --template 打印标准表头先建表。' % label)


def col(header, row, name):
    n = norm(name)
    for i, h in enumerate(header):
        if h == n:
            return row[i].strip() if i < len(row) else ''
    return ''


TIME_FMTS = ('%m-%d %H:%M', '%Y-%m-%d %H:%M', '%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S',
             '%m/%d %H:%M', '%Y/%m/%d %H:%M')


def parse_time(s):
    s = (s or '').strip()
    if not s or s in ('—', '-', '——'):
        return None
    for f in TIME_FMTS:
        try:
            if '%Y' in f:
                return datetime.strptime(s, f)
            # 台账只写 MM-DD HH:MM：显式补当前年份，避免 strptime 的歧义告警
            return datetime.strptime('%d-%s' % (datetime.now().year, s), '%Y-' + f)
        except ValueError:
            continue
    return None


def has_token(text, token):
    """原话里是否点名了本次对象（clip_01 / 第1段 / img_02 / 第2批 …）。"""
    if not text:
        return False
    t = norm(text).lower()
    tk = token.lower()
    if tk and tk in t:
        return True
    m = re.search(r'(\d+)', tk)
    if not m:
        return False
    num = str(int(m.group(1)))   # clip_01 → 1，clip_02 → 2（台账里通常写「第1段」不带前导零）
    is_img = tk.startswith('img')
    pats = ([r'第%s批', r'批次%s', r'img_?0*%s', r'第%s张'] if is_img
            else [r'第%s段', r'段%s', r'clip_?0*%s', r'第%s个'])
    for p in pats:
        if re.search(p % num, t):
            return True
    return False


def check_authorization(header, row, token, errs, warns, tag):
    """授权三件事：有原话、原话不模糊、时间不倒挂。"""
    said = col(header, row, '用户授权原话')
    at = parse_time(col(header, row, '授权时间'))
    asked = parse_time(col(header, row, '申请时间'))

    if not said or said in ('—', '-', '——'):
        errs.append('%s 没有「用户授权原话」——没申请就提交，等于私自花钱' % tag)
        return
    low = norm(said).lower()
    hit = [w for w in VAGUE_WORDS if norm(w) in low]
    if hit and not has_token(said, token):
        errs.append('%s 授权原话「%s」是模糊批准（命中：%s），且没点名 %s——不算授权，要追问到具体对象'
                    % (tag, said, '、'.join(hit), token))
    if at is None:
        errs.append('%s 授权时间缺失或格式不对（要 MM-DD HH:MM）：%s' % (tag, col(header, row, '授权时间')))
    elif asked and at < asked:
        errs.append('%s 授权时间 %s 早于申请时间 %s——时间倒挂，通常是事后补账'
                    % (tag, at.strftime('%m-%d %H:%M'), asked.strftime('%m-%d %H:%M')))


def is_submitted(header, row, result_key='结果'):
    """「待提交」行里的 submit_id 通常写「—」占位，不能当成真提交。"""
    sid = (col(header, row, 'submit_id') or '').strip()
    result = col(header, row, result_key)
    if result == '待提交':
        return False
    return bool(sid) and sid not in ('—', '-', '——', '待提交')


# ------------------------------------------------------------------ 视频台账


def check_video(path, segment, errs, warns, infos):
    header, rows = pick_table(parse_tables(path), VIDEO_COLS, path)
    if not rows:
        errs.append('%s 表里没有任何记录（一段都还没申请？）' % path)
        return

    seen_ids = {}
    by_seg = {}
    total_submitted = 0
    actual_sum = 0.0
    actual_rows = 0

    for idx, row in enumerate(rows, 1):
        seg = col(header, row, '段号')
        if not seg:
            continue
        if not re.fullmatch(r'clip_\d{2,3}', seg):
            errs.append('第 %d 行段号「%s」不合规（要 clip_NN）' % (idx, seg))
        action = col(header, row, '动作')
        result = col(header, row, '结果')
        sid = col(header, row, 'submit_id')
        submitted = is_submitted(header, row)

        tag = '第 %d 行 %s' % (idx, seg)
        if action and action not in VIDEO_ACTIONS and not action.startswith('重生成') and not action.startswith('重试'):
            warns.append('%s 动作「%s」不在约定枚举（%s）' % (tag, action, '/'.join(VIDEO_ACTIONS)))
        if result and result not in VIDEO_RESULTS:
            warns.append('%s 结果「%s」不在约定枚举（%s）' % (tag, result, '/'.join(VIDEO_RESULTS)))
        if not col(header, row, '预计积分'):
            warns.append('%s 没写预计积分（等于没报价）' % tag)

        # 模型与分辨率：必须由学员在申请单里选定后逐字写进台账（提交行强制）
        model = (col(header, row, '通道/模型') or col(header, row, '模型') or '').strip()
        param = (col(header, row, '参数（时长/分辨率/比例）') or col(header, row, '参数') or '').strip()
        mlow = model.lower()
        if model and not any(k in mlow for k in ('seedance2.5', 'seedance2.0_vip', 'seedance2.0', 'seedance1')):
            warns.append('%s 模型「%s」不在常用清单（seedance2.5 / seedance2.0_vip）——'
                         '若是例外通道（模型不可选），备注列写清原因' % (tag, model))
        if submitted and not model:
            errs.append('%s 已提交但没写模型——模型必须由学员在申请单里选定（① seedance2.5 ② seedance2.0_vip）'
                        '并写进台账，不许默认沿用' % tag)
        elif not submitted and not model:
            warns.append('%s 还没写模型——发申请单时模型要以选择题形式报给学员' % tag)
        res_m = re.search(r'(480p|720p|1080p|4k)', param, re.I)
        if res_m:
            res = res_m.group(1).lower()
            if res in ('4k', '1080p'):
                warns.append('%s 分辨率 %s：单价未实测，'
                             '提交前先跑 1s 小样测价（quote.py --rate-only 看实测表）' % (tag, res))
        elif submitted:
            errs.append('%s 已提交但参数里没写清分辨率（480p/720p/1080p）——'
                        '分辨率必须由学员选定并写进台账，不许默认沿用' % tag)
        elif not param:
            warns.append('%s 参数列还是空的（时长/分辨率/比例）' % tag)

        # 实际积分：生成成功后必须回填，否则"报的价"和"扣的钱"永远对不上账
        actual = col(header, row, '实际积分')
        if result in ('success', '已验收', '已下载') or (not result and submitted):
            if not actual:
                warns.append('%s 已生成但没回填「实际积分」——跑 `dreamina list_task` 读这条任务的 '
                             'commerce_info.credit_count 填进来（报价准不准全靠这一列）' % tag)
            else:
                got = parse_float(actual)
                planned = numbers(col(header, row, '预计积分'))
                if got is not None:
                    actual_sum += got
                    actual_rows += 1
                if got is not None and planned:
                    lo, hi = min(planned), max(planned)
                    if got < lo * 0.7 or got > hi * 1.3:
                        warns.append('%s 实际积分 %g 与报价 %s 差得太多（超 30%%）——'
                                     '要么预计积分写错了，要么时长/分辨率/参考片段秒数记错了；'
                                     '按真实扣费跑一次 `python scripts/quote.py --calibrate --ref <参考秒>` 校准'
                                     % (tag, got, col(header, row, '预计积分')))
        if action == '例外通道' and not col(header, row, '备注'):
            warns.append('%s 走了例外通道但没写原因（事后无法对账）' % tag)

        # 参考视频：每段必须明确「传 / 不传」，且与上传素材一致（两档计费规则不同）
        refv = col(header, row, '参考视频')
        refn = norm(refv)
        mats = col(header, row, '上传素材（@ 编号）') or col(header, row, '上传素材')
        if not refn:
            errs.append('%s 没记录「参考视频」——这段传不传参考视频必须由用户选定并写进台账'
                        '（写「传 src_01（6s）」或「不传」）' % tag)
        elif re.match(r'^(不传|否|无|不需要|不用|不带)', refn):
            if 'Video' in mats:
                errs.append('%s 参考视频写「不传」，但上传素材里有 @Video——两处矛盾；'
                            '而且计费档不同，必须二选一' % tag)
        elif re.match(r'^(传|是|带|用|参考)', refn):
            if 'Video' not in mats:
                errs.append('%s 参考视频写「传」，但上传素材里没有 @VideoN——两处矛盾'
                            '（要么补素材，要么改成不传）' % tag)
            if not re.search(r'\d', refn):
                warns.append('%s 参考视频没写片段时长（如「传 src_01（6s）」）——'
                             '带视频档按"参考秒数+输出秒数"计费，缺时长报不准积分' % tag)
        else:
            errs.append('%s 参考视频「%s」判断不了，只能写「传 src_NN（Xs）」或「不传」' % (tag, refv))

        if submitted:
            if not mats:
                errs.append('%s 已提交但「上传素材」列为空——空着就说明这段是纯文生，违规' % tag)
            dead = [w for w in ('.prompt', '.md', '.txt') if w in sid]
            if dead:
                errs.append('%s submit_id 是「%s」，不像任务 ID（更像误填了文件名）' % (tag, sid))
            total_submitted += 1
        if sid and sid not in ('待提交', '—', '-', '——'):
            if sid in seen_ids:
                errs.append('%s 的 submit_id %s 与第 %d 行重复——复制粘贴出来的假记录'
                            % (tag, sid, seen_ids[sid]))
            else:
                seen_ids[sid] = idx
        if result == '待提交' and sid not in ('', '待提交', '—', '-', '——'):
            warns.append('%s 结果写「待提交」却已经有 submit_id，两处矛盾（要么改结果，要么清空 submit_id）' % tag)
        if result == 'success' and not sid:
            warns.append('%s 结果写 success 但没有 submit_id——事后没法用 query_result 复核' % tag)

        by_seg.setdefault(seg, []).append((idx, action, submitted, result, sid))

    for seg, items in by_seg.items():
        subs = [(i, a, r) for (i, a, s, r, _) in items if s]
        if subs:
            first_idx, first_action, _ = subs[0]
            if first_action not in VIDEO_ACTIONS_FIRST and not (
                    first_action.startswith('重生成') or first_action.startswith('重试')):
                errs.append('%s 首次提交的动作是「%s」，应为「首次」' % (seg, first_action or '(空)'))
            for k, (i, a, r) in enumerate(subs[1:], 2):
                if a in VIDEO_ACTIONS_FIRST:
                    errs.append('%s 第 %d 次提交仍写「%s」——重跑必须标成「重生成-画面/重试-失败」，'
                                '否则台账看不出这是第二次花钱' % (seg, k, a))
        # success 之后再提交 = 重生成
        succeeded = any(r == 'success' for (_, _, s, r, _) in items if s)
        if succeeded:
            after = [(i, a) for (i, a, s, r, _) in items if s and r != 'success'
                     and not str(a).startswith('重生成') and not str(a).startswith('重试')]
            for i, a in after:
                errs.append('%s 第 %d 行在这段已 success 后又提交，动作是「%s」——重生成要写重生成类动作'
                            % (seg, i, a or '(空)'))

    # 台账「参考视频」列 vs materials 清单里的 @Video：两处不一致，就会按错档计费
    root = os.path.dirname(os.path.dirname(os.path.abspath(path)))
    seen_seg = set()
    for idx, row in enumerate(rows, 1):
        seg = col(header, row, '段号')
        if not seg or seg in seen_seg:
            continue
        seen_seg.add(seg)
        mat = os.path.join(root, '04-提示词', '%s.materials.txt' % seg)
        if not os.path.isfile(mat):
            continue
        try:
            with io.open(mat, 'r', encoding='utf-8-sig', errors='replace') as f:
                has_video_mat = bool(re.search(r'^\s*@Video\d', f.read(), re.M))
        except (IOError, OSError):
            continue
        last = [r for r in rows if col(header, r, '段号') == seg][-1]
        refn = norm(col(header, last, '参考视频'))
        if not refn:
            continue
        if refn.startswith('传') and not has_video_mat:
            errs.append('%s 台账写「参考视频=传」，但 %s 里没有 @Video 行——命令不会带 --video，'
                        '你以为传了其实没传' % (seg, os.path.basename(mat)))
        elif re.match(r'^(不传|否|无|不需要|不用|不带)', refn) and has_video_mat:
            errs.append('%s 台账写「参考视频=不传」，但 %s 里还有 @Video 行——cmd.sh 会带上 --video，'
                        '按另一档计费；要么删素材行，要么改台账' % (seg, os.path.basename(mat)))

    for idx, row in enumerate(rows, 1):
        if not is_submitted(header, row):
            continue
        check_authorization(header, row, col(header, row, '段号'), errs, warns, '第 %d 行 %s'
                            % (idx, col(header, row, '段号')))

    if segment:
        items = by_seg.get(segment, [])
        if not items:
            errs.append('%s 里没有 %s 的任何记录——先按 5.2 发申请单并记账，再提交'
                        % (path, segment))
        else:
            ready = [i for (i, a, s, r, _) in items if r == '待提交']
            if not ready:
                errs.append('%s 没有「结果=待提交」的授权行——已授权待执行的那一行要写成 submit_id 空、结果=待提交'
                            % segment)
            auth = [i for i in ready if col(header, rows[i - 1], '用户授权原话')]
            if ready and not auth:
                errs.append('%s 的待提交行没有授权原话（阶段 5.3 第一步：先记账再提交）' % segment)
            infos.append('%s：共 %d 条记录，其中已提交 %d 条'
                         % (segment, len(items), len([1 for (_, _, s, _, _) in items if s])))

    infos.append('%s：%d 行，已提交 %d 条' % (path, len(rows), total_submitted))
    if actual_rows:
        infos.append('已回填实际积分 %d 条，合计 %g 积分（这就是这部片子到目前真花的钱）'
                     % (actual_rows, actual_sum))
    elif total_submitted:
        infos.append('还没有任何实际积分回填——报价是估的还是准的，目前无从判断')


# ------------------------------------------------------------------ 图片台账


def check_images(path, batch, errs, warns, infos):
    header, rows = pick_table(parse_tables(path), IMG_COLS, path)
    if not rows:
        errs.append('%s 表里没有任何记录（一批都还没申请？）' % path)
        return

    sample_ok_at = None   # 第一条 结果=success 的样张行号
    img_actual = 0.0
    img_rows = 0
    for idx, row in enumerate(rows, 1):
        tmp = col(header, row, '类型')
        if norm(tmp) == '样张' and col(header, row, '结果') == 'success':
            sample_ok_at = idx
            break

    for idx, row in enumerate(rows, 1):
        b = col(header, row, '批次')
        tag = '第 %d 行 %s' % (idx, b or '?')
        typ = col(header, row, '类型')
        chan = col(header, row, '通道')
        model = col(header, row, '模型版本')
        res = col(header, row, '分辨率')
        ratio = col(header, row, '比例')
        num = col(header, row, '张数')
        result = col(header, row, '结果')
        kind = norm(col(header, row, '图型'))
        kind = IMG_TYPE_ALIAS.get(kind, kind)

        if typ in IMG_ACTIONS_LEGACY:
            errs.append('%s 类型「%s」已废弃：现在一个资产只出 1 张图，没有批量。'
                        '改成「单张」（或本项目第一张写「样张」）' % (tag, typ))
        elif typ and typ not in IMG_ACTIONS:
            warns.append('%s 类型「%s」不在约定枚举（%s）' % (tag, typ, '/'.join(IMG_ACTIONS)))
        if result and result not in IMG_RESULTS:
            warns.append('%s 结果「%s」不在约定枚举（%s）' % (tag, result, '/'.join(IMG_RESULTS)))

        # 参数合法性
        if chan and chan not in ('text2image', 'image2image'):
            errs.append('%s 通道「%s」不认识（只能是 text2image / image2image）' % (tag, chan))
        elif chan and model:
            table = IMG_MODELS_I2I if chan == 'image2image' else IMG_MODELS_T2I
            if model not in table:
                errs.append('%s %s 不支持模型 %s（支持：%s）'
                            % (tag, chan, model, '、'.join(sorted(table))))
            elif res and res not in table[model]:
                errs.append('%s 模型 %s 不支持分辨率 %s（只能是 %s）'
                            % (tag, model, res, ' / '.join(table[model])))
        if not model:
            errs.append('%s 没写模型版本——出图前必须让学员在申请单里选定' % tag)
        if not res:
            errs.append('%s 没写分辨率——出图前必须让学员在申请单里选定' % tag)
        if ratio and ratio not in IMG_RATIOS:
            errs.append('%s 比例 %s 不支持（%s）' % (tag, ratio, ' / '.join(IMG_RATIOS)))
        if num:
            if not re.fullmatch(r'\d+', num):
                warns.append('%s 张数「%s」不是纯数字' % (tag, num))
            elif int(num) != 1:
                errs.append('%s 张数写了 %s——每段提示词只出 1 张图，要多个资产就分多次申请、'
                            '每张单独授权' % (tag, num))
        else:
            warns.append('%s 没写张数（应写 1：一个资产一张图）' % tag)

        # 图型与比例必须匹配：比例由"这张图要装什么"决定
        raw_kind = norm(col(header, row, '图型'))
        is_legacy_kind = raw_kind in LEGACY_KIND_NAMES
        if is_legacy_kind:
            warns.append('%s 图型「%s」是旧的多面板写法——现在一段提示词只出一张图，'
                         '主角请写「主角」（3:4 单张定妆照）、场景写「场景」（9:16 单张定场图）'
                         % (tag, raw_kind))
        if kind and ratio:
            allowed = IMG_TYPE_RATIOS.get(kind)
            if allowed and ratio not in allowed:
                if kind == '主角' or is_legacy_kind:
                    # 旧台账的比例是当时的面板要求，不再判 ✘，只提醒新写法该用什么
                    hint = {'主角': '（全身入画不裁头脚；方图/横图会切腿）',
                            '场景': '（与成片同比例，竖屏取景才准）',
                            '首帧': '（与成片同比例）'}.get(kind, '')
                    warns.append('%s 图型「%s」比例写了 %s，建议 %s%s'
                                 % (tag, kind, ratio, '/'.join(allowed), hint))
                else:
                    errs.append('%s 图型「%s」比例写 %s 不行（只能是 %s）：场景与首帧要和成片同比例，'
                                '否则阶段 5 用起来会被二次裁切' % (tag, kind, ratio, ' / '.join(allowed)))
        elif not kind:
            warns.append('%s 没写「图型」（主角 / 场景 / 道具 / 首帧），脚本没法核对比例对不对' % tag)
        elif kind not in IMG_TYPE_RATIOS:
            warns.append('%s 图型「%s」不认识（主角 / 场景 / 道具 / 首帧 / 衔接帧）' % (tag, kind))

        # 重生成必须有在先记录：同一批次前面得有失败或已出图的行，否则"重生成"是凭空多花的钱
        if typ == '重生成':
            prev = []
            for r in rows[:idx - 1]:
                rk = norm(col(header, r, '图型'))
                if col(header, r, '批次') == b or (kind and IMG_TYPE_ALIAS.get(rk, rk) == kind):
                    prev.append(r)
            if not prev:
                errs.append('%s 类型写「重生成」，但台账里前面没有同一批次或同一图型的出图记录——'
                            '先有第 1 次，才有第 2 次（否则这笔钱说不清）' % tag)

        # 实际积分：出图成功后回填，图片单价按模型分档，偏差大说明选错了模型/分辨率
        actual = col(header, row, '实际积分')
        if result in ('success', '已下载') and not actual:
            warns.append('%s 出图成功但没回填「实际积分」——`dreamina list_task` 里这条的 '
                         'commerce_info.credit_count 就是实扣金额' % tag)
        elif actual:
            got = parse_float(actual)
            planned = numbers(col(header, row, '预计积分'))
            if got is not None:
                img_actual += got
                img_rows += 1
            if got is not None and planned:
                lo, hi = min(planned), max(planned)
                if got < lo * 0.7 or got > hi * 1.3:
                    warns.append('%s 实际积分 %g 与报价 %s 差得太多（超 30%%）——'
                                 '多半是模型/分辨率/张数记错了，或者命中了不同计费档'
                                 % (tag, got, col(header, row, '预计积分')))

        # 授权：台账里每一行都是一次真实生成，无条件要有原话与时间（类型写错也不能漏查）
        check_authorization(header, row, b or 'img', errs, warns, tag)

    if batch:
        hit = [r for r in rows if col(header, r, '批次') == batch]
        if not hit:
            errs.append('%s 里没有批次 %s 的记录——先写「出图申请单」并记账，再执行生图命令'
                        % (path, batch))
        infos.append('%s：台账共 %d 张，首张样张%s'
                     % (path, len(rows), ('已出（第 %d 行）' % sample_ok_at) if sample_ok_at else '还没出 ⚠'))

    infos.append('%s：%d 张记录' % (path, len(rows)))
    if img_rows:
        infos.append('出图已回填实际积分 %d 张，合计 %g 积分' % (img_rows, img_actual))


# ------------------------------------------------------------------ 资产交付对账

ASSET_PREFIXES = ('char_', 'scene_', 'prop_', 'first_', 'last_')
ASSET_ORDER = {'char_': 0, 'scene_': 1, 'prop_': 2, 'first_': 3, 'last_': 4}
ASSET_EXTS = ('.png', '.jpg', '.jpeg', '.webp')
# query_result 的原始下载名：<submit_id>_image_1.png
RAW_DL_RE = re.compile(r'^[0-9A-Za-z]{6,}_(image|video)_\d+\.[A-Za-z0-9]+$')


def scan_assets(asset_dir):
    """扫资产目录 → (定稿文件列表[(路径, 文件名)] 按 char→scene→prop→first→last 排序, 旧稿数, 未改名下载稿列表)"""
    keep, old, raw = [], 0, []
    for root, dirs, names in os.walk(asset_dir):
        rel = os.path.relpath(root, asset_dir).replace('\\', '/')
        parts = [] if rel == '.' else rel.split('/')
        if 'qc' in parts:          # 质检报告目录，不算资产
            continue
        for n in names:
            low = n.lower()
            if not low.endswith(ASSET_EXTS):
                continue
            if '_old' in parts:    # 废稿不算定稿
                old += 1
                continue
            if low.startswith(ASSET_PREFIXES):
                keep.append((os.path.join(root, n), n))
            elif RAW_DL_RE.match(n):
                raw.append(n)
    keep.sort(key=lambda x: (ASSET_ORDER.get(x[1].split('_')[0] + '_', 9), x[1]))
    return keep, old, raw


def audit_assets(asset_dir, gate_path, errs, warns, infos):
    """磁盘 ↔ 台账「产出文件」双向对账。

    存在的理由：学员看得见的是对话里交付的图，不是文件夹。实测出过"目录三张、对话只交付一张"——
    人眼对不出来，但台账与磁盘是两份独立记录，一对就能指出是哪一张漏了。
    """
    if not os.path.isdir(asset_dir):
        errs.append('找不到资产目录 %s——资产必须都落在同一个目录，否则对不了账' % asset_dir)
        return

    keep, old, raw = scan_assets(asset_dir)
    disk = set(n for _, n in keep)

    declared = []
    if not gate_path:
        warns.append('没有可用的图片台账，只能查命名与磁盘数量，没法核对"该交付几张"')
    else:
        header = rows = None
        for h, r in parse_tables(gate_path):
            if '批次' in h and ('图型' in h or '类型' in h):
                header, rows = h, r
                break
        if header is None:
            errs.append('%s 里找不到图片台账表格（用 --template image 打印表头先建表）' % gate_path)
        else:
            for idx, row in enumerate(rows, 1):
                if col(header, row, '结果') not in ('success', '已下载'):
                    continue
                p = col(header, row, '产出文件').strip().strip('`').strip()
                if not p or p in ('—', '-', '——'):
                    errs.append('台账第 %d 行（批次 %s）结果是成功，但「产出文件」空着——'
                                '这张图到底交付没有说不清，先补上文件路径'
                                % (idx, col(header, row, '批次') or '?'))
                    continue
                declared.append((idx, col(header, row, '批次') or '?', re.split(r'[\\/]', p)[-1].strip('`')))
    dset = set(n for _, _, n in declared)

    # last_*.jpg 是阶段 5 从上一段成片抽的衔接帧，不经过图片台账（不花钱、不出图），
    # 所以只参与交付清单，不参与台账对账，否则每张续接帧都会被误判成"漏记账"
    LEDGER_PREFIXES = ('char_', 'scene_', 'prop_', 'first_')
    ledger_disk = set(n for n in disk if n.lower().startswith(LEDGER_PREFIXES))
    ledger_decl = set(n for n in dset if n.lower().startswith(LEDGER_PREFIXES))
    last_frames = sorted(disk - ledger_disk)
    if dset - ledger_decl:
        warns.append('%s 在图片台账里被记了行，但它是阶段 5 抽的衔接帧、不花图钱——'
                     '台账只记真出图的资产（char_/scene_/prop_/first_），衔接帧记在 gate-log 备注即可'
                     % '、'.join(sorted(dset - ledger_decl)))

    if gate_path:
        for idx, b, pb in declared:
            if pb not in disk:
                errs.append('台账第 %d 行（%s）写着产出 %s，但 %s 里没这个文件——'
                            '记录与实际不符：没落盘？还是落了却改了名？先弄清楚再问学员"能不能用"'
                            % (idx, b, pb, asset_dir))
        for n in sorted(ledger_disk - ledger_decl):
            errs.append('%s 在磁盘上，但台账没有任何一行写它——要么漏记账，要么出图后没交付给学员'
                        '（"文件夹三张、对话一张"就是这么来的）。补台账并补一次 present_files 交付' % n)
    for n in raw:
        errs.append('%s 还是下载原名（<submit_id>_image_1.png）——没改成规范名，'
                    '阶段 5 的 @ImageN 会指错。改成 char_ / scene_ / prop_ / first_ 前缀' % n)
    if old:
        infos.append('%s 下 _old/ 有 %d 张废稿（不计入定稿，也不会被阶段 5 选中）' % (asset_dir, old))

    if gate_path:
        infos.append('定稿资产：磁盘 %d 张（台账类 %d + 衔接帧 %d）/ 台账成功 %d 行%s'
                     % (len(disk), len(ledger_disk), len(last_frames), len(ledger_decl),
                        '' if len(ledger_disk) == len(ledger_decl) else
                        '  ← 数量不一致，上面的 ✘ 没清零前不许问"这张能用吗"'))
    else:
        infos.append('定稿资产：磁盘 %d 张（没有台账，没法核对"该交付几张"）' % len(disk))
    if keep:
        lines = ['应交付清单（present_files 的 files 必须逐项覆盖；刚出的那张排第一）：']
        lines += ['   %s' % os.path.abspath(p).replace('\\', '/') for p, _ in keep]
        infos.append('\n'.join(lines))


# ------------------------------------------------------------------ 报告


def write_report(path, errs, warns, infos, args):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    lines = ['# 生成许可台账校验报告', '',
             '- 时间：%s' % datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
             '- 视频台账：%s' % (args.gate or '—'),
             '- 图片台账：%s' % (args.image_gate or '—'),
             '- 结论：%s' % ('✘ 不通过（%d 项）' % len(errs) if errs else '✅ 通过'), '']
    for title, items, mark in (('信息', infos, '✓'), ('建议', warns, '⚠'), ('必须改', errs, '✘')):
        lines.append('## %s' % title)
        if items:
            lines.extend('- %s %s' % (mark, i) for i in items)
        else:
            lines.append('- 无')
        lines.append('')
    with io.open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines))
    return path


def main():
    ap = argparse.ArgumentParser(description='生成许可台账机械校验（图片 + 视频，含重生成）')
    ap.add_argument('--gate', default='', help='视频台账 05-成片/gate-log.md')
    ap.add_argument('--segment', default='', help='只校验这一段（clip_01），提交该段前必带')
    ap.add_argument('--image-gate', default='', help='图片台账 03-资产/image-gate.md')
    ap.add_argument('--batch', default='', help='只校验这一批（img_01），执行生图前必带')
    ap.add_argument('--asset-audit', default='',
                    help='资产交付对账：给 03-资产 目录，核磁盘文件与台账「产出文件」列是否一一对应，'
                         '并打印本次必须交付的清单（防"目录三张、只交付一张"）')
    ap.add_argument('--out', default='', help='报告输出路径（xxx.gate-check.md）')
    ap.add_argument('--template', choices=('video', 'image'), help='打印标准表头后退出')
    args = ap.parse_args()

    if args.template:
        print('=== 视频台账（05-成片/gate-log.md）===\n' + TEMPLATE_VIDEO
              if args.template == 'video' else
              '=== 图片台账（03-资产/image-gate.md）===\n' + TEMPLATE_IMAGE)
        print('把表头与示例行照抄进台账，改内容；列名不要动（脚本按列名取值）。')
        return

    if not args.gate and not args.image_gate and not args.asset_audit:
        ap.error('至少要给一个台账：--gate（视频）或 --image-gate（图片），或用 --asset-audit 对账资产')

    errs, warns, infos = [], [], []

    if args.gate:
        if not os.path.isfile(args.gate):
            errs.append('找不到视频台账 %s（阶段 5 第一段就要建它）' % args.gate)
        else:
            print('=== 视频台账校验：%s%s ===' % (args.gate, (' / ' + args.segment) if args.segment else ''))
            check_video(args.gate, args.segment, errs, warns, infos)

    image_gate_ok = bool(args.image_gate) and os.path.isfile(args.image_gate)
    if args.image_gate:
        if not image_gate_ok:
            errs.append('找不到图片台账 %s（阶段 3 第一次出图前就要建它）' % args.image_gate)
        else:
            print('=== 图片台账校验：%s%s ===' % (args.image_gate, (' / ' + args.batch) if args.batch else ''))
            check_images(args.image_gate, args.batch, errs, warns, infos)

    if args.asset_audit:
        print('=== 资产交付对账：%s ===' % args.asset_audit)
        audit_assets(args.asset_audit,
                     args.image_gate if image_gate_ok else '',
                     errs, warns, infos)

    for i in infos:
        print('  ✓ %s' % i)
    for w in warns:
        print('  ⚠ %s' % w)
    for e in errs:
        print('  ✘ %s' % e)

    if errs:
        print('\n✘ 不通过：%d 项必须改。未清零不许执行生成命令（图片和视频都一样，重生成也一样）。' % len(errs))
    elif args.gate or args.image_gate:
        print('\n✅ 通过：台账能对上账（授权原话 / 时间 / 参数都在），可以执行生成。')
        if args.gate and args.segment:
            print('   下一步：bash 04-提示词/%s.cmd.sh' % args.segment)
    else:
        print('\n✅ 通过：资产命名规范、数量已列出（本次没给台账，授权未校验）。')

    if args.out:
        print('报告：%s' % write_report(args.out, errs, warns, infos, args))

    sys.exit(1 if errs else 0)


if __name__ == '__main__':
    main()
