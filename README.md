# mori

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

如果你的 llama.cpp 不在默认路径，设置其中之一：

- `LLAMA_CPP_BIN_DIR=/path/to/llama-cpp/build/bin`
- `LLAMA_CPP_DIR=/path/to/llama-cpp`（会自动解析到 build/bin）
