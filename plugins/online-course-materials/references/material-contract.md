# 素材包与审阅契约（技能0.3.0）

每P独立job，state.json可恢复进度，bundle/manifest.json为交接入口；兼容lecture-materials/1.0。稳定身份为平台+BVID+CID+本P范围，本地视频使用原件SHA。标题、整理日期和字幕版本不改变身份；SQLite与课堂收据支持中断后恢复，个人改动不覆盖。

## 材料与限制

- source-ref.json/source.md：身份、链接、分P、日期依据。媒体仅清理前保存在source/；清理后media-archive.json保留指纹，不能称原媒体仍可本地播放。
- transcripts/raw、segments.jsonl、transcript.md：原字幕/ASR及本P毫秒时间，全部原始文字保留。coverage.jsonl记录文字可用/无文字待核实区间（>1秒），自动文字不证明识别正确。
- visuals/images、index.jsonl：最大宽1920的全幅候选截图及SHA、时间和重复出现时间。字节相同可合并，近似图不自动当重复。scene/status由实际审阅填，不是脚本推断；候选编号不是PPT页码。
- visuals/events.jsonl、scan.json：原帧检测范围/参数，全部检测事件和是否出图。全幅320×180 RGB检测，含色彩图例/高亮变化，默认350ms事件间隔、30秒锚点；不预设教学ROI，仍可能漏微小/极短变化。超2000张本地额度时按全片时间窗选切换/稳定状态，sampling_windows明确未出图事件，不能称逐帧语义完整。旧包没有events仍可读/验证。
- alignment.jsonl仅给时间邻接候选，不是旁白与画面的语义匹配。
- draft/visual-review：顺序预览、细读/裁切请求、检查点和额度。完整审阅规则在 [visual-review.md](visual-review.md)。该工作目录可增量更新，最终review固化证据；补图保留未变的记录/计数，仅重审改变的时间组。
- review.json：完整转写读取、每个候选去向及审阅等级、无文字区间、密集采样区间、序列证据、操作计数和草稿SHA。脚本检查结构/文件一致性，不证明代理真的看图、听核或语义完整。

read对transcript/visuals/coverage/alignment分页到next_offset=null。visual index可供查路径；实际审阅入口是review-next，不要求把整个index重复打印到上下文。previewed是真实缩略图审阅，viewed是原图细读，duplicate须已核对来源和内容依据。needs_detail继续核对；真实无法辨认用unreadable，额度/工具导致具体疑点用unresolved；不能把尚未做完的工作冒充无法完成。

## 缺口与放行

无文字区间逐一对应manifest.quality.unknown_text_ranges，另存JSON数组供review-export：

```json
[
  {"begin_ms": 12000, "end_ms": 15000, "status": "unresolved", "basis": "字幕缺段，无法听核；正文披露此范围"}
]
```

无缺口用[]。no_teaching_content_verified只有实际听核或可定位等效证据才用；波形/VAD/单张截图不足以证明无讲解。媒体探测确无音轨的视觉视频单独记录audio_track_present=false，不虚构旁白或把全片误判字幕缺段。

sampling_reviews逐一对应scan.sampling_windows，status为sequence_reviewed或unresolved，basis给时序核对依据或具体视觉疑点。仍须说明候选采样，不承诺未出图的每个状态均被核对。

使用review-export生成审阅文件，需真实完成成稿复核、--note-reviewed、匹配草稿SHA。review入口校验新包必须来自已保存检查点/计数；旧包的逐图审阅格式兼容保留。未知/无法辨认/未核实的图、无文字区间或采样疑点，相应知识点简短注明疑点；未解决的覆盖缺口在文末用一两句“部分摘录”说明受影响的内容范围，时间索引、模型/核对过程只保存在内部记录。正文按主题组织，不塞逐帧覆盖表。修改草稿后重新复核、export和review；补图/强制ASR撤销旧review。

## 入库、保留与清理

storage.json指定的素材根目录（安装时填写当前用户的绝对路径），课程/完整日期课次标题来源短标识分层。草稿在JOB/draft，图复制到旁边assets，使用标准相对Markdown链接。最终文件名/H1用YY.M.D 本节具体主题；课程名用于文件夹。现有目录不移动。

来源集合固定为manifest.source_files，派生WAV不是原视频。用import-note包装lookup/plan/commit，要求reviewed和草稿SHA一致，同名拒绝覆盖。成功入库后自动清理当前网站job的下载/合并视频、分轨/派生音频和ASR媒体缓存，先持久化media-archive再按限定清单删除，可重试恢复。保留截图/索引/检测事件、字幕/转写、草稿、审阅及来源指纹；不清理本地原件、失败任务或课次外文件。

validate清理前校验媒体与全部包文件，清理后校验保留文件和清理记录，不证明语义完整。已清理后补图/重转写需prepare --new-version重新获取，不能用改变的来源集合绕过去重。隔离测试用--config，不作为权限绕过。
