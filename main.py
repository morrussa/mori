from __future__ import annotations

import argparse
import os
import random
from pathlib import Path

from mori_llm.llama_cpp_cli import (
    iter_lua_sequence,
    pick_default_chat_model,
    pick_default_embed_model,
    resolve_llama_cpp_bin_dir,
)
from mori_llm.pipeline import MoriPipeline
from mori_memory.bridge import MoriMemoryBridge


def _blocks_to_messages(blocks: object) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for block in iter_lua_sequence(blocks):
        try:
            role = str(block["role"])  # type: ignore[index]
            content = str(block["content"])  # type: ignore[index]
        except Exception:
            continue
        role = role.strip()
        content = content.strip()
        if role and content:
            out.append({"role": role, "content": content})
    return out


def _remove_cot(text: str) -> str:
    marker = "</think>"
    idx = text.find(marker)
    if idx != -1:
        text = text[idx + len(marker) :]
    text = text.replace("\n\n", "")
    if text.endswith("[end of text]"):
        text = text[: -len("[end of text]")]
    return text.strip()


def _default_system_prompt() -> str:
    return "\n".join(
        [
            "你是 Mori，一个本地运行的助手。",
            "优先简洁直接地回答；如果需要更多信息再提问。",
            "如果系统提示里包含【相关对话片段】或其它记忆上下文，请合理利用。",
        ]
    )


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parent
    model_dir = repo_root / "model"

    parser = argparse.ArgumentParser(prog="mori", description="Mori entrypoint (memory + llama.cpp).")
    parser.add_argument("--workdir", default=str(repo_root), help="Working directory (stores memory/ by default).")
    parser.add_argument("--llama-bin-dir", default=None, help="Path to llama.cpp build/bin.")

    parser.add_argument("--chat-model", default=None, help="Path to chat .gguf model (default: pick from model/).")
    parser.add_argument(
        "--embed-model", default=None, help="Path to embedding .gguf model (default: pick from model/)."
    )

    parser.add_argument("--ctx-size", type=int, default=0, help="llama-server --ctx-size (0 = 8192).")
    parser.add_argument("--n-predict", type=int, default=512, help="llama-cli --n-predict.")
    parser.add_argument("--temp", type=float, default=0.7, help="llama-cli --temp.")
    parser.add_argument("--top-p", type=float, default=0.9, help="llama-cli --top-p.")
    parser.add_argument("--system", default=_default_system_prompt(), help="Base system prompt.")

    args = parser.parse_args()

    if not model_dir.is_dir():
        raise FileNotFoundError(f"model/ directory not found: {model_dir}")

    args.chat_model = str(Path(args.chat_model).expanduser()) if args.chat_model else str(pick_default_chat_model(model_dir))
    args.embed_model = str(Path(args.embed_model).expanduser()) if args.embed_model else str(pick_default_embed_model(model_dir))

    args.llama_bin_dir = str(resolve_llama_cpp_bin_dir(args.llama_bin_dir))
    return args


def main() -> int:
    args = parse_args()

    workdir = Path(args.workdir).expanduser().resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    os.chdir(workdir)
    (workdir / "memory").mkdir(parents=True, exist_ok=True)
    (workdir / "memory" / "v4" / "topic_graph").mkdir(parents=True, exist_ok=True)

    repo_root = Path(__file__).resolve().parent
    model_path = Path(args.chat_model).resolve()
    embed_model_path = Path(args.embed_model).resolve()

    pipeline = MoriPipeline(
        llama_bin_dir=args.llama_bin_dir,
        large_ctx_size=int(args.ctx_size) if int(args.ctx_size) > 0 else 8192,
        embed_ctx_size=8192,
    )
    pipeline.load_models_py(
        large_model_path=str(model_path),
        embedding_model_path=str(embed_model_path),
    )

    memory = MoriMemoryBridge(lua_root=repo_root / "mori_memory", py_pipeline=pipeline)

    print("Mori 已启动。输入 /exit 退出。")

    turn = 1
    while True:
        try:
            user_input = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input in {"/exit", "/quit"}:
            break

        blocks = memory.compile_context({"turn": turn, "user_input": user_input})
        messages = [{"role": "system", "content": args.system.strip()}]
        messages.extend(_blocks_to_messages(blocks))
        messages.append({"role": "user", "content": user_input})

        assistant = pipeline.generate_chat_sync_py(
            messages=messages,
            params={
                "max_tokens": int(args.n_predict),
                "temperature": float(args.temp),
                "top_p": float(args.top_p),
                "seed": random.randint(114, 514),
            },
        )
        assistant = _remove_cot(assistant)
        print(f"mori> {assistant}")

        memory.ingest_turn({"turn": turn, "user_input": user_input, "assistant_text": assistant})
        turn += 1

    memory.shutdown()
    pipeline.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
