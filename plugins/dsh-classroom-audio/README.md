# DSH Classroom Audio 1.1.1

为 DSH 提供课堂长录音转写，默认在 Apple Silicon Mac 上离线运行 Qwen3-ASR，保留每个文件的独立时间轴。配合仓库根目录的 classroom-notes 技能，将课堂讲解融合到完整课件笔记中。

## 运行条件

- Apple Silicon Mac，Metal GPU 可用。
- 已安装 DSH 和 Node.js 22.16 或更高版本。
- Python 3.12 独立虚拟环境，依赖见 requirements-local.txt。
- 本机已有 Qwen3-ASR-1.7B 与 Qwen3-ForcedAligner-0.6B 模型；模型、录音和运行缓存不随仓库分发。

默认插件目录是当前用户的 `~/.dsh/vendor/dsh-classroom-audio`，Python 环境为其中的 `.venv`。模型目录为 `~/.dsh/models/Qwen3-ASR-1.7B` 和 `~/.dsh/models/Qwen3-ForcedAligner-0.6B`。代码通过当前用户主目录解析这些通用路径。

## 安装

从仓库根目录复制插件到默认位置。已有安装先备份，保留原有 .venv，不覆盖凭据或模型。

```sh
mkdir -p ~/.dsh/vendor/dsh-classroom-audio
cp -R plugins/dsh-classroom-audio/. ~/.dsh/vendor/dsh-classroom-audio/
cd ~/.dsh/vendor/dsh-classroom-audio
npm ci
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-local.txt
dsh plugin --profile web add "$PWD"
```

其他 profile 需单独安装，例如将 web 改为 headless。模型按各自项目说明准备到上述目录；运行期使用 HF_HUB_OFFLINE=1，不自动下载。更新代码后退出旧 DSH 进程，再启动并重新加载技能；同一端口只能运行一个 web 实例。

- [Qwen3-ASR 官方项目](https://github.com/QwenLM/Qwen3-ASR)
- [社区 MLX 运行时](https://github.com/moona3k/mlx-qwen3-asr)

## 工作流程

`classroom_audio attachments → prepare → start → poll → read`

1. doctor 检查运行环境；attachments 列出当前会话真实上传的录音。
2. prepare 默认 engine=local，接收会话附件或工作区文件。外部路径由 DSH 按文件权限复制进工作区后使用。
3. 本地不传 vocabulary。术语表用于转写后校订，避免模型复述提示词而引发错误对齐。云端仍可使用受限词表。
4. start 一次启动后台任务，顺序处理录音并共享 GPU 锁。进程中断可以续跑；确定性内容错误应先检查原因。
5. poll 更新状态并发布转写文件；任一文件完成即可分页 read，持续读到 next_offset=null。空闲时使用主技能的 scripts/wait_audio.py 有界等待，不能等待派生转写文件出现后才调用 poll。

每次支持 1–4 个文件，单份不超过 120 分钟，总计不超过 180 分钟。普通本地手机双声道默认混合为单声道并记录转换参数；也可按已知麦克风分配选择声道。

## 1.1.1 修复

- 禁用本地词表提示，保留云端词表支持。
- 时间戳验证通过后才写可复用分段缓存；失败结果另存为诊断证据。
- 拒绝越界、NaN 和无穷时间戳，不通过放宽校验掩盖异常。
- 区分进程中断和确定性对齐失败，避免盲目重启。
- 等待附件输出文件打开后再接收数据，避免失败清理后异步留下临时文件。

## 数据边界

本地识别不需要 API Key，不向云端 ASR 上传录音，也不会在失败后自动转云端。DSH 整理文本时仍可能将课件和转写发给其配置的文字模型服务，不能将整个流程称为完全离线。

工作区 .dsh-work/classroom-audio/ 保存原件副本、转换音频、分段与转写，应保持私有并被版本控制忽略。自动识别不等于人工听核；未安装说话人分离，发言者身份和置信度保持未知。无文字时间段不能自动视为静音。

仅用户明确选择并授权云端时才使用百炼。用户本人运行 `python3 bin/setup-key.py`，密钥通过隐藏输入和标准输入写入 DSH 原生凭据库。仓库不含真实密钥、凭据库、录音或会话；测试中的凭据均为虚构值。

## 测试

```sh
npm test
python3 -m unittest discover -s test -p 'test_*.py'
```

19 项 Node 测试覆盖 DSH 工具注册、会话附件、合成音频、边界保护和模拟云端流程；5 项 Python 测试覆盖时间戳与缓存。测试需要已安装的 DSH；纯 Python 测试不需要加载模型。整堂录音性能和识别准确率取决于硬件及录音条件，短片段回归不构成完整课堂准确率保证。

插件 package.json 保留 UNLICENSED 标记；第三方运行时和模型适用各自许可。
