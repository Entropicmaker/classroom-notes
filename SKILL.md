---
name: classroom-notes
description: 将课堂 PPT 照片、PPTX、课程文档与可选的课堂录音或转写融合，完整保留课件并加入有时间来源的老师讲解、强调、补充和更正，按日期与学科写入 Obsidian。支持每节课两份约45分钟录音。用于“课堂笔记入库”“课件加录音整理”“课堂纳入”等请求；不用于普通知识问答或论文检索。
---

# 课堂笔记入库（DSH）

## 目标与边界

把用户提供的课件与可选录音整理成内容忠实、可继续手动补充的 Obsidian 带图课堂笔记。课件默认完整摘录，不是摘要：所有可辨认的知识内容都要进入笔记，关键点不得删减、改义或用概括代替。有录音时按知识点融入老师的解释链、例子、强调、易错提醒、补充和更正，不把成稿变成重念 PPT 或流水账转写。课件缺失、听辨不清及转写未核实都必须明确标记。

纸笔笔记、个人理解由用户后续加入，不索取或代写。不要顺带重做旧笔记、更新课程导航、移动已有文件或修改 Obsidian 设置。配置在 `references/vault.json`；只有路径失效或课程归属确有歧义才补问。没有写入权限时使用 DSH 当前工具提供的限定路径审批机制，不得假装已入库。

技能目录由 `skill` 工具结果中的 Base directory 给出。下面所有 `references/...`、`scripts/...` 都相对该目录解析；不要假设当前工作目录就是技能目录。

## DSH 输入与工具约定

- 用户随消息附加的 PNG/JPEG/WebP/GIF 会直接进入当前多模态上下文。仍须逐张查看；当消息给出可读取的本地路径、需要复看或处理渲染页时，调用 `read_image`。若当前模型不支持图片或 `read_image` 拒绝，在 macOS 上先运行下方 Vision OCR 兜底，继续完成文字摘录；OCR 不能证明图示、版式或公式已核对，因此只要没有实际视觉复核就必须标为部分摘录，并建议切换到支持图片的模型完成复核。
- PPTX、PDF 等普通附件会以只读文件路径提供。用 `read` 查看文本，用 `bash` 执行本技能脚本；不要把二进制文件交给 `read`。
- DSH 的文字模型（包括 dsv4.1f）不具备原生听音频能力。有 M4A/MP3/WAV 等录音时，先读取 [DSH 录音操作](references/dsh-audio.md) 与 [课堂融合规则](references/audio-review.md)，使用 `classroom_audio` 插件，**默认本地 Qwen3-ASR-1.7B，无需 API Key**。用户只需上传课件和录音，附件读取、转换、分段和启动模型由 DSH 完成，不把这些操作转交用户。仅当用户明确改选云端才使用 cloud；本地失败不自动上传。不得向文字模型发送音频二进制或声称自己听过。
- 默认一堂课为两份约 45 分钟录音，按用户顺序编号 audio1/audio2，独立转写、合为一篇笔记；不是必须凑够两份。保留各自从零开始的时间戳，不能把第二份伪装成第一份连续的第 45 分钟，也不能跨文件沿用 speaker ID 身份。
- 草稿和覆盖清单放在当前工作区 `.dsh-work/classroom-notes/<本次唯一目录>/`，不要写进技能目录。最终文件由入库脚本写入 Obsidian 库。
- `read_image` 只支持 PNG/JPEG/WebP/GIF。PPTX 必须先渲染；PDF 也要先用本机可用的 `pdftoppm` 转为逐页 PNG 后再核对。

## 每次执行

1. 读取 `references/vault.json` 和 [笔记风格](references/note-style.md)。用户指定学科优先，否则依据材料标题和配置别名匹配现有课程；地质学/地貌学等歧义不能猜。按“日期与命名”确定日期和标题。
2. 清点全部输入，记录用户给出的顺序、原文件名、可读路径和来源类型。录音路径不清时调用 `classroom_audio action=attachments`，只选本次课程的录音；不要要求用户重新转换或手动复制已上传的文件。先运行 `scripts/vault_import.py lookup`，全部课件和原录音都作为 `--source`；录音不要用重采样 WAV/FLAC 代替原件，可用插件保存的字节一致附件副本。若有新增录音，另查仅课件来源是否已入库，以免忽略旧的 PPT 版。相同完整来源已存在则保护个人修改；旧 PPT 笔记存在而本次加录音时，默认另建“日期 学科（课堂融合版）”，不覆盖旧版。用户明确要求补充原笔记才在读取全文、备份后定点合并。
3. 在本次工作目录建立 `source/`、`extracted/`、`rendered/`、`assets/`、`transcripts/`、`coverage.md` 和草稿。不得遗漏尾部附件或只挑“重要页”。
4. **手机照片（主要输入）**：遵循 [照片与逐页核对](references/source-review.md)。逐张实际查看全图；需要放大或复看时对其路径调用 `read_image`。逐张完整摘录标题、正文、表格、公式、脚注和图示关系。`read_image` 不可用时，macOS 上对每张图执行 `vision_ocr.swift` 生成带坐标与置信度的 JSON 初稿；低置信度、分栏、表格、公式和图示仍不可视为已核对。
5. **PPTX**：
   - 先运行 `python3 scripts/extract_pptx.py SOURCE --out WORK/extracted`，读取 `source.md` 与 `manifest.json`；它提取文本、表格、图片、备注及可恢复的图表/关系信息，但不是完整性证明。
   - 再运行 `python3 scripts/render_pptx.py SOURCE --out WORK/rendered`。在 macOS 上脚本优先调用 Microsoft PowerPoint 高保真导出（系统可能要求一次自动化许可），失败时才回退 LibreOffice；随后用 Poppler 生成逐页 PNG 和 `render-manifest.json`。必须读取 manifest 的 `backend` 与 `warnings`；脚本在 macOS 的 LibreOffice 分支自动建立隔离的中文字体配置和缓存。同批第一份 PowerPoint 因权限失败后，剩余课件直接用 `--backend libreoffice`，不重复探测权限。先查看一张含中文的渲染页，通过后再批量核对。若仍有字体缺失，以 XML 提取补足并在覆盖清单标明，不现场反复调试办公软件。
   - 用 `read_image` 按页码读取 **每一张** 渲染页，与提取内容互补。图表、SmartArt、公式、页内图片、文字叠加和演讲者备注都要核对；渲染失败或页数不一致时不能称全部页面已核对。
6. **PDF/其他课程文档**：按实际页或内容单元完整检查。PDF 用可用的 `pdftoppm -png -r 160` 生成逐页 PNG，再逐页 `read_image`；若格式无法可靠渲染，明确披露并完成仍可处理的部分。
7. **有录音/转写时**：录音路径就绪就按 `dsh-audio.md` 启动本地转写，和课件核对交错进行；本地 prepare 省略 vocabulary，课件词表只留给转写后校订。任一份 DONE 后即可分页读到 `next_offset=null`，不等两份全结束才读；检查所有无 ASR 文字的区间，不擅自视作静音。按主题把课堂解释、强调、补充、纠错、有效提问映射到课件知识点，不能均分录音时间来猜页码。建立 `alignment.md`、`audio-coverage.md` 与 `corrections.md`；原始 ASR 保留不改。两份中任一失败或遗漏时不能宣称课堂融合已完成。不要在后台脚本中等待派生的 transcript.md 出现：它由 poll 生成，不能以它作为调用 poll 的前提。
8. 按笔记风格生成暂存 `.md`。一级标题与文件名一致，章节主题放二级标题。课堂新增内容紧邻相关知识点并标 `audio1/原文件名 00:18:32–00:19:10` 等来源；不能关联的实质内容放“课堂补充”。强调、考试、作业要求必须有可定位原句与明确发言者依据；不能只凭重复/音量判定。需要保留的图复制到草稿旁 `assets/`，正文用 `![图名](assets/安全文件名.png)`；入库脚本会改成目标笔记的相对链接。所有嵌入图片必须实际查看；已在逐页核对中查看的同一图不重复调用。裁切后用于识读的副本保持长边不超过 4096px（工具硬限 8192px），不要靠超大倍数放大恢复原图没有的细节。
9. **入库前复核**：以原始照片/渲染页和逐页摘录对照成稿，完成 `coverage.md`。定义、条件、否定、例外、数字、单位、公式、表格行列与图示关系逐项检查。录音分别记录自动转写、疑难段回听/二次转写、未核实范围；DSH 不能自行听核时明确披露。未读完继续处理；确实无法辨认或工具失败才标部分摘录，并注明具体来源、位置、范围和原因。
10. 运行 `vault_import.py plan`，核对目标路径、图片和冲突；确认 ready 后运行 `commit`，仍传完整原始来源集。用户发起“入库/整理入库”已经授权本次写入，除实际权限限制或课程歧义外不再索取批准。若 DSH 沙箱拦截，按工具提示仅申请 Obsidian 库所需路径权限后重试同一命令。
11. 简短报告课程、笔记名称、最终路径和 Obsidian 链接；分别说明课件覆盖、两份录音转写和听核范围。`present` 可用时把最终 `.md` 声明为交付物。入库脚本只迁移图片，不自动归档录音；原录音须保留，不输出不存在的音频播放链接。内部覆盖清单不当作学习正文。

## 调度与失败处理

- 首次检查集中读取所需配置，清点本次明确提供的材料；不为寻找补图去扫描未提供的其他课件。准备录音后立即 start，课件提取、渲染和核对可在其后台运行期间进行；同一 GPU 上不并发开启两份转写。
- 处理课件时约每 30–60 秒 poll 一次。无独立工作时运行 `python3 scripts/wait_audio.py --job 'prepare返回的directory' --timeout 45`；读完 audio1 后追加 `--seen audio1`。返回 audio_done/complete/failed/interrupted 后立即 poll，再 read 或处理失败；timeout 只代表本轮等待结束，报告简短进度后可再等。不能使用十分钟阻塞的 job_output 或等待两份均完成的循环。
- “进程中断”与“内容/对齐失败”不同。INTERRUPTED 可 start 续跑；INVALID_LOCAL_ALIGNMENT、LOCAL_ALIGNMENT_MISSING、LOCAL_TRANSCRIPT_TRUNCATED 不盲目重启或删缓存。旧词表任务只允许省略词表建立新任务一次；仍失败就保留失败证据、具体时间缺口及可完成的课件部分，不承诺整堂融合成功。普通课堂整理不修改插件源码或系统配置。
- 每批材料保留一份完整摘录和简洁覆盖映射，写完一个主题再集中合入草稿。核对过的内容不在 slides、coverage、alignment 中反复抄写全文。修改同一个文件须顺序进行，按刚读取的原文定位；edit 未匹配先读取目标局部，再修正一次，不重复盲替换。
- prepare 已返回时长、声道和转换结果，不重复调用 PATH 中未验证存在的 ffprobe。命令失败时保留 stderr 和退出码，不能把“工具未安装”解释为“文档没有文字”。入库后图片相对路径需 `urllib.parse.unquote` 解码再验证，避免把中文 URL 编码当断链。
- plan 的 ready 只证明路径与文件条件。课程目录为工作区时，兄弟目录 `09 附件` 仍可能被沙箱拦截；记录附件目标，实际被拦截后按 DSH 工具审批机制重试原 commit，不扩大工作区或改全局权限。

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
