# classroom-notes · 完整课堂与视频学习笔记

把课堂课件、可选录音和学习视频整理成可以直接学习的 Obsidian 带图笔记。保留完整知识点和讲解过程，通过主题融合、段落与阅读分层减轻负担。文件名使用日期加本节具体内容，例如 **`26.9.27 沉积岩的形成`**，课程名用于文件夹。

仓库根目录是 DSH 的课堂笔记技能，另附 [Codex 版本](variants/codex/classroom-notes/SKILL.md)、[在线视频技能](plugins/online-course-materials/README.md) 和 [本地录音插件](plugins/dsh-classroom-audio/README.md)。更新记录见 [CHANGELOG.md](CHANGELOG.md)。

## 笔记呈现

- 课件中的定义、公式、表格、条件、例外和图示关系完整保留；老师的解释链、推导、例子与有效问答融入对应知识点。
- 名词解释与老师强调使用普通引用，知识原文、术语/主题及内部段落、列表和顺序保留，不使用提示框或折叠。其他受保护的学习栏目保持原有名称与结构。
- 成稿从知识正文开始。出处、课件页码、录音时间、日期判断和处理记录只保存在内部工作文件中。
- 简短主线帮助阅读，不能替代完整内容。较长普通案例可收进可展开的完整讲解；定义、关键推导、适用条件及老师强调直接显示。
- 修改已有文件先备份，逐项对照完整栏目内容，保留个人批注。材料中的真实缺口在相关位置简短说明，不补造。

```markdown
> 名词解释：完整定义原句，保留限定、否定、括号与例子。

> 老师强调：保留有实际发言依据的完整强调内容。
>
> 多段讲解或列表继续保留在同一引用中。
```

## 课堂材料与视频

独立照片、PPTX、PDF逐张/逐页核对。录音完整分页读取，普通讲解融合到知识点，不把转写简单附在课件摘录后。

在线视频交给配套技能按时间序列审阅。它支持课件、板书、图解、科普动画、实验实拍、软件演示、口述及混合内容；不预设固定板书区域。动画保留起始条件、变化、关键转折和结果，有实质疑点时才精读或补图。科普没有教师依据时不强加“老师强调”或考试栏目。候选采样仍可能遗漏微小或短暂变化，真实未核实内容保留缺口说明。

成功入库后，在线视频技能清理本次网站任务下载的视频、音轨、派生音频和 ASR 媒体缓存，保留需要的图片、字幕/转写、审阅记录和来源指纹。失败任务与用户本地原件保留。

## 安装 DSH 技能

```sh
git clone https://github.com/Entropicmaker/classroom-notes.git ~/.dsh/skills/classroom-notes
cd ~/.dsh/skills/classroom-notes
cp references/vault.example.json references/vault.json
$EDITOR references/vault.json
python3 scripts/check_environment.py
```

在 `vault.json` 填写当前用户的绝对库路径：`vault` 为库根，`courses_dir` 为课程目录，`attachments_dir` 为课程附件目录，`aliases` 为课程别名。该个人配置不纳入版本控制。

已有安装先保留个人配置和未提交修改，再合并仓库更新。配套录音插件的依赖、模型准备与 DSH profile 注册见其 [安装说明](plugins/dsh-classroom-audio/README.md)；在线技能的依赖和素材目录设置见其 [安装说明](plugins/online-course-materials/README.md)。

## Codex 版本

```sh
mkdir -p ~/.codex/skills/classroom-notes
cp -R variants/codex/classroom-notes/. ~/.codex/skills/classroom-notes/
cd ~/.codex/skills/classroom-notes
cp references/vault.example.json references/vault.json
$EDITOR references/vault.json
```

该版本保留课件核对、录音融合和同一笔记风格；不要求 Codex 加载 DSH 专用原生工具。

## 入库脚本

```sh
python3 scripts/vault_import.py lookup --course 地质学 --source 课件.pptx
python3 scripts/vault_import.py plan --course 地质学 \
  --title '26.9.27 沉积岩的形成' --note WORK/draft.md --source 课件.pptx
python3 scripts/vault_import.py commit --course 地质学 \
  --title '26.9.27 沉积岩的形成' --note WORK/draft.md --source 课件.pptx
```

`lookup`、`plan` 只读，`commit` 才写入。默认验证真实日期与本节主题，拒绝仅用课程名命名；一级标题须与文件名一致。用户明确选择特殊文件名时才用 `--custom-title`。同名文件拒绝覆盖，重复材料通过导入收据识别，个人改动受保护。

## 依赖与验证

课件提取与入库脚本使用 Python 标准库。照片需要支持图像的模型；macOS 的 Vision OCR 可作纯文字兜底，不能代替图示关系核对。PPTX/PDF视觉核对可使用 Microsoft PowerPoint、LibreOffice 和 Poppler。在线视频依赖见配套技能，本地 ASR 依赖见录音插件。

仓库分发技能、程序源码、合成测试和通用配置模板。个人库路径、录音、课件、转写、会话、凭据、模型和运行依赖缓存保留在本地。所有测试均不能证明任意课堂录音识别准确或每个视频状态都已被理解。

## 许可

课堂与在线视频技能适用 [MIT](LICENSE)。可选录音插件保留其 `UNLICENSED` 标记；第三方模型、运行时与其他依赖适用各自许可。
