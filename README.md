# mori

本项目的特点？它不依赖LLM本身的规模，让你不要花token费，嗯，就这么简单。
呐呐，谁叫vedal声称neuro-sama是2B q2k呢？那么我只能用4B q4km来测试咯？虽然还是没那么小，但我是不能忍受弱智的。
不想拉踩，但是反正比其他类似的项目配置要求更低，我这台20c40t 32gbram 16gbvram的电脑都能带
然后它有一个非常特殊的记忆，和市面上所有类似项目的解决方案都不一样，只是不想写日记而已啦。
没了！其余的就是我莫名其妙的python洁癖了！
此外这个项目里用的inox2d是改过的，你问我为什么不pr？我不知道！

本仓库是 Mori 的聚合入口（monorepo-like），其中：

- `model/`：本地模型文件（`.gguf`），不进 git
- `mori_memory/`：LuaJIT 记忆核心（通过 `lupa` 绑定）
- `mori_llm/`：LLM 行为与 llama.cpp 驱动
- `mori_tts/`、`mori_live2d/`：各自独立子仓库（submodule）

## 初始化（首次 clone）

```bash
git clone --recurse-submodules <this-repo>
cd mori
git submodule update --init --recursive
```

## 创建 venv 并安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 准备模型

把 chat / embedding 两个 `.gguf` 放到 `model/`（目录已在 `.gitignore` 里）。

## 统一配置（推荐）

仓库根目录现在支持统一配置文件：`mori.config.json`。

这些入口都会自动优先读取它：

- `python3 main.py`
- `python3 vtuber.py`
- `python3 scripts/run_bili_vtuber_love2d.py`
- `python3 scripts/run_bili_vtuber_inochi.py`

也可以显式指定：

```bash
python3 vtuber.py --config /path/to/mori.config.json
```

默认规则：

- 如果当前目录或仓库根目录存在 `mori.config.json`，就会自动加载
- 配置里的值会作为默认值使用，命令行显式传参仍然可以覆盖
- 配置里的相对路径会按 `mori.config.json` 所在目录解析
- `mori.config.json` 里已经按 `common / tts / cli / vtuber / love2d / inochi` 分组

你可以直接编辑仓库里的 `mori.config.json`，然后用最短命令启动。

## 安装 TTS（LuxTTS）

当前 TTS 链路改成了 `LuxTTS` 的 Python 接入，不再依赖仓库内的 Candle/LuaJIT native 模块。

在当前 venv 里安装：

```bash
bash mori_tts/scripts/install_lux_tts.sh
```

默认模型使用 Hugging Face 上的 `YatharthS/LuxTTS`，首次运行时会自动下载到本机缓存。

## 运行

如果已经写好 `mori.config.json`，最简单就是：

```bash
python3 main.py
```

默认也会从 `model/` 里挑一个 chat 和 embedding 模型；也可以显式指定：

```bash
python3 main.py \
  --chat-model model/<chat>.gguf \
  --embed-model model/<embed>.gguf
```

开启语音输出：

```bash
python3 main.py --tts \
  --tts-model YatharthS/LuxTTS \
  --tts-prompt-wav /path/to/prompt.wav
```

如果你想调 LuxTTS 的生成速度/风格，可以加这些参数：

```bash
python3 main.py --tts \
  --tts-model YatharthS/LuxTTS \
  --tts-prompt-wav /path/to/prompt.wav \
  --tts-num-steps 4 \
  --tts-guidance-scale 3.0 \
  --tts-t-shift 0.5 \
  --tts-speed 1.0
```

如果你的 llama.cpp 不在默认路径，设置其中之一：

- `LLAMA_CPP_BIN_DIR=/path/to/llama-cpp/build/bin`
- `LLAMA_CPP_DIR=/path/to/llama-cpp`（会自动解析到 build/bin）

## 最基础 AI VTuber（字幕 + 可选 TTS）

入口：`vtuber.py`

如果已经写好 `mori.config.json`，最简单就是：

```bash
python3 vtuber.py
```

```bash
python3 vtuber.py --tts --live-dir live \
  --tts-model YatharthS/LuxTTS \
  --tts-prompt-wav /path/to/prompt.wav
```

LuxTTS 不是流式生成，但当前运行时会边收到 LLM 输出边分段提交，所以仍然能较快地产生分段 wav：

```bash
python3 vtuber.py --tts --live-dir live \
  --tts-model YatharthS/LuxTTS \
  --tts-prompt-wav /path/to/prompt.wav \
  --tts-num-steps 4 \
  --tts-guidance-scale 3.0 \
  --tts-t-shift 0.5
```

它会持续写入：

- `live/subtitle.txt`（OBS 文本源可直接“从文件读取”）
- `live/events.jsonl`
- `live/audio/turn_XXXX_seg_YY.wav`（启用 `--tts` 时，按段生成，便于低延迟播放/打断）

### Bilibili 弹幕（参考 my-neuro）

```bash
python3 vtuber.py --bilibili-room-url 'https://live.bilibili.com/<room_id>' --bilibili-interval 2 --tts --bilibili-exit-when-offline
```

默认只处理“启动后”的新弹幕；想立即验证链路可加：`--bilibili-catchup 1`。

### Inochi2D（Love2D 前端，WIP）

启动 Love2D 前端（Inox2D 渲染 `.inx/.inp` + 轮询字幕/音频驱动嘴形，仍是 WIP）：

```bash
python3 -m mori_live2d.cli build-inox2d
love mori_live2d/love2d_frontend
```

更多说明见 `mori_live2d/README.md`。
