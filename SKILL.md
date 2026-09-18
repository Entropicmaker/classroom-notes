---
name: classroom-notes
description: 将手机拍摄的课堂 PPT、PPTX 或课程文档逐张核对并完整整理为 Obsidian 带图笔记，按日期与学科直接写入你的 Obsidian 库。用于“课堂笔记入库”“课件整理入库”等请求；不用于普通知识问答或论文检索。
---

# 课堂笔记入库（DSH）

## 目标与边界

把用户提供的课件整理成内容忠实、可继续手动补充的 Obsidian 带图课堂笔记。默认是完整摘录，不是摘要：所有可辨认的知识内容都要进入笔记，关键点不得删减、改义或用概括代替。不能保证照片未拍到、反光遮挡或无法辨认的内容；必须保留证据并明确标记缺失。

纸笔笔记、个人理解由用户后续加入，不索取或代写。不要顺带重做旧笔记、更新课程导航、移动已有文件或修改 Obsidian 设置。配置在 `references/vault.json`；只有路径失效或课程归属确有歧义才补问。没有写入权限时使用 DSH 当前工具提供的限定路径审批机制，不得假装已入库。

技能目录由 `skill` 工具结果中的 Base directory 给出。下面所有 `references/...`、`scripts/...` 都相对该目录解析；不要假设当前工作目录就是技能目录。

## DSH 输入与工具约定

- 用户随消息附加的 PNG/JPEG/WebP/GIF 会直接进入当前多模态上下文。仍须逐张查看；当消息给出可读取的本地路径、需要复看或处理渲染页时，调用 `read_image`。若当前模型不支持图片或 `read_image` 拒绝，在 macOS 上先运行下方 Vision OCR 兜底，继续完成文字摘录；OCR 不能证明图示、版式或公式已核对，因此只要没有实际视觉复核就必须标为部分摘录，并建议切换到支持图片的模型完成复核。
- PPTX、PDF 等普通附件会以只读文件路径提供。用 `read` 查看文本，用 `bash` 执行本技能脚本；不要把二进制文件交给 `read`。
- 草稿和覆盖清单放在当前工作区 `.dsh-work/classroom-notes/<本次唯一目录>/`，不要写进技能目录。最终文件由入库脚本写入 Obsidian 库。
- `read_image` 只支持 PNG/JPEG/WebP/GIF。PPTX 必须先渲染；PDF 也要先用本机可用的 `pdftoppm` 转为逐页 PNG 后再核对。

## 每次执行

1. 读取 `references/vault.json` 和 [笔记风格](references/note-style.md)。用户指定学科优先，否则依据材料标题和配置别名匹配现有课程；地质学/地貌学等歧义不能猜。按“日期与命名”确定日期和标题。
2. 清点全部输入，记录用户给出的顺序、原文件名、可读路径和来源类型。先运行 `scripts/vault_import.py lookup` 检查同一材料是否已导入。已存在则告知位置并保护个人修改；明确重做才另建版本。
3. 在本次工作目录建立 `source/`、`extracted/`、`rendered/`、`assets/`、`transcripts/`、`coverage.md` 和草稿。不得遗漏尾部附件或只挑“重要页”。
4. **手机照片（主要输入）**：遵循 [照片与逐页核对](references/source-review.md)。逐张实际查看全图；需要放大或复看时对其路径调用 `read_image`。逐张完整摘录标题、正文、表格、公式、脚注和图示关系。`read_image` 不可用时，macOS 上对每张图执行 `vision_ocr.swift` 生成带坐标与置信度的 JSON 初稿；低置信度、分栏、表格、公式和图示仍不可视为已核对。
5. **PPTX**：
   - 先运行 `python3 scripts/extract_pptx.py SOURCE --out WORK/extracted`，读取 `source.md` 与 `manifest.json`；它提取文本、表格、图片、备注及可恢复的图表/关系信息，但不是完整性证明。
   - 再运行 `python3 scripts/render_pptx.py SOURCE --out WORK/rendered`。在 macOS 上脚本优先调用 Microsoft PowerPoint 高保真导出（系统可能要求一次自动化许可），失败时才回退 LibreOffice；随后用 Poppler 生成逐页 PNG 和 `render-manifest.json`。必须读取 manifest 的 `backend` 与 `warnings`；回退渲染如有字体缺失，要以 XML 提取补足并在覆盖清单标明。
   - 用 `read_image` 按页码读取 **每一张** 渲染页，与提取内容互补。图表、SmartArt、公式、页内图片、文字叠加和演讲者备注都要核对；渲染失败或页数不一致时不能称全部页面已核对。
6. **PDF/其他课程文档**：按实际页或内容单元完整检查。PDF 用可用的 `pdftoppm -png -r 160` 生成逐页 PNG，再逐页 `read_image`；若格式无法可靠渲染，明确披露并完成仍可处理的部分。
7. 按笔记风格生成暂存 `.md`。一级标题与文件名一致，章节主题放二级标题。需要保留的图复制到草稿旁 `assets/`，正文用 `![图名](assets/安全文件名.png)`；入库脚本会改成目标笔记的相对链接。所有嵌入图片必须实际查看。
8. **入库前复核**：以原始照片/渲染页和逐页摘录对照成稿，完成 `coverage.md`。定义、条件、否定、例外、数字、单位、公式、表格行列与图示关系逐项检查。未核对来源不能跳过；确实无法辨认或处理的部分要在正文注明具体来源、位置、范围和原因，并标为部分摘录。
9. 运行 `vault_import.py plan`，核对目标路径、图片和冲突；确认 ready 后运行 `commit`。用户发起“入库/整理入库”已经授权本次写入，除实际权限限制或课程歧义外不再索取批准。若 DSH 沙箱拦截，按工具提示仅申请 Obsidian 库所需路径权限后重试同一命令。
10. 简短报告课程、笔记名称、最终路径和 Obsidian 链接；`present` 工具可用时把最终 `.md` 声明为交付物。有未辨认内容必须明确告知。覆盖清单只留在工作目录，不当作学习正文。

## 日期与命名

- 默认文件名和一级标题为 `YYYY-MM-DD 学科`；学科使用配置别名归一后的课程文件夹名。章节名放正文，不替代文件名。用户明确指定其他文件名时优先。
- 日期优先级：用户明确给出的上课日期 → 可靠来源能确定的课程日期（照片可用原始拍摄日期）→ 用户当前时区的处理日期。文件复制/修改时间、PPT 内引用年份、模板日期不能充当上课日期。使用处理日期时在正文写“未提供上课日期，采用整理日期”。
- 一批照片明显属于不同上课日时分开生成；跨午夜、同一节课多次拍摄或日期冲突无法确定时不擅自拆课。
- 同日同学科不同批次使用 `YYYY-MM-DD 学科（02）` 并递增；重复材料仍先 lookup。明确重做使用 `YYYY-MM-DD 学科（重整版）`，冲突则加序号，并向 plan/commit 传 `--new-version`。

## 固定目录与保护规则

- 笔记：`<vault>/<courses_dir>/<现有课程>/<日期 学科>.md`，两段目录名取自 `references/vault.json`。
- 图片：`<vault>/<attachments_dir>/<课程>/<标题-内容指纹>/`。
- 导入记录：课程文件夹内 `.classroom-imports/`；只用于重复材料识别，不证明内容完整。
- 同名笔记存在时拒绝覆盖；不得通过删除重建规避。脚本只验证文件与入库条件，不替代逐页语义复核。
- 补充已有笔记时先读目标全文，保护用户小记和改动。入库脚本只支持新建；修改既有文件须定点编辑并保留原文副本。
- 新课程只有在用户明确指定后才创建；脚本不会自动创建课程、清理旧文件或批量改写全库。

## 脚本命令模板

```sh
python3 scripts/check_environment.py
mkdir -p '/path/to/work/vision-cache'
CLANG_MODULE_CACHE_PATH='/path/to/work/vision-cache' SWIFT_MODULECACHE_PATH='/path/to/work/vision-cache' /usr/bin/swift scripts/vision_ocr.swift '/path/to/photo01.jpg' > '/path/to/work/transcripts/photo01-ocr.json'
python3 scripts/extract_pptx.py '/path/to/source.pptx' --out '/path/to/work/extracted'
python3 scripts/render_pptx.py '/path/to/source.pptx' --out '/path/to/work/rendered'
python3 scripts/vault_import.py lookup --course '地质学' --source '/path/to/photo01.jpg' --source '/path/to/photo02.jpg'
python3 scripts/vault_import.py plan --course '地质学' --title '2026-09-13 地质学' --note '/path/to/work/draft.md' --source '/path/to/photo01.jpg'
python3 scripts/vault_import.py commit --course '地质学' --title '2026-09-13 地质学' --note '/path/to/work/draft.md' --source '/path/to/photo01.jpg'
```

多份来源重复 `--source` 并保持输入顺序。拆成不同日期/学科时每份笔记只传对应来源。`lookup`/`plan` 只读，`commit` 才写入。`--config` 仅用于明确选择其他库或隔离测试，不能绕过目标范围。
