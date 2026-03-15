from __future__ import annotations

import argparse
import json
import os
import random
import re
import time
from pathlib import Path

from mori_llm.llama_cpp_cli import (
    iter_lua_sequence,
    pick_default_chat_model,
    pick_default_embed_model,
    resolve_llama_cpp_bin_dir,
)
from mori_llm.pipeline import MoriPipeline
from mori_memory.bridge import MoriMemoryBridge
from mori_tts.qwen3_tts import QWEN3_TTS_DEFAULT_MODEL, is_cuda_runtime, synthesize as qwen3_tts_synthesize


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
    if text.endswith("[end of text]"):
        text = text[: -len("[end of text]")]
    return text.strip()


def _default_system_prompt() -> str:
    return "\n".join(
        [
            "你是 Mori，一个本地运行的助手。",
            "你正在进行最基础的 AI VTuber 演示：输出字幕文件 + 可选 TTS 音频。",
            "优先简洁直接地回答；如果需要更多信息再提问。",
            "如果系统提示里包含【相关对话片段】或其它记忆上下文，请合理利用。",
        ]
    )


def _enhance_system_prompt_for_bilibili(system_prompt: str) -> str:
    system_prompt = str(system_prompt or "").strip()
    if not system_prompt:
        return system_prompt
    if "你可能会收到直播弹幕" in system_prompt:
        return system_prompt
    return (
        system_prompt
        + "\n\n"
        + "你可能会收到直播弹幕消息，这些消息会被标记为[接收到了直播间的弹幕]，"
        + "表示这是来自直播间观众的消息，而不是主人直接对你说的话。"
        + "当你看到[接收到了直播间的弹幕]标记时，你应该知道这是其他人发送的，"
        + "但你仍然可以回应，就像在直播间与观众互动一样。"
    )


def _parse_bilibili_room_id(value: str) -> int:
    s = str(value or "").strip()
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    m = re.search(r"live\\.bilibili\\.com/(\\d+)", s)
    if m:
        return int(m.group(1))
    return 0


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parent
    model_dir = repo_root / "model"
    tts_root = model_dir / "tts" / "qwen3_tts_rs"

    parser = argparse.ArgumentParser(prog="mori-vtuber", description="Basic Mori VTuber (memory + llama.cpp + TTS + live outputs).")
    parser.add_argument("--workdir", default=str(repo_root), help="Working directory (stores memory/ + live/).")
    parser.add_argument("--llama-bin-dir", default=None, help="Path to llama.cpp build/bin.")

    parser.add_argument("--chat-model", default=None, help="Path to chat .gguf model (default: pick from model/).")
    parser.add_argument("--embed-model", default=None, help="Path to embedding .gguf model (default: pick from model/).")

    parser.add_argument("--ctx-size", type=int, default=0, help="llama-server --ctx-size (0 = 8192).")
    parser.add_argument("--n-predict", type=int, default=512, help="llama-cli --n-predict.")
    parser.add_argument("--temp", type=float, default=0.7, help="llama-cli --temp.")
    parser.add_argument("--top-p", type=float, default=0.9, help="llama-cli --top-p.")
    parser.add_argument("--system", default=_default_system_prompt(), help="Base system prompt.")

    parser.add_argument("--tts", action="store_true", help="Enable TTS (qwen3_tts_rs).")
    parser.add_argument(
        "--tts-cuda",
        action="store_true",
        help="Require CUDA runtime build for TTS (fail if CPU build).",
    )
    parser.add_argument("--tts-root", default=str(tts_root), help="qwen3_tts_rs install dir (contains tts + models/).")
    parser.add_argument("--tts-model", default=QWEN3_TTS_DEFAULT_MODEL, help="Model dir name under <tts-root>/models/.")
    parser.add_argument("--tts-speaker", default="Vivian", help="Speaker name (CustomVoice model).")
    parser.add_argument("--tts-language", default="chinese", help="Language name (e.g. chinese/english).")
    parser.add_argument("--tts-instruction", default="", help="Optional voice instruction (1.7B CustomVoice).")

    parser.add_argument("--live-dir", default="live", help="Directory for live outputs (relative to --workdir unless absolute).")
    parser.add_argument("--subtitle-file", default="subtitle.txt", help="Subtitle file name under --live-dir.")
    parser.add_argument("--event-log", default="events.jsonl", help="JSONL event log under --live-dir.")

    parser.add_argument("--bilibili-room-id", type=int, default=0, help="Enable bilibili live mode by room id (0 = disabled).")
    parser.add_argument("--bilibili-room-url", default="", help="Optional bilibili live URL to extract room id.")
    parser.add_argument("--bilibili-catchup", type=int, default=0, help="Process last N recent danmaku on startup (0 = wait for new).")
    parser.add_argument("--bilibili-interval", type=float, default=2.0, help="Polling interval seconds for bilibili mode.")
    parser.add_argument("--bilibili-exit-when-offline", action="store_true", help="Exit when the live room goes offline.")
    parser.add_argument("--bilibili-live-check-interval", type=float, default=15.0, help="Live status check interval seconds.")

    args = parser.parse_args()

    if not model_dir.is_dir():
        raise FileNotFoundError(f"model/ directory not found: {model_dir}")

    args.chat_model = str(Path(args.chat_model).expanduser()) if args.chat_model else str(pick_default_chat_model(model_dir))
    args.embed_model = str(Path(args.embed_model).expanduser()) if args.embed_model else str(pick_default_embed_model(model_dir))

    if int(args.bilibili_room_id or 0) <= 0 and str(args.bilibili_room_url or "").strip():
        args.bilibili_room_id = _parse_bilibili_room_id(str(args.bilibili_room_url))

    args.llama_bin_dir = str(resolve_llama_cpp_bin_dir(args.llama_bin_dir))
    return args


def main() -> int:
    args = parse_args()

    workdir = Path(args.workdir).expanduser().resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    os.chdir(workdir)
    (workdir / "memory").mkdir(parents=True, exist_ok=True)
    (workdir / "memory" / "v4" / "topic_graph").mkdir(parents=True, exist_ok=True)

    live_dir = Path(args.live_dir).expanduser()
    if not live_dir.is_absolute():
        live_dir = workdir / live_dir
    live_dir = live_dir.resolve()
    live_dir.mkdir(parents=True, exist_ok=True)

    subtitle_path = (live_dir / str(args.subtitle_file)).resolve()
    event_log_path = (live_dir / str(args.event_log)).resolve()

    audio_dir = (live_dir / "audio").resolve()
    audio_dir.mkdir(parents=True, exist_ok=True)

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

    subtitle_path.write_text("", encoding="utf-8")

    print("Mori VTuber 已启动。输入 /exit 退出。")
    print("命令：/tts on|off|toggle  切换语音输出。")
    print(f"live> subtitle: {subtitle_path}")
    print(f"live> events:   {event_log_path}")

    system_prompt = str(args.system).strip()
    bilibili_enabled = int(args.bilibili_room_id or 0) > 0
    if bilibili_enabled:
        system_prompt = _enhance_system_prompt_for_bilibili(system_prompt)

    tts_enabled = bool(args.tts)
    tts_require_cuda = bool(args.tts_cuda)
    if tts_enabled:
        try:
            runtime = "cuda" if is_cuda_runtime(args.tts_root) else "cpu"
            print(f"tts> runtime={runtime} root={Path(args.tts_root).expanduser().resolve()}")
        except Exception as e:
            print(f"tts> runtime check failed: {e}")
    turn = 1

    def _run_one_turn(*, user_input: str, source: str = "stdin", nickname: str = "") -> None:
        nonlocal turn, tts_enabled

        blocks = memory.compile_context({"turn": turn, "user_input": user_input})
        messages = [{"role": "system", "content": system_prompt}]
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

        subtitle_path.write_text(assistant, encoding="utf-8")

        wav_path: Path | None = None
        if tts_enabled:
            wav_path = audio_dir / f"turn_{turn:04d}.wav"
            try:
                qwen3_tts_synthesize(
                    text=assistant,
                    out_wav_path=wav_path,
                    tts_root=args.tts_root,
                    model=args.tts_model,
                    speaker=args.tts_speaker,
                    language=args.tts_language,
                    instruction=str(args.tts_instruction or "").strip() or None,
                    require_cuda=tts_require_cuda,
                )
                print(f"tts> {wav_path}")
            except Exception as e:
                print(f"tts> 失败：{e}")
                wav_path = None

        event = {
            "ts": time.time(),
            "turn": int(turn),
            "source": str(source or "stdin"),
            "nickname": str(nickname or ""),
            "user_input": user_input,
            "assistant_text": assistant,
            "subtitle_path": str(subtitle_path),
            "wav_path": str(wav_path) if wav_path else "",
        }
        with event_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

        turn += 1

    if bilibili_enabled:
        import queue
        import threading

        from mori_live_stream.bilibili_live import BilibiliLivePoller, DanmakuMessage
        from mori_live_stream.bilibili_room import get_room_info

        room_id = int(args.bilibili_room_id)
        interval_s = float(args.bilibili_interval)
        poller = BilibiliLivePoller(room_id=room_id)

        catchup_n = int(args.bilibili_catchup or 0)
        if catchup_n < 0:
            catchup_n = 0
        if catchup_n > 10:
            catchup_n = 10

        try:
            info = get_room_info(room_id=room_id)
            print(f"bili> title={info.title} online={info.online} live_status={info.live_status}")
        except Exception as e:
            print(f"bili> room info error: {e}")

        try:
            current = poller.fetch()
            print(f"bili> current_messages={len(current)} catchup={catchup_n}")
            if catchup_n > 0 and current:
                ordered = sorted(current, key=lambda m: (float(m.ts or 0.0), str(m.timeline), str(m.nickname), str(m.text)))
                for msg in ordered[-catchup_n:]:
                    user_input = f"[接收到了直播间的弹幕] {msg.nickname}给你发送了一个消息: {msg.text}"
                    print(f"你(bili-catchup)> {msg.nickname}: {msg.text}")
                    _run_one_turn(user_input=user_input, source="bilibili", nickname=msg.nickname)
        except Exception as e:
            print(f"bili> fetch error: {e}")

        q: queue.Queue[DanmakuMessage] = queue.Queue(maxsize=512)
        stop = threading.Event()
        exit_requested = threading.Event()

        def _poll_loop() -> None:
            while not stop.is_set():
                try:
                    for msg in poller.poll_new():
                        try:
                            q.put(msg, timeout=0.1)
                        except queue.Full:
                            try:
                                q.get_nowait()
                            except queue.Empty:
                                pass
                            try:
                                q.put(msg, timeout=0.1)
                            except queue.Full:
                                pass
                except Exception as e:
                    print(f"bili> poll error: {e}")
                stop.wait(interval_s)

        t = threading.Thread(target=_poll_loop, name="bilibili-poll", daemon=True)
        t.start()

        def _live_monitor() -> None:
            check_interval_s = float(args.bilibili_live_check_interval)
            while not stop.is_set() and not exit_requested.is_set():
                try:
                    info = get_room_info(room_id=room_id)
                    if int(info.live_status) != 1:
                        print(f"bili> 房间已下播（live_status={info.live_status}），准备退出。")
                        exit_requested.set()
                        stop.set()
                        return
                except Exception as e:
                    print(f"bili> live check error: {e}")
                stop.wait(check_interval_s)

        monitor_thread: threading.Thread | None = None
        if bool(args.bilibili_exit_when_offline):
            monitor_thread = threading.Thread(target=_live_monitor, name="bilibili-live-monitor", daemon=True)
            monitor_thread.start()

        print(
            f"bili> room_id={room_id} interval={interval_s}s exit_when_offline={bool(args.bilibili_exit_when_offline)} (Ctrl+C 退出)"
        )
        try:
            last_notice = 0.0
            while True:
                if exit_requested.is_set():
                    break
                try:
                    msg = q.get(timeout=0.2)
                except queue.Empty:
                    if time.time() - last_notice >= 30.0:
                        print("bili> waiting for new danmaku...")
                        last_notice = time.time()
                    continue
                user_input = f"[接收到了直播间的弹幕] {msg.nickname}给你发送了一个消息: {msg.text}"
                print(f"你(bili)> {msg.nickname}: {msg.text}")
                _run_one_turn(user_input=user_input, source="bilibili", nickname=msg.nickname)
        except KeyboardInterrupt:
            print()
            stop.set()
            t.join(timeout=2)
            if monitor_thread:
                monitor_thread.join(timeout=2)
    else:
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

            _run_one_turn(user_input=user_input, source="stdin")

    subtitle_path.write_text("", encoding="utf-8")
    memory.shutdown()
    pipeline.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
