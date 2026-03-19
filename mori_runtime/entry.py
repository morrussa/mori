from __future__ import annotations

import argparse
import queue
import random
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from mori_llm.llama_cpp_cli import pick_default_chat_model, pick_default_embed_model, resolve_llama_cpp_bin_dir
from mori_llm.pipeline import MoriPipeline
from mori_tts.cosyvoice3_tts import (
    COSYVOICE3_DEFAULT_MODEL,
    COSYVOICE3_DEFAULT_PROMPT_PREFIX,
    CosyVoice3TTS,
)


class _LuaBridgeMap(dict[str, Any]):
    def __getitem__(self, key: object) -> Any:
        return dict.get(self, key)


def _bridge_record(value: dict[str, Any] | _LuaBridgeMap) -> _LuaBridgeMap:
    if isinstance(value, _LuaBridgeMap):
        return value
    return _LuaBridgeMap(value)


def _default_system_prompt() -> str:
    return "\n".join(
        [
            "你是 Mori，一个本地运行的助手。",
            "优先简洁直接地回答；如果需要更多信息再提问。",
            "如果系统提示里包含【相关对话片段】或其它记忆上下文，请合理利用。",
        ]
    )


def _parse_bilibili_room_id(value: str) -> int:
    s = str(value or "").strip()
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    import re

    m = re.search(r"live\\.bilibili\\.com/(\\d+)", s)
    if m:
        return int(m.group(1))
    return 0


class PyInbox:
    def __init__(self) -> None:
        self._q: queue.Queue[_LuaBridgeMap] = queue.Queue(maxsize=2048)
        self._closed = False

    def put(self, ev: dict[str, Any]) -> None:
        if self._closed:
            return
        bridged = _bridge_record(ev)
        try:
            self._q.put(bridged, timeout=0.1)
        except queue.Full:
            try:
                _ = self._q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._q.put(bridged, timeout=0.1)
            except queue.Full:
                pass

    def get(self) -> _LuaBridgeMap:
        return self._q.get()

    def drain_nowait(self, max_items: int = 32) -> list[_LuaBridgeMap]:
        out: list[_LuaBridgeMap] = []
        for _ in range(max(0, int(max_items))):
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                break
        return out

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        bridged = _bridge_record({"source": "system", "text": "/exit", "priority": 10_000, "enqueued_at": time.time()})
        try:
            self._q.put(bridged, timeout=0.1)
        except queue.Full:
            try:
                _ = self._q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._q.put(bridged, timeout=0.1)
            except queue.Full:
                pass


class PyLLM:
    def __init__(self, pipeline: MoriPipeline) -> None:
        self._pipeline = pipeline

    def stream_chat(
        self,
        messages: object,
        params: object,
        on_delta: Callable[[str], Any] | None,
        should_abort: Callable[[], bool] | None = None,
    ) -> None:
        if on_delta is None:
            raise ValueError("on_delta is required")

        gen = self._pipeline.generate_chat_stream_py(messages=messages, params=params)
        try:
            for delta in gen:
                if should_abort is not None:
                    try:
                        if bool(should_abort()):
                            break
                    except Exception:
                        pass
                try:
                    on_delta(str(delta))
                except Exception:
                    # If Lua callback fails, stop streaming to avoid hanging.
                    break
                if should_abort is not None:
                    try:
                        if bool(should_abort()):
                            break
                    except Exception:
                        pass
        finally:
            try:
                gen.close()  # type: ignore[attr-defined]
            except Exception:
                pass

    def shutdown(self) -> None:
        self._pipeline.shutdown()


@dataclass(frozen=True)
class _TtsJob:
    job_id: str
    intent_id: str
    turn: int
    source: str
    nickname: str
    segment_idx: int
    text: str
    wav_path: str
    created_at: float


class PyTTS:
    def __init__(
        self,
        *,
        engine: CosyVoice3TTS,
        mode: str,
        prompt_wav_path: str,
        prompt_text: str,
        instruct_text: str,
        n_timesteps: int,
        max_workers: int = 1,
    ) -> None:
        from concurrent.futures import ThreadPoolExecutor

        self._engine = engine
        self._default_mode = str(mode or "zero_shot")
        self._default_prompt_wav_path = str(prompt_wav_path or "")
        self._default_prompt_text = str(prompt_text or "")
        self._default_instruct_text = str(instruct_text or "")
        self._default_n_timesteps = int(n_timesteps)

        self._executor = ThreadPoolExecutor(max_workers=max(1, int(max_workers)))
        self._lock = threading.Lock()
        self._jobs: dict[str, tuple[_TtsJob, Any]] = {}

    @staticmethod
    def _get(payload: object, key: str, default: Any = None) -> Any:
        try:
            return payload[key]  # type: ignore[index]
        except Exception:
            pass
        try:
            return payload.get(key, default)  # type: ignore[union-attr]
        except Exception:
            return default

    def submit(self, payload: object) -> str:
        intent_id = str(self._get(payload, "intent_id", "") or "")
        if not intent_id:
            raise ValueError("tts.submit missing intent_id")
        turn = int(self._get(payload, "turn", 0) or 0)
        source = str(self._get(payload, "source", "") or "")
        nickname = str(self._get(payload, "nickname", "") or "")
        segment_idx = int(self._get(payload, "segment_idx", 0) or 0)
        text = str(self._get(payload, "text", "") or "")
        out_wav_path = str(self._get(payload, "out_wav_path", "") or "")
        if not out_wav_path:
            raise ValueError("tts.submit missing out_wav_path")

        mode = str(self._get(payload, "mode", self._default_mode) or self._default_mode)
        prompt_wav_path = str(self._get(payload, "prompt_wav_path", self._default_prompt_wav_path) or "")
        prompt_text = str(self._get(payload, "prompt_text", self._default_prompt_text) or "")
        instruct_text = str(self._get(payload, "instruct_text", self._default_instruct_text) or "")
        n_timesteps = int(self._get(payload, "n_timesteps", self._default_n_timesteps) or self._default_n_timesteps)

        job_id = f"{intent_id}:{segment_idx}:{time.time():.6f}:{random.randint(1000,9999)}"
        created_at = time.time()
        job = _TtsJob(
            job_id=job_id,
            intent_id=intent_id,
            turn=turn,
            source=source,
            nickname=nickname,
            segment_idx=segment_idx,
            text=text,
            wav_path=str(Path(out_wav_path).expanduser().resolve()),
            created_at=created_at,
        )

        def _run() -> str:
            self._engine.synthesize_to_wav(
                text=text,
                out_wav_path=job.wav_path,
                mode=mode,
                prompt_wav_path=prompt_wav_path,
                prompt_text=prompt_text,
                instruct_text=instruct_text,
                n_timesteps=n_timesteps,
            )
            return job.wav_path

        fut = self._executor.submit(_run)
        with self._lock:
            self._jobs[job_id] = (job, fut)
        return job_id

    def drain(self, _payload: object | None = None) -> list[_LuaBridgeMap]:
        done: list[_LuaBridgeMap] = []
        with self._lock:
            items = list(self._jobs.items())
        for job_id, (job, fut) in items:
            if not fut.done():
                continue
            ok = True
            err = ""
            wav_path = job.wav_path
            try:
                wav_path = str(fut.result())
            except Exception as e:
                ok = False
                err = str(e)
            with self._lock:
                self._jobs.pop(job_id, None)
            done.append(
                _bridge_record(
                    {
                        "job_id": job_id,
                        "intent_id": job.intent_id,
                        "turn": job.turn,
                        "source": job.source,
                        "nickname": job.nickname,
                        "segment_idx": job.segment_idx,
                        "text": job.text,
                        "wav_path": wav_path if ok else "",
                        "ok": ok,
                        "error": err,
                        "created_at": job.created_at,
                        "finished_at": time.time(),
                    }
                )
            )
        return done

    def cancel_intent(self, intent_id: str) -> int:
        intent_id = str(intent_id or "")
        if not intent_id:
            return 0
        canceled = 0
        with self._lock:
            items = list(self._jobs.items())
        for job_id, (job, fut) in items:
            if job.intent_id != intent_id:
                continue
            if fut.cancel():
                canceled += 1
                with self._lock:
                    self._jobs.pop(job_id, None)
        return canceled

    def shutdown(self) -> None:
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)  # type: ignore[call-arg]
        except TypeError:
            self._executor.shutdown(wait=False)


def _setup_lua_runtime(*, repo_root: Path):
    try:
        from lupa.luajit21 import LuaRuntime  # type: ignore[import-not-found]
    except ModuleNotFoundError as e:  # pragma: no cover
        raise ModuleNotFoundError("Missing dependency: lupa. Activate venv, then `pip install -r requirements.txt`.") from e

    lua = LuaRuntime(unpack_returned_tuples=True)
    append_path = lua.eval("function(p) package.path = package.path .. ';' .. p end")

    # mori_runtime lua modules
    runtime_lua_root = (repo_root / "mori_runtime" / "lua").resolve()
    append_path(str(runtime_lua_root / "?.lua"))
    append_path(str(runtime_lua_root / "?/init.lua"))

    # mori_memory lua modules
    mem_root = (repo_root / "mori_memory").resolve()
    append_path(str(mem_root / "?.lua"))
    append_path(str(mem_root / "?/init.lua"))

    # mori_live_stream lua modules
    live_root = (repo_root / "mori_live_stream" / "lua").resolve()
    append_path(str(live_root / "?.lua"))
    append_path(str(live_root / "?/init.lua"))

    return lua


def _to_lua_value(lua, value: Any):
    if isinstance(value, dict):
        out: dict[object, object] = {}
        for k, v in value.items():
            out[k] = _to_lua_value(lua, v)
        return lua.table_from(out)
    if isinstance(value, (list, tuple)):
        return lua.table_from([_to_lua_value(lua, item) for item in value])
    return value


def _start_stdin_thread(inbox: PyInbox, *, priority: int) -> threading.Thread:
    def _loop() -> None:
        while True:
            line = sys.stdin.readline()
            if line == "":
                inbox.close()
                return
            text = line.strip()
            if not text:
                continue
            inbox.put(
                {
                    "source": "stdin",
                    "text": text,
                    "nickname": "",
                    "priority": int(priority),
                    "enqueued_at": time.time(),
                }
            )
            if text in {"/exit", "/quit"}:
                return

    t = threading.Thread(target=_loop, name="stdin-reader", daemon=True)
    t.start()
    return t


def _start_bilibili_thread(
    inbox: PyInbox,
    *,
    room_id: int,
    interval_s: float,
    catchup_n: int,
    priority: int,
    exit_when_offline: bool,
    live_check_interval_s: float,
) -> threading.Thread:
    from mori_live_stream.bilibili_live import BilibiliLivePoller
    from mori_live_stream.bilibili_room import get_room_info

    def _extract_user_id(raw: dict[str, Any] | None) -> str:
        if not isinstance(raw, dict):
            return ""
        for key in ("uid", "user_id", "uid_str", "userid", "userId", "userID"):
            try:
                value = raw.get(key)
            except Exception:
                value = None
            if value is None:
                continue
            s = str(value).strip()
            if s:
                return s
        return ""

    def _loop() -> None:
        poller = BilibiliLivePoller(room_id=int(room_id))
        # optional catchup: fetch current and enqueue last N
        cn = int(catchup_n or 0)
        if cn < 0:
            cn = 0
        if cn > 10:
            cn = 10
        if cn > 0:
            try:
                current = poller.fetch()
                ordered = sorted(
                    current,
                    key=lambda m: (float(m.ts or 0.0), str(m.timeline), str(m.nickname), str(m.text)),
                )
                for msg in ordered[-cn:]:
                    user_id = _extract_user_id(getattr(msg, "raw", None))
                    inbox.put(
                        {
                            "source": "bilibili",
                            "text": msg.text,
                            "nickname": msg.nickname,
                            "user_id": user_id,
                            "room_id": int(room_id),
                            "timeline": msg.timeline,
                            "priority": int(priority),
                            "enqueued_at": time.time(),
                        }
                    )
            except Exception:
                pass
        last_live_check = 0.0
        while True:
            if exit_when_offline and (time.time() - last_live_check) >= float(live_check_interval_s or 10.0):
                last_live_check = time.time()
                try:
                    info = get_room_info(room_id=int(room_id))
                    if int(info.live_status) != 1:
                        inbox.put(
                            {
                                "source": "system",
                                "text": "/exit",
                                "nickname": "",
                                "priority": 10_000,
                                "enqueued_at": time.time(),
                            }
                        )
                        return
                except Exception:
                    pass

            try:
                for msg in poller.poll_new():
                    user_id = _extract_user_id(getattr(msg, "raw", None))
                    inbox.put(
                        {
                            "source": "bilibili",
                            "text": msg.text,
                            "nickname": msg.nickname,
                            "user_id": user_id,
                            "room_id": int(room_id),
                            "timeline": msg.timeline,
                            "priority": int(priority),
                            "enqueued_at": time.time(),
                        }
                    )
            except Exception:
                pass
            time.sleep(float(interval_s))

    t = threading.Thread(target=_loop, name="bilibili-reader", daemon=True)
    t.start()
    return t


def _build_common_parser(*, prog: str) -> argparse.ArgumentParser:
    repo_root = Path(__file__).resolve().parents[1]
    model_dir = repo_root / "model"
    tts_root = model_dir / "tts" / "cosyvoice3"

    parser = argparse.ArgumentParser(prog=prog, description="Mori (Lua runtime + llama.cpp + memory + optional TTS).")
    parser.add_argument("--workdir", default=str(repo_root), help="Working directory (stores memory/ and live/ by default).")
    parser.add_argument("--llama-bin-dir", default=None, help="Path to llama.cpp build/bin.")

    parser.add_argument("--chat-model", default=None, help="Path to chat .gguf model (default: pick from model/).")
    parser.add_argument("--embed-model", default=None, help="Path to embedding .gguf model (default: pick from model/).")

    parser.add_argument("--ctx-size", type=int, default=0, help="llama-server --ctx-size (0 = 8192).")
    parser.add_argument("--n-predict", type=int, default=512, help="max_tokens.")
    parser.add_argument("--temp", type=float, default=0.7, help="temperature.")
    parser.add_argument("--top-p", type=float, default=0.9, help="top_p.")
    parser.add_argument("--system", default=_default_system_prompt(), help="Base system prompt.")

    parser.add_argument("--tts", action="store_true", help="Enable TTS (cosyvoice3).")
    parser.add_argument("--tts-root", default=str(tts_root), help="CosyVoice3 model root dir.")
    parser.add_argument("--tts-model", default=COSYVOICE3_DEFAULT_MODEL, help="Model dir name under <tts-root>/ (or absolute dir).")
    parser.add_argument(
        "--tts-mode",
        choices=["zero_shot", "cross_lingual", "instruct"],
        default="zero_shot",
        help="Synthesis mode.",
    )
    parser.add_argument("--tts-device", choices=["auto", "cpu", "cuda", "metal"], default="auto", help="Device for inference.")
    parser.add_argument("--tts-f16", action="store_true", help="Use FP16 precision (GPU only).")
    parser.add_argument("--tts-prompt-wav", default="", help="Reference voice audio path (required when --tts is on).")
    parser.add_argument("--tts-prompt-text", default="", help="Full prompt_text for zero_shot (prefix+transcript).")
    parser.add_argument("--tts-prompt-transcript", default="", help="Transcript of prompt wav (appended to required prefix).")
    parser.add_argument("--tts-instruct-text", default="", help="Instruction text for instruct mode.")
    parser.add_argument("--tts-n-timesteps", type=int, default=10, help="Number of flow sampling steps.")

    return parser


def run_cli(argv: list[str] | None = None) -> int:
    parser = _build_common_parser(prog="mori")
    parser.add_argument("--audio-dir", default="tts_out", help="Directory for chunked wav outputs (relative to --workdir unless absolute).")
    parser.add_argument("--tts-out-dir", dest="audio_dir", help="Alias of --audio-dir (legacy).")
    parser.add_argument("--interrupt-policy", choices=["priority", "always", "never"], default="priority")
    args = parser.parse_args(argv)
    return _run(mode="cli", args=args)


def run_vtuber(argv: list[str] | None = None) -> int:
    parser = _build_common_parser(prog="mori-vtuber")
    parser.add_argument("--live-dir", default="live", help="Directory for live outputs (relative to --workdir unless absolute).")
    parser.add_argument("--subtitle-file", default="subtitle.txt", help="Subtitle file name under --live-dir.")
    parser.add_argument("--event-log", default="events.jsonl", help="JSONL event log under --live-dir.")

    parser.add_argument("--bilibili-room-id", type=int, default=0, help="Enable bilibili by room id (0 = disabled).")
    parser.add_argument("--bilibili-room-url", default="", help="Enable bilibili by room url.")
    parser.add_argument("--bilibili-interval", type=float, default=2.0, help="Polling interval seconds.")
    parser.add_argument("--bilibili-catchup", type=int, default=0, help="Process last N current messages on startup (0-10).")
    parser.add_argument("--bilibili-exit-when-offline", action="store_true", help="Exit when room goes offline.")
    parser.add_argument("--bilibili-live-check-interval", type=float, default=10.0, help="Live status check interval seconds.")
    parser.add_argument("--interrupt-policy", choices=["priority", "always", "never"], default="priority")
    args = parser.parse_args(argv)

    if args.bilibili_room_id <= 0 and str(args.bilibili_room_url or "").strip():
        parsed = _parse_bilibili_room_id(str(args.bilibili_room_url))
        if parsed <= 0:
            raise ValueError(f"Cannot parse bilibili room id from url: {args.bilibili_room_url!r}")
        args.bilibili_room_id = parsed

    return _run(mode="vtuber", args=args)


def _run(*, mode: str, args: argparse.Namespace) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    model_dir = repo_root / "model"
    if not model_dir.is_dir():
        raise FileNotFoundError(f"model/ directory not found: {model_dir}")

    workdir = Path(args.workdir).expanduser().resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    # match legacy behavior
    (workdir / "memory").mkdir(parents=True, exist_ok=True)
    (workdir / "memory" / "v4" / "topic_graph").mkdir(parents=True, exist_ok=True)
    # chdir for relative paths and for Lua persistence defaults
    import os

    os.chdir(workdir)

    chat_model = Path(args.chat_model).expanduser() if args.chat_model else pick_default_chat_model(model_dir)
    embed_model = Path(args.embed_model).expanduser() if args.embed_model else pick_default_embed_model(model_dir)

    llama_bin_dir = resolve_llama_cpp_bin_dir(args.llama_bin_dir)

    pipeline = MoriPipeline(
        llama_bin_dir=str(llama_bin_dir),
        large_ctx_size=int(args.ctx_size) if int(args.ctx_size) > 0 else 8192,
        embed_ctx_size=8192,
    )
    pipeline.load_models_py(
        large_model_path=str(chat_model),
        embedding_model_path=str(embed_model),
    )

    py_llm = PyLLM(pipeline)

    tts_enabled = bool(args.tts)
    py_tts: PyTTS | None = None
    computed_prompt_text = ""
    computed_instruct_text = ""
    if tts_enabled:
        prompt_wav = str(args.tts_prompt_wav or "").strip()
        if not prompt_wav:
            print("tts> 失败：缺少 --tts-prompt-wav（参考音频）", file=sys.stderr)
            tts_enabled = False
        else:
            engine = CosyVoice3TTS(
                tts_root=args.tts_root,
                model=args.tts_model,
                device=args.tts_device,
                use_f16=bool(args.tts_f16),
            )
            prompt_text = str(args.tts_prompt_text or "").strip()
            if not prompt_text:
                transcript = str(args.tts_prompt_transcript or "").strip()
                prompt_text = COSYVOICE3_DEFAULT_PROMPT_PREFIX + transcript if transcript else COSYVOICE3_DEFAULT_PROMPT_PREFIX
            instruct_text = str(args.tts_instruct_text or "").strip() or COSYVOICE3_DEFAULT_PROMPT_PREFIX
            computed_prompt_text = prompt_text
            computed_instruct_text = instruct_text

            py_tts = PyTTS(
                engine=engine,
                mode=str(args.tts_mode),
                prompt_wav_path=prompt_wav,
                prompt_text=prompt_text,
                instruct_text=instruct_text,
                n_timesteps=int(args.tts_n_timesteps),
                max_workers=1,
            )

    # outputs
    subtitle_path = ""
    event_log_path = ""
    audio_dir = ""

    if mode == "vtuber":
        live_dir = Path(args.live_dir).expanduser()
        if not live_dir.is_absolute():
            live_dir = workdir / live_dir
        live_dir = live_dir.resolve()
        live_dir.mkdir(parents=True, exist_ok=True)
        subtitle_path = str((live_dir / str(args.subtitle_file)).resolve())
        event_log_path = str((live_dir / str(args.event_log)).resolve())
        audio_dir = str((live_dir / "audio").resolve())
        Path(audio_dir).mkdir(parents=True, exist_ok=True)
    else:
        out_dir = Path(getattr(args, "audio_dir", "tts_out")).expanduser()
        if not out_dir.is_absolute():
            out_dir = workdir / out_dir
        out_dir = out_dir.resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        audio_dir = str(out_dir)
        # keep event log alongside for debugging
        event_log_path = str((out_dir / "events.jsonl").resolve())

    inbox = PyInbox()

    stdin_priority = 100
    _start_stdin_thread(inbox, priority=stdin_priority)

    if mode == "vtuber" and int(getattr(args, "bilibili_room_id", 0) or 0) > 0:
        _start_bilibili_thread(
            inbox,
            room_id=int(args.bilibili_room_id),
            interval_s=float(args.bilibili_interval),
            catchup_n=int(getattr(args, "bilibili_catchup", 0) or 0),
            priority=10,
            exit_when_offline=bool(args.bilibili_exit_when_offline),
            live_check_interval_s=float(getattr(args, "bilibili_live_check_interval", 10.0) or 10.0),
        )

    lua = _setup_lua_runtime(repo_root=repo_root)

    # For mori_memory: let Lua call embeddings via this pipeline (existing convention).
    lua.globals().py_pipeline = pipeline
    lua.globals().py_llm = py_llm
    lua.globals().py_tts = py_tts
    lua.globals().py_inbox = inbox
    lua.globals().py_now = time.time

    config = {
        "system_prompt": str(args.system or "").strip(),
        "llm_params": {
            "max_tokens": int(args.n_predict),
            "temperature": float(args.temp),
            "top_p": float(args.top_p),
        },
        "interrupt_policy": str(getattr(args, "interrupt_policy", "priority")),
        "tts_enabled": bool(tts_enabled and py_tts is not None),
        "tts_mode": str(args.tts_mode),
        "tts_prompt_wav_path": str(args.tts_prompt_wav or "").strip(),
        "tts_prompt_text": computed_prompt_text,
        "tts_instruct_text": computed_instruct_text,
        "tts_n_timesteps": int(args.tts_n_timesteps),
        "subtitle_path": subtitle_path,
        "event_log_path": event_log_path,
        "audio_dir": audio_dir,
        "print_to_stdout": mode == "cli",
        "bilibili_enabled": bool(mode == "vtuber" and int(getattr(args, "bilibili_room_id", 0) or 0) > 0),
    }
    if not config["system_prompt"]:
        config["system_prompt"] = _default_system_prompt()

    config_lua = _to_lua_value(lua, config)

    ctx = lua.table_from(
        {
            "py_llm": py_llm,
            "py_tts": py_tts,
            "py_inbox": inbox,
            "py_now": time.time,
            "config": config_lua,
        }
    )

    app = lua.eval("require")("mori.app.runtime")
    return int(app.run(config_lua, ctx) or 0)
