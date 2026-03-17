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
from mori_tts.cosyvoice3_tts import (
    COSYVOICE3_DEFAULT_MODEL,
    COSYVOICE3_DEFAULT_PROMPT_PREFIX,
    CosyVoice3TTS,
)


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
    tts_root = model_dir / "tts" / "cosyvoice3"

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

    parser.add_argument("--tts", action="store_true", help="Enable TTS (cosyvoice3).")
    parser.add_argument("--tts-root", default=str(tts_root), help="CosyVoice3 model root dir (contains <model>/config.json).")
    parser.add_argument("--tts-model", default=COSYVOICE3_DEFAULT_MODEL, help="Model dir name under <tts-root>/ (or pass an absolute dir).")
    parser.add_argument(
        "--tts-mode",
        choices=["zero_shot", "cross_lingual", "instruct"],
        default="zero_shot",
        help="Synthesis mode: zero_shot (needs prompt text+wav), cross_lingual (wav only), instruct (instruct+wav).",
    )
    parser.add_argument("--tts-device", choices=["auto", "cpu", "cuda", "metal"], default="auto", help="Device for inference.")
    parser.add_argument("--tts-f16", action="store_true", help="Use FP16 precision (GPU only).")
    parser.add_argument("--tts-prompt-wav", default="", help="Reference voice audio path (required when --tts is on).")
    parser.add_argument("--tts-prompt-text", default="", help="Full prompt_text for zero_shot (prefix+transcript).")
    parser.add_argument("--tts-prompt-transcript", default="", help="Transcript of prompt wav (appended to required prefix).")
    parser.add_argument("--tts-instruct-text", default="", help="Instruction text for instruct mode.")
    parser.add_argument("--tts-n-timesteps", type=int, default=10, help="Number of flow sampling steps (default: 10).")
    parser.add_argument(
        "--tts-out-dir",
        default="tts_out",
        help="Output directory for wav files (relative to --workdir unless absolute).",
    )

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
    tts_out_dir = Path(args.tts_out_dir).expanduser()
    if not tts_out_dir.is_absolute():
        tts_out_dir = workdir / tts_out_dir
    tts_out_dir = tts_out_dir.resolve()
    tts_out_dir.mkdir(parents=True, exist_ok=True)

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
    print("命令：/tts on|off|toggle  切换语音输出。")

    tts_enabled = bool(args.tts)
    tts_engine: CosyVoice3TTS | None = None
    if tts_enabled:
        prompt_wav = str(args.tts_prompt_wav or "").strip()
        if not prompt_wav:
            print("tts> 失败：缺少 --tts-prompt-wav（参考音频）")
            tts_enabled = False
        else:
            try:
                tts_engine = CosyVoice3TTS(
                    tts_root=args.tts_root,
                    model=args.tts_model,
                    device=args.tts_device,
                    use_f16=bool(args.tts_f16),
                )
                print(
                    "tts> cosyvoice3"
                    f" mode={args.tts_mode}"
                    f" device={args.tts_device}"
                    f" f16={bool(args.tts_f16)}"
                    f" sr={tts_engine.sample_rate}"
                    f" model={tts_engine.paths.model_dir}"
                )
            except Exception as e:
                print(f"tts> init failed: {e}")
                tts_enabled = False
    turn = 1
    while True:
        try:
            user_input = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input.startswith("/tts"):
            parts = user_input.split()
            if len(parts) == 1:
                print(f"tts> {'on' if tts_enabled else 'off'}")
                continue
            cmd = parts[1].lower()
            if cmd in {"on", "1", "true"}:
                tts_enabled = True
            elif cmd in {"off", "0", "false"}:
                tts_enabled = False
            elif cmd in {"toggle", "t"}:
                tts_enabled = not tts_enabled
            else:
                print("tts> 用法：/tts on|off|toggle")
                continue
            print(f"tts> {'on' if tts_enabled else 'off'}")
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
        if tts_enabled:
            wav_path = tts_out_dir / f"turn_{turn:04d}.wav"
            try:
                if tts_engine is None:
                    raise RuntimeError("tts engine not initialized")
                prompt_text = str(args.tts_prompt_text or "").strip()
                if not prompt_text:
                    transcript = str(args.tts_prompt_transcript or "").strip()
                    prompt_text = COSYVOICE3_DEFAULT_PROMPT_PREFIX + transcript if transcript else COSYVOICE3_DEFAULT_PROMPT_PREFIX

                tts_engine.synthesize_to_wav(
                    text=assistant,
                    out_wav_path=wav_path,
                    mode=str(args.tts_mode),
                    prompt_wav_path=args.tts_prompt_wav,
                    prompt_text=prompt_text,
                    instruct_text=str(args.tts_instruct_text or "").strip() or COSYVOICE3_DEFAULT_PROMPT_PREFIX,
                    n_timesteps=int(args.tts_n_timesteps),
                )
                print(f"tts> {wav_path}")
            except Exception as e:
                print(f"tts> 失败：{e}")
        turn += 1

    memory.shutdown()
    pipeline.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
