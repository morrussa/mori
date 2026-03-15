# mori

本项目的特点？它不依赖LLM本身的规模，让你不要花token费，嗯，就这么简单。
呐呐，谁叫vedal声称neuro-sama是2B q2k呢？那么我只能用4B q4km来测试咯？虽然还是没那么小，但我是不能忍受弱智的。
不想拉踩，但是反正比其他类似的项目配置要求更低，我这台20c40t 32gbram 16gbvram的电脑都能带
然后它有一个非常特殊的记忆，和市面上所有类似项目的解决方案都不一样，只是不想写日记而已啦。
没了！其余的就是我莫名其妙的python洁癖了！

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

## 安装 TTS（qwen3_tts_rs）

TTS 的运行时与模型默认安装到 `model/tts/qwen3_tts_rs/`（同样不进 git）：

```bash
python3 mori_tts/install_qwen3_tts_rs.py --root model/tts/qwen3_tts_rs
```

默认会自动检测 NVIDIA GPU：有则下载 CUDA runtime，没有则下载 CPU runtime；也可以强制：

```bash
python3 mori_tts/install_qwen3_tts_rs.py --cuda --root model/tts/qwen3_tts_rs
python3 mori_tts/install_qwen3_tts_rs.py --cpu  --root model/tts/qwen3_tts_rs
```

## 运行

默认会从 `model/` 里挑一个 chat 和 embedding 模型；也可以显式指定：

```bash
python3 main.py \
  --chat-model model/<chat>.gguf \
  --embed-model model/<embed>.gguf
```

开启语音输出：

```bash
python3 main.py --tts
```

强制要求 CUDA runtime（如果装的是 CPU runtime 会直接报错提示重装）：

```bash
python3 main.py --tts --tts-cuda
```

如果你的 llama.cpp 不在默认路径，设置其中之一：

- `LLAMA_CPP_BIN_DIR=/path/to/llama-cpp/build/bin`
- `LLAMA_CPP_DIR=/path/to/llama-cpp`（会自动解析到 build/bin）

## 最基础 AI VTuber（字幕 + 可选 TTS）

入口：`vtuber.py`

```bash
python3 vtuber.py --tts --live-dir live
```

如果你希望确认 TTS 确实在用 GPU（CUDA runtime），加上：

```bash
python3 vtuber.py --tts --tts-cuda --live-dir live
```

它会持续写入：

- `live/subtitle.txt`（OBS 文本源可直接“从文件读取”）
- `live/events.jsonl`
- `live/audio/turn_XXXX.wav`（启用 `--tts` 时）

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
