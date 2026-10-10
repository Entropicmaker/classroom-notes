# online-course-materials · 学习视频整理技能

从 B 站或本地视频获取字幕/转写与全幅候选画面，按知识点整理完整带图笔记。支持课件、板书、图解、科普动画、实验实拍、软件演示、口述和混合内容。素材采样与实际审阅分开，不把候选截图当作已经理解的课件。

当前实现版本为 **0.3.0**，与仓库根目录的 `classroom-notes` 配合使用。

## 安装

先按仓库根目录说明安装 `classroom-notes` 并配置 `vault.json`。从仓库根目录复制本技能：

```sh
mkdir -p ~/.dsh/skills/online-course-materials
cp -R plugins/online-course-materials/. ~/.dsh/skills/online-course-materials/
cd ~/.dsh/skills/online-course-materials
cp references/storage.example.json references/storage.json
$EDITOR references/storage.json
python3 -m pip install --target scripts/vendor -r scripts/requirements.txt
```

`materials_root` 必须填写当前用户素材目录的绝对路径，例如桌面下的“学习ppt”。课程和课次分别存放在子目录中。`storage.json` 是本机配置，更新代码时保留它。

需要 Python 3.10 或更高版本、Node.js 和 ffmpeg/ffprobe。也可使用已安装录音插件提供的 ffmpeg/ffprobe；现有 Python 3.12 本地模型环境可运行本技能。`scripts/vendor` 由依赖安装生成，不随仓库分发。没有字幕且需要本地识别时，按 [录音插件说明](../dsh-classroom-audio/README.md) 准备已有本地运行环境和模型。

## 流程与恢复

```sh
python3 scripts/course_materials.py --help
python3 scripts/course_materials.py doctor
```

在 Agent 中给出视频和课程后，技能执行 `prepare → start/status → validate → read → review-plan/review-next/review-save → review-export → review → import-note`。已有任务通过 job/state 与检查点恢复，不重复下载、转写或整段重做。

全幅检测包含颜色与布局变化；预览按时间顺序成组提供，字幕全文分页读完。需核实的小字、公式、图例或关键变化再请求 `review-detail`，确实缺少过程证据再补图。同一内容复用已有记录，动画保留时序和中间态；细读、裁切和补图有额度，默认顺序处理。未核实知识与采样缺口如实说明。

最终命名为 `YY.M.D 本节具体内容`。完整知识与讲解过程保留，简短总结只作阅读线索。名词解释、老师强调用普通引用；成稿去掉图片出处、录音时间线和处理说明。没有教师依据的科普/动画不强加课堂栏目。

## 网站媒体清理

成功入库后自动清理当前网站任务的视频、音轨、派生音频和 ASR 媒体缓存，先保存来源指纹与清理记录。保留笔记需要的截图、字幕/转写、索引、审阅和来源身份。失败任务及用户本地原件不会清理；清理中断可恢复。

媒体已清理后，补图或重转写需要重新获取新版本。脚本校验文件、结构和额度一致性，不能证明模型实际看图或语义完整。

## 测试

先为 Python 3.10 或更高版本安装上面的依赖，再从仓库根目录运行；下例中的 `python3` 应指向该运行环境：

```sh
python3 -m unittest discover -s tests -p 'test_online_*.py'
```

测试使用临时目录、合成图像/视频与模拟 ASR，不调用真实模型或云端识别。阅读规则见 [SKILL.md](SKILL.md)，审阅规则见 [visual-review.md](references/visual-review.md)，素材与清理契约见 [material-contract.md](references/material-contract.md)。
