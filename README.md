# classroom-notes · 课堂笔记入库技能

把课堂课件（手机照片 / PPTX / PDF）与可选课堂录音**逐张核对、完整摘录**，整理成 Obsidian 带图课堂笔记，并按「日期 + 学科」直接写入你的 Obsidian 库。

这是一个 Agent Skill：`SKILL.md` 描述流程与边界，`references/` 放笔记风格与配置，`scripts/` 放可执行的确定性脚本。

## 它做什么

- **逐张看，不抽样**：每张课件照片都要实际查看，逐页摘录标题、正文、表格、公式、脚注与图示关系，尾页同样不放过。
- **默认完整摘录**：定义、条件、例外、否定、数字、单位、公式、表格行列与图上标注全部保留，不拿「主要包括」「等」当摘录，不做概括式摘要。
- **带图入库**：关键图示从原图或渲染页取出，写入 Obsidian 附件目录，并在正文用相对链接引用；所有嵌入图片都必须实际看过。
- **不破坏既有笔记**：同名笔记拒绝覆盖；重复材料先查导入记录（课程文件夹内 `.classroom-imports/`）；修改既有笔记须定点编辑并留副本。
- **诚实标注缺口**：反光、遮挡、截断、无法辨认的内容按「来源 + 位置 + 原因」写明，不猜字、不用省略号无声替代，并区分「完整核对 / 部分摘录 / 尚未完成」。

## 安装

放进 Agent 的技能目录即可，目录名 `classroom-notes` 就是技能名：

```sh
git clone https://github.com/Entropicmaker/classroom-notes.git ~/.dsh/skills/classroom-notes
```

然后填写你自己的 Obsidian 库路径：

```sh
cd ~/.dsh/skills/classroom-notes
cp references/vault.example.json references/vault.json
$EDITOR references/vault.json
python3 scripts/check_environment.py   # 自检：库路径、目录、可选工具是否就绪
```

## 可选：本地课堂录音插件

仓库内的 [plugins/dsh-classroom-audio](plugins/dsh-classroom-audio/README.md) 提供 1.1.1 版录音插件，安装和模型准备见其说明。Apple Silicon 上默认使用本地 Qwen3-ASR，不需要云端 Key；两份录音保留独立时间轴，逐份完成后读取并按知识点融合。

新版本地识别不注入课件词表，避免提示词复述与对齐失败；分段验证通过后才缓存。主技能新增有界等待脚本，并修复 macOS LibreOffice 回退渲染的中文字体缓存。

插件代码更新后应退出旧 DSH 再启动；同一个 web 端口不能同时运行两个实例。关闭网页不会退出后台服务。

## 配置 `references/vault.json`

| 字段 | 说明 |
| --- | --- |
| `vault` | Obsidian 库根目录，**必须是绝对路径**（脚本不做 `~` 展开） |
| `courses_dir` | 课程笔记目录，相对库根，例如 `03 课程` |
| `attachments_dir` | 附件目录，相对库根，例如 `09 附件/课程` |
| `aliases` | 课程别名归一化：把课件里的写法映射到现有课程文件夹名，例如 `{"gis": "GIS"}` |

这是你的本机个人配置，已在 `.gitignore` 中排除，不会进入版本库。

## 依赖

- **Python 3**：课件提取与入库脚本只用标准库；可选录音插件需要独立 Python 环境和模型，见插件说明。
- **照片输入**：需要支持图片的模型 + `read_image`；macOS 上可用 `scripts/vision_ocr.swift`（Swift + Vision 框架）做纯文字 OCR 兜底 —— 它读不出图示、箭头与版式，只能算初稿。
- **PPTX / PDF 渲染**（可选，用于视觉核对）：macOS 上优先 Microsoft PowerPoint 高保真导出，失败时回退 LibreOffice；PDF 转逐页 PNG 需要 Poppler 的 `pdftoppm`。也可用环境变量 `CLASSROOM_SOFFICE`、`CLASSROOM_PDFTOPPM` 指定路径。

## 目录结构

```
classroom-notes/
├── SKILL.md
├── references/
│   ├── vault.example.json        # 库路径与课程别名模板
│   ├── note-style.md             # 笔记风格与原文保护规则
│   └── source-review.md          # 照片处理与逐页覆盖核对
└── scripts/
    ├── check_environment.py      # 环境自检：库目录 + 可选工具
    ├── vision_ocr.swift          # macOS Vision OCR（带坐标与置信度）
    ├── extract_pptx.py           # PPTX 文本/表格/图片/备注 XML 提取
    ├── render_pptx.py            # PPTX → PDF → 逐页 PNG（含 render-manifest）
    └── vault_import.py           # lookup / plan / commit 三态入库
```

## 脚本

```sh
python3 scripts/check_environment.py
python3 scripts/extract_pptx.py 课件.pptx --out WORK/extracted
python3 scripts/render_pptx.py 课件.pptx --out WORK/rendered
python3 scripts/vault_import.py lookup --course 地质学 --source 照片1.jpg
python3 scripts/vault_import.py plan   --course 地质学 --title '2026-09-13 地质学' \
  --note WORK/draft.md --source 照片1.jpg
python3 scripts/vault_import.py commit --course 地质学 --title '2026-09-13 地质学' \
  --note WORK/draft.md --source 照片1.jpg
```

`lookup` / `plan` 只读，`commit` 才写入；`commit` 先写导入凭据与附件、最后发布笔记，任一步失败只回收本次新建的文件，已存在的同名笔记与附件一律拒绝覆盖。

## 用法

在 Agent 会话里说「课堂笔记入库 / 课件整理入库」并附上照片或课件即可，技能会走完整流程（清点 → 逐张识读 → 完整摘录 → 逐页复核 → plan → commit）。也可以只把本仓库当脚本集使用，流程细节见 `SKILL.md`。

不适用：普通知识问答、论文检索、把已有笔记改写成摘要。

## 发布内容与隐私

仓库只分发技能、插件源代码、合成测试和通用配置模板。不要提交 references/vault.json、DSH 凭据库、环境变量文件、录音、课件原件、转写、会话日志、模型或运行缓存。插件的 .gitignore 和包文件白名单排除这些内容；提交前仍需检查暂存区。

## 许可

主技能适用 [MIT](LICENSE)。可选录音插件保留其 package.json 的 UNLICENSED 标记；第三方模型和运行时适用各自许可。笔记风格与核对规则是本技能的主要价值，欢迎按自己的课程改造 `references/` 下的规则。
