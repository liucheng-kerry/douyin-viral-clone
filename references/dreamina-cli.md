# Dreamina CLI 生图生视频通道（references）

本工作流阶段 3（图片资产）和阶段 5（视频生成）的执行通道是即梦官方 CLI `dreamina`，让学员全程在 WorkBuddy 内完成，不需要自己去即梦网页版或其他平台。

## 前置检查（每次开工先跑）

```bash
dreamina --version        # 确认 CLI 可用（安装于 ~/bin/dreamina.exe）
dreamina user_credit      # 确认登录态 + 报积分余额
```

- 已登录 → 直接向用户报「当前余额 N 积分」。
- 未登录/报错 → 引导执行 `dreamina login`（OAuth Device Flow）。CLI 会打印 `verification_uri`、`user_code`，把链接和用户码贴给学员，在浏览器完成授权后 CLI 自动返回成功；把账户信息（user_id / vip_level / total_credit）明确告知学员。**登录命令必须等它跑完并回显结果，不能只贴链接就走。**
- CLI 不可用且学员不便授权（网络故障、无即梦账号）→ 回退 WorkBuddy 内置 ImageGen/VideoGen，并明确告知通道变更。

## 授权与费用（硬门槛，不可跳过、不可合并授权）

所有真实生成任务执行前：

1. 跑 `dreamina user_credit` 报当前余额；
2. 估算本次消耗（图片约 5–10 credits/张，视频约 50–100 credits/5 秒）；
3. 等学员确认后再执行。

**两条线的授权粒度不同**：

| 阶段 | 授权粒度 | 说明 |
|---|---|---|
| 阶段 3 生图 | **1 张样张 → 批量** 两段式 | 先做第 1 段首帧样张，过风格三问 + 学员点头后，才批量生成剩余 N 张（批量时报总消耗） |
| 阶段 5 生视频 | **一段一授权** | 每段单独发「生成申请单」，学员逐段确认；第 N 段授权不覆盖第 N+1 段；"全自动/直接跑完"不豁免；模糊答复不算授权；失败重试要重新报费用并取得同意 |

视频段的授权必须落到 `05-成片/gate-log.md`（申请时间 / 用户原话 / 授权时间 / submit_id / 结果），**没有这一行不许提交**。申请单的七项固定内容见 SKILL.md 阶段 5.2。

## 命令速查（详细参数以 `dreamina <子命令> -h` 为准，不要凭记忆硬编码）

### 改图 image2image（★ 风格一致的首选通道）

```bash
dreamina image2image --images="01-拆解/frame_006.jpg,03-资产/char_主角.png" \
  --prompt="保留图1的构图/景别/机位/光线/色调与背景，只把主体替换为图2的角色，姿态与占位不变，其余一切不变。<风格句> 避免：<负向词>" \
  --ratio=9:16 --resolution_type=2k --poll=60
```

- `--images strings` 支持 1–10 张本地图片（超过 10 张易失败）。**多图必须用英文逗号分隔（`--images="a.png,b.png"`）**——用空格分隔会被当成多余位置参数直接报 `unknown command`（已实测）；路径里不要带逗号。
- `--ratio` 省略则默认 16:9；`--resolution_type` 必填；模型 4.0–5.0 → 2k/4k，5.0Pro → 1.5k/2k/4k。
- **为什么首选它做首帧**：原片帧提供构图、光线、色调、景深，风格天然同族，比纯文生稳定得多。
- **为什么仍要带风格句**：模型会重绘，句子里不锁风格它就会自行"美化"成商业大片质感。
- **prompt 禁忌**：不要重复描述外观（长相/毛色/服装由角色图锁定），否则和参考图打架。
- 用途：各段首帧图、衔接帧改绘、主角替换微调。

### 生图 text2image（无原片帧对应时用）

```bash
dreamina text2image --prompt="..." --ratio=9:16 --resolution_type=2k --generate_num=1 --model_version=5.0 --poll=60
```

- `--resolution_type` **必填**；`--ratio` 支持 21:9/16:9/3:2/4:3/1:1/3:4/2:3/9:16（默认 16:9），可与 `--width/--height` 互换（两者必须成对、互斥）。
- 模型与分辨率组合：3.0/3.1 → 1k/2k；4.0/4.1/4.5/4.6/4.7/5.0 → 2k/4k；5.0Pro → 1.5k/2k/4k。
- `--generate_num` 1–10；`--poll=N` 提交后顺带等 N 秒。
- 竖屏资产用 `--ratio=9:16`，横屏用 16:9。

### 改图 image2image

用于衔接帧改绘（「把图片1 改为……状态」）和主角替换类改图。参数以 `-h` 为准。

### 生视频（全部异步：提交 → submit_id → query_result）

| yyl-video-prompt 生成模式 | Dreamina 命令 |
|---|---|
| 纯文生 | `text2video` |
| 首帧图生 | `image2video` |
| 首尾帧 | `frames2video` |
| 多图参考（多图故事） | `multiframe2video`（固定模型，不可选模型） |
| 全能参考 / Seedance 2.5 多图 | `multimodal2video` |

```bash
# 文生视频（默认 seedance2.0fast，5s）
dreamina text2video --prompt="..." --duration=5 --ratio=9:16 --video_resolution=720p --poll=120

# 首帧图生视频（默认 seedance2.0_vip；比例自动跟随首帧）
dreamina image2video --image=./clip_01_first.png --prompt="..." --video_resolution=720p --poll=120
```

- `--video_resolution` **必填**。seedance2.5 → 480p/720p/1080p，时长 4–30s，**VIP 专属**；seedance2.0_vip → 720p/1080p/4k；其他模型 → 720p，时长 4–15s。
- `image2video` / `frames2video` 用 seedance2.5 时**禁止传 `--ratio`**（输出跟随首帧）；其他模型可传也可自动跟随。
- `--duration` 默认 5s。

### 查询与下载

```bash
dreamina query_result --submit_id=<id> --download_dir=replicate/<项目>/05-成片
dreamina list_task --gen_status=success
```

## 结果判定与容错（来自 dreamina 官方 SKILL 规范）

- 提交返回 `submit_id` + `gen_status=querying` 只代表**受理成功**，不等于生成成功。
- 只有 `gen_status=success` 才算完成；`fail` 时把 `fail_reason` 原样告知学员并给下一步建议。
- `AigcComplianceConfirmationRequired` → 该模型需先在即梦 Web 端完成一次首次生成确认；引导学员去网页端跑一次再回来重试（这是全流程唯一需要出 WorkBuddy 的例外，要明确说明）。
- 失败段重试 1 次，仍失败则记录原因继续下一段，最后统一汇报。
- `--poll=N` 超时仍 querying → 保存 submit_id，改用 `query_result` 持续跟进。

## 产物落盘与质检

- `--download_dir` 直接指向对应阶段目录；下载后重命名为约定名（`char_<角色名>.png` / `clip_<NN>.mp4`，见 pipeline-spec.md）。
- 图片生成后用 ViewImage 逐张质检：**先看风格三问**（色调/质感/光线 vs 原片同秒帧），再看面板结构与一致性；风格不对优先改风格句与负向词，不是多抽几张。
- 视频每段生成完单独交付给学员验收，通过后才进下一段（见阶段 5.3）。
- 每个 `submit_id` 和最终状态记录到项目 `05-成片/README.md`（命令、参数、submit_id、状态），授权信息记 `05-成片/gate-log.md`，两处都要有，便于对账和排查。
