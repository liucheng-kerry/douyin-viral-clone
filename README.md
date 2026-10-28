# douyin-viral-clone —— 一键复刻抖音爆款 AI 视频工作流

把一条对标爆款链接变成自己的 AI 视频成片，**全程在 WorkBuddy 内完成**，不需要学员拿着提示词去即梦或其他平台。

```
对标链接 → ①拆解 → ②复刻确认+脚本 → ③即梦生图资产 → ④视频提示词 → ⑤即梦生成成片 → ⑥BGM 适配合成
```

## 工作流（六阶段）

| 阶段 | 内容 | 关键产出 |
|---|---|---|
| ① 对标拆解 | 以 `yyl-benchmark-breakdown` 为方法论权威（四层+视觉七维），叠加**逐帧九项拆解**（主体/道具/环境/景别/机位/运镜/构图/光线/文字），逐帧合并成镜头段 | `frame-table.md` + 三件套报告 + 声音层记录 |
| ② 复刻确认+脚本 | 确认复刻方式（1:1 / 主角替换 / 只借结构）→ 写分段时间轴脚本 | `script.md` |
| ③ 图片资产 | 角色四面板 + 场景九宫格 + **各段首帧图**（逐项对齐逐帧拆解表对应秒的画面结构），即梦 CLI 生成 | `char_*.png` / `scene_*.png` / `first_*.png` |
| ④ 视频提示词 | 严格按 `yyl-video-prompt` 方法论：景别/机位/运镜/构图照逐帧拆解表复刻；首帧图生模式下外观不进正文；时间码即动作预算 | `clip_<NN>.md` |
| ⑤ 即梦生成成片 | 按生成模式映射 CLI 命令（text2video / image2video / frames2video / multiframe2video / multimodal2video），费用确认门 + 成功判定 + 中断恢复 | `clip_<NN>.mp4` + 拼接版 |
| ⑥ BGM 适配合成 | **硬确认门：学员确认成片后才启动**。写 BGM 适配单（情绪曲线对齐钩子/转折秒数）→ 生成音频（即梦 Web / 曲库 / Suno）→ ffmpeg 混音 | `成片_BGM版.mp4` |

## 目录结构

```
douyin-viral-clone/
├── SKILL.md                    # 六阶段编排流程（入口）
├── references/
│   ├── pipeline-spec.md        # 各阶段输入/输出契约与文件命名
│   └── dreamina-cli.md         # 即梦 CLI 生图生视频通道规范
├── vendored/
│   ├── yyl-benchmark-breakdown/  # 内嵌拆解方法论（一比一副本，阶段 1 用）
│   └── yyl-video-prompt/         # 内嵌视频提示词方法论（一比一副本，阶段 4 用）
└── scripts/
    ├── fetch-video.sh          # 链接下载兜底通道（yt-dlp）
    └── prepare-local-video.sh  # 本地视频证据包（转写+逐帧抽帧+contact sheet）
```

## 安装

**方式一：npx 一条命令**（需要 Node.js 22.20+，会交互式选择安装到哪个 agent；`-g` 装全局）：

```bash
npx skills add https://github.com/liucheng-kerry/douyin-viral-clone -g
```

> WorkBuddy 用户注意：skills CLI 的 agent 列表暂不含 WorkBuddy。WorkBuddy 学员请用方式二，或用方式一装到任意 agent 后把整个 `douyin-viral-clone` 文件夹复制到 `~/.workbuddy/skills/` 下。

**方式二：git clone（WorkBuddy 推荐方式）**：

```bash
# 用户级（所有项目可用）
git clone https://github.com/liucheng-kerry/douyin-viral-clone.git "$HOME/.workbuddy/skills/douyin-viral-clone"
```

装完重启 WorkBuddy（或新开会话）即可生效，本技能自包含、无需安装其他技能。

## 依赖

| 依赖 | 用途 | 必需性 |
|---|---|---|
| [WorkBuddy](https://www.workbuddy.cn) | 运行环境（Skill 工具调用、多模态读图） | 必需 |
| [即梦 CLI (dreamina)](https://jimeng.jianying.com/cli) | 生图生视频通道，需即梦账号授权 | 必需（阶段 3/5） |
| ffmpeg + ffprobe | 抽帧、转写喂音、混音合成 | 必需 |
| openai-whisper | 口播转写 | 必需 |
| yt-dlp | 链接下载兜底通道 | 可选 |
| 技能 `ai-drama-reference-art` | 角色四面板/场景九宫格提示词规范 | 可选（未装时按 SKILL.md 内置兜底写法） |

## 使用

在 WorkBuddy 对话中说：

> 复刻这个爆款：<抖音/B站/小红书链接>，主角换成 <你的选题方向>

或直接给本地视频文件路径。工作流会在"复刻想法确认、脚本确认、图片费用、视频费用、成片确认后再做 BGM"五个位置停下来等你确认，其余自动推进。

## 设计原则

- **保守执行**：生成操作消耗即梦积分，每个付费动作前报余额与预计消耗
- **成功判定严格**：`gen_status=success` 才算完成；中断后凭 `list_task` 找回，避免重复扣积分
- **质量来自拆解**：图片和视频的质量上限由逐帧拆解的颗粒度决定——每个物体、景别、机位、运镜、构图都要落到提示词里

## 来源声明（Credits）

本仓库 `vendored/` 目录下的两个方法论技能——

- `vendored/yyl-benchmark-breakdown/`（对标拆解方法论）
- `vendored/yyl-video-prompt/`（导演式 AI 视频提示词方法论）

来自 **「鱼亦乐」的方法论技能包**，为一比一完整副本（未做任何内容修改），在此致谢。其版权归原作者所有，本仓库收录仅用于教学复刻工作流的内部衔接。若你是原作者并希望移除或更改署名方式，请提 Issue 或联系仓库所有者，我们会立即处理。`vendored/` 之外的其余部分（六阶段编排、逐帧拆解规范、即梦 CLI 通道、脚本）为本仓库原创。
