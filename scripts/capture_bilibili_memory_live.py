#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import sys
import time
from collections import Counter, deque
from pathlib import Path
from typing import Any, Iterable


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


REPO_ROOT = _repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mori_live_stream.bilibili_live import BilibiliLivePoller, DanmakuMessage  # noqa: E402
from mori_live_stream.bilibili_room import RoomInfo, get_room_info  # noqa: E402
from mori_llm.llama_cpp_cli import iter_lua_sequence, pick_default_embed_model, resolve_llama_cpp_bin_dir  # noqa: E402
from mori_llm.pipeline import LlamaCppServerClient  # noqa: E402
from mori_memory.bridge import MoriMemoryBridge  # noqa: E402


TURN_RE = re.compile(r"第(\d+)轮")
FACT_RE = re.compile(r"fact:(F\d{5,})")


def parse_args() -> argparse.Namespace:
    model_dir = (REPO_ROOT / "model").resolve()
    capture_root = (REPO_ROOT / "live_capture").resolve()

    p = argparse.ArgumentParser(
        prog="capture-bilibili-memory-live",
        description="Poll a real Bilibili live room and drive mori_memory directly, logging raw danmaku and memory traces.",
    )
    p.add_argument("--room-id", type=int, default=0, help="Bilibili live room id.")
    p.add_argument("--room-url", default="", help="Optional room URL like https://live.bilibili.com/<id>.")
    p.add_argument("--capture-root", default=str(capture_root), help="Parent directory for generated run directories.")
    p.add_argument("--run-dir", default="", help="Optional exact output directory. If set, --capture-root is ignored.")
    p.add_argument("--interval", type=float, default=2.0, help="Polling interval seconds.")
    p.add_argument("--catchup", type=int, default=5, help="Process last N current messages once at startup (0-20).")
    p.add_argument("--exit-when-offline", action="store_true", help="Exit when the room is no longer live.")
    p.add_argument("--live-check-interval", type=float, default=15.0, help="Room live-status check interval seconds.")
    p.add_argument("--stop-after", type=float, default=0.0, help="Stop after N seconds (0 = no limit).")
    p.add_argument("--max-messages", type=int, default=0, help="Stop after N processed messages (0 = no limit).")
    p.add_argument("--status-every", type=int, default=25, help="Print one progress line every N processed messages.")

    p.add_argument("--llama-bin-dir", default=None, help="Path to llama.cpp build/bin.")
    p.add_argument("--embed-model", default=None, help="Path to embedding .gguf model (default: pick from model/).")
    p.add_argument("--embed-ctx-size", type=int, default=8192, help="llama-server --ctx-size for embeddings.")
    p.add_argument(
        "--embed-gpu-layers",
        default="all",
        help='llama-server --gpu-layers for embeddings ("all", "0", or integer string).',
    )
    p.add_argument("--embed-startup-timeout", type=int, default=600, help="Embedding server startup timeout seconds.")

    p.add_argument(
        "--memory-profile",
        choices=["capture", "default"],
        default="capture",
        help='Memory config profile. "capture" keeps current logic but removes bilibili guard blocking.',
    )
    p.add_argument("--max-streams", type=int, default=0, help="Override disentangle.max_streams (0 = keep config default).")
    p.add_argument(
        "--assign-threshold",
        type=float,
        default=-1.0,
        help="Override disentangle.assign_threshold (<0 = keep config default).",
    )
    p.add_argument(
        "--pending-threshold",
        type=float,
        default=-1.0,
        help="Override disentangle.pending_threshold (<0 = keep config default).",
    )
    p.add_argument(
        "--pending-margin",
        type=float,
        default=-1.0,
        help="Override disentangle.pending_margin (<0 = keep config default).",
    )
    p.add_argument(
        "--commit-idle-turns",
        type=int,
        default=0,
        help="Override disentangle.commit_idle_turns (0 = keep config default).",
    )
    p.add_argument(
        "--commit-chunk-turns",
        type=int,
        default=0,
        help="Override disentangle.commit_chunk_turns (0 = keep config default).",
    )
    p.add_argument(
        "--pending-context-turns",
        type=int,
        default=0,
        help="Override disentangle.pending_context_turns (0 = keep config default).",
    )

    args = p.parse_args()
    if int(args.room_id or 0) <= 0 and str(args.room_url or "").strip():
        args.room_id = parse_room_id(str(args.room_url))
    if int(args.room_id or 0) <= 0:
        raise SystemExit("Missing --room-id (or pass --room-url like https://live.bilibili.com/<id>)")

    if args.embed_model:
        args.embed_model = str(Path(args.embed_model).expanduser().resolve())
    else:
        args.embed_model = str(pick_default_embed_model(model_dir))

    args.llama_bin_dir = str(resolve_llama_cpp_bin_dir(args.llama_bin_dir))
    args.catchup = max(0, min(20, int(args.catchup or 0)))
    args.interval = max(0.2, float(args.interval or 2.0))
    args.live_check_interval = max(2.0, float(args.live_check_interval or 15.0))
    args.status_every = max(1, int(args.status_every or 25))
    args.embed_ctx_size = max(512, int(args.embed_ctx_size or 8192))
    args.embed_startup_timeout = max(10, int(args.embed_startup_timeout or 600))
    return args


def parse_room_id(value: str) -> int:
    s = str(value or "").strip()
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    m = re.search(r"live\.bilibili\.com/(\d+)", s)
    return int(m.group(1)) if m else 0


def now_ts() -> float:
    return time.time()


def now_iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts or now_ts()))


def json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    return str(value)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    line = json.dumps(payload, ensure_ascii=False, default=json_default)
    with path.open("a", encoding="utf-8") as f:
        f.write(line)
        f.write("\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=json_default)
        f.write("\n")


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def stats_percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    ordered = sorted(float(v) for v in values)
    pos = clamp(percentile, 0.0, 100.0) / 100.0 * (len(ordered) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(ordered[lo])
    alpha = pos - lo
    return float(ordered[lo] * (1.0 - alpha) + ordered[hi] * alpha)


def summarize_ms(values: list[float]) -> dict[str, float]:
    if not values:
        return {"count": 0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
    return {
        "count": len(values),
        "p50_ms": round(stats_percentile(values, 50.0), 3),
        "p95_ms": round(stats_percentile(values, 95.0), 3),
        "max_ms": round(max(values), 3),
    }


def extract_user_id(raw: dict[str, Any] | None) -> str:
    if not isinstance(raw, dict):
        return ""
    for key in ("uid", "user_id", "uid_str", "userid", "userId", "userID"):
        value = raw.get(key)
        if value is None:
            continue
        s = str(value).strip()
        if s:
            return s
    return ""


def _iter_lua_sequence(value: object) -> Iterable[object]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return value
    try:
        length = len(value)  # type: ignore[arg-type]
    except Exception:
        length = 0
    if isinstance(length, int) and length > 0:
        seq: list[object] = []
        for i in range(1, length + 1):
            try:
                seq.append(value[i])  # type: ignore[index]
            except Exception:
                break
        if seq:
            return seq
    try:
        return list(value)  # type: ignore[arg-type]
    except Exception:
        return ()


def lua_to_py(value: object, *, depth: int = 0) -> object:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if depth >= 5:
        return str(value)

    try:
        items = list(value.items())  # type: ignore[union-attr]
    except Exception:
        items = None

    if items is not None:
        out: dict[object, object] = {}
        for k, v in items:
            key: object
            if k is None or isinstance(k, (str, int, float, bool)):
                key = k
            else:
                key = str(k)
            out[key] = lua_to_py(v, depth=depth + 1)
        return out

    seq = list(_iter_lua_sequence(value))
    if seq:
        return [lua_to_py(v, depth=depth + 1) for v in seq]
    return str(value)


def to_lua_table(lua: Any, value: Any) -> Any:
    if isinstance(value, dict):
        table = lua.table()
        for key, item in value.items():
            table[key] = to_lua_table(lua, item)
        return table
    if isinstance(value, (list, tuple)):
        table = lua.table()
        for idx, item in enumerate(value, start=1):
            table[idx] = to_lua_table(lua, item)
        return table
    return value


def flatten_block_contents(lua_blocks: Any) -> list[str]:
    out: list[str] = []
    idx = 1
    while True:
        try:
            block = lua_blocks[idx]
        except Exception:
            block = None
        if block is None:
            break
        try:
            content = block["content"]
        except Exception:
            content = None
        if content is not None:
            out.append(str(content))
        idx += 1
    return out


def parse_turns_from_blocks(blocks: list[str]) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for block in blocks:
        for match in TURN_RE.findall(block):
            turn = int(match)
            if turn > 0 and turn not in seen:
                seen.add(turn)
                out.append(turn)
    return out


def parse_fact_ids_from_blocks(blocks: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for block in blocks:
        for fact_id in FACT_RE.findall(block):
            if fact_id not in seen:
                seen.add(fact_id)
                out.append(fact_id)
    return out


def room_info_to_dict(info: RoomInfo | None) -> dict[str, Any] | None:
    if info is None:
        return None
    return {
        "room_id": int(info.room_id),
        "live_status": int(info.live_status),
        "title": str(info.title),
        "online": int(info.online),
        "live_time": str(info.live_time),
        "raw": info.raw,
    }


def make_message_key(room_id: int, message: DanmakuMessage) -> str:
    uid = extract_user_id(getattr(message, "raw", None))
    return "|".join(
        [
            str(room_id),
            uid,
            str(message.timeline or ""),
            str(message.nickname or ""),
            str(message.text or ""),
        ]
    )


class EmbeddingPyPipelineAdapter:
    def __init__(
        self,
        *,
        llama_bin_dir: Path,
        model_path: Path,
        ctx_size: int,
        gpu_layers: str,
        startup_timeout_s: int,
    ) -> None:
        server_bin = (llama_bin_dir / "llama-server").resolve()
        self._client = LlamaCppServerClient(
            server_bin=server_bin,
            model_path=model_path.resolve(),
            ctx_size=int(ctx_size),
            embeddings=True,
            enable_jinja=False,
            gpu_layers=str(gpu_layers),
            startup_timeout_s=int(startup_timeout_s),
        )
        self.get_embedding = lambda _self, text, mode="query": self.get_embedding_py(text=text, mode=mode)
        self.get_embeddings = lambda _self, texts, mode="query": self.get_embeddings_py(texts=texts, mode=mode)
        self.generate_chat_sync = lambda _self, messages, params=None: ""

    @staticmethod
    def _normalize(vec: list[float]) -> list[float]:
        s = 0.0
        for x in vec:
            s += float(x) * float(x)
        if s <= 0.0:
            return vec
        inv = s ** -0.5
        return [float(x) * inv for x in vec]

    @staticmethod
    def _prefix(text: str, mode: str) -> str:
        text = str(text or "").strip()
        if not text:
            return ""
        if text.startswith("query: ") or text.startswith("passage: "):
            return text
        prefix = "query: " if str(mode or "query") == "query" else "passage: "
        return prefix + text

    def get_embedding_py(self, *, text: object, mode: object = "query") -> list[float]:
        vecs = self.get_embeddings_py(texts=[text], mode=mode)
        return vecs[0] if vecs else []

    def get_embeddings_py(self, *, texts: object, mode: object = "query") -> list[list[float]]:
        prefixed: list[str] = []
        mode_str = str(mode or "query")
        for item in iter_lua_sequence(texts):
            txt = self._prefix(str(item or ""), mode_str)
            if txt:
                prefixed.append(txt)
        if not prefixed:
            return []

        response = self._client.create_embedding(texts=prefixed)
        data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(data, list):
            return []

        out: list[list[float]] = []
        for row in data:
            emb = row.get("embedding") if isinstance(row, dict) else None
            if not isinstance(emb, list):
                continue
            out.append(self._normalize([float(x) for x in emb]))
        return out

    def shutdown(self) -> None:
        self._client.stop()


class LiveMemoryHarness:
    def __init__(self, *, repo_root: Path, run_dir: Path, args: argparse.Namespace) -> None:
        self.repo_root = repo_root.resolve()
        self.run_dir = run_dir.resolve()
        self.args = args
        self.run_dir.mkdir(parents=True, exist_ok=True)
        os.chdir(self.run_dir)
        (self.run_dir / "memory" / "v4" / "topic_graph").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "memory" / "v4" / "runtime").mkdir(parents=True, exist_ok=True)

        self.embedder = EmbeddingPyPipelineAdapter(
            llama_bin_dir=Path(args.llama_bin_dir),
            model_path=Path(args.embed_model),
            ctx_size=int(args.embed_ctx_size),
            gpu_layers=str(args.embed_gpu_layers),
            startup_timeout_s=int(args.embed_startup_timeout),
        )
        self.bridge = MoriMemoryBridge(lua_root=self.repo_root / "mori_memory", py_pipeline=self.embedder)
        self.lua = self.bridge.lua
        self._configure()
        self._shutdown = False

    def _configure(self) -> None:
        lines = [
            'local config = require("module.config")',
            "config.reset()",
            "config.settings.topic.allow_llm_summary = false",
            'config.settings.guard.scope_strategy = "source_room"',
            "config.settings.guard.anchor_scope_prefix = true",
        ]

        if str(self.args.memory_profile) == "capture":
            lines.extend(
                [
                    "config.settings.guard.enabled = true",
                    "config.settings.guard.default_credit_by_source.bilibili = 1.0",
                    "config.settings.guard.credit_decay = 1.0",
                    "config.settings.guard.credit_bonus = 0.0",
                    "config.settings.guard.credit_penalty = 0.0",
                    "config.settings.guard.block_threshold = -1.0",
                    "config.settings.guard.restore_threshold = 0.0",
                    "config.settings.guard.allow_recall_threshold = 0.0",
                    "config.settings.guard.allow_history_threshold = 0.0",
                    "config.settings.guard.allow_topic_threshold = 0.0",
                    "config.settings.guard.allow_memory_write_threshold = 0.0",
                ]
            )

        if int(self.args.max_streams or 0) > 0:
            lines.append(f"config.settings.disentangle.max_streams = {int(self.args.max_streams)}")
        if float(self.args.assign_threshold) >= 0.0:
            lines.append(f"config.settings.disentangle.assign_threshold = {float(self.args.assign_threshold)}")
        if float(self.args.pending_threshold) >= 0.0:
            lines.append(f"config.settings.disentangle.pending_threshold = {float(self.args.pending_threshold)}")
        if float(self.args.pending_margin) >= 0.0:
            lines.append(f"config.settings.disentangle.pending_margin = {float(self.args.pending_margin)}")
        if int(self.args.commit_idle_turns or 0) > 0:
            lines.append(f"config.settings.disentangle.commit_idle_turns = {int(self.args.commit_idle_turns)}")
        if int(self.args.commit_chunk_turns or 0) > 0:
            lines.append(f"config.settings.disentangle.commit_chunk_turns = {int(self.args.commit_chunk_turns)}")
        if int(self.args.pending_context_turns or 0) > 0:
            lines.append(f"config.settings.disentangle.pending_context_turns = {int(self.args.pending_context_turns)}")

        self.lua.execute("\n".join(lines))

    def compile_context(self, meta: dict[str, Any]) -> list[str]:
        blocks = self.bridge.compile_context(to_lua_table(self.lua, meta))
        return flatten_block_contents(blocks)

    def ingest_turn(self, meta: dict[str, Any]) -> dict[str, Any]:
        result = self.bridge.ingest_turn(to_lua_table(self.lua, meta))
        converted = lua_to_py(result)
        return converted if isinstance(converted, dict) else {"raw_result": converted}

    def shutdown(self) -> None:
        if self._shutdown:
            return
        try:
            self.bridge.shutdown()
        finally:
            self.embedder.shutdown()
            self._shutdown = True


class StopFlag:
    def __init__(self) -> None:
        self.stop_requested = False
        self.signal_name = ""

    def handler(self, signum: int, _frame: Any) -> None:
        self.stop_requested = True
        self.signal_name = signal.Signals(signum).name


def make_run_dir(args: argparse.Namespace) -> Path:
    if str(args.run_dir or "").strip():
        return Path(str(args.run_dir)).expanduser().resolve()
    root = Path(str(args.capture_root)).expanduser().resolve()
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    return (root / f"bilibili_room_{int(args.room_id)}_{stamp}").resolve()


def fetch_room_status(room_id: int) -> tuple[RoomInfo | None, str]:
    try:
        return get_room_info(room_id=room_id), ""
    except Exception as e:
        return None, str(e)


def emit_room_status(path: Path, *, checked_at: float, info: RoomInfo | None, error: str = "") -> None:
    payload = {
        "checked_at": checked_at,
        "checked_at_iso": now_iso(checked_at),
        "ok": not bool(error),
        "error": str(error or ""),
        "room": room_info_to_dict(info),
    }
    append_jsonl(path, payload)


def process_message(
    *,
    harness: LiveMemoryHarness,
    turn: int,
    room_id: int,
    message: DanmakuMessage,
    room_info: RoomInfo | None,
) -> dict[str, Any]:
    user_id = extract_user_id(getattr(message, "raw", None))
    base_meta = {
        "turn": int(turn),
        "source": "bilibili",
        "room_id": int(room_id),
        "timeline": str(message.timeline or ""),
        "nickname": str(message.nickname or ""),
        "user_id": str(user_id),
        "text": str(message.text or ""),
        "user_input": str(message.text or ""),
        "raw_user_input": str(message.text or ""),
        "assistant_text": "",
        "atomic_include_assistant": False,
    }

    t0 = time.perf_counter()
    blocks = harness.compile_context(base_meta)
    compile_ms = (time.perf_counter() - t0) * 1000.0

    t1 = time.perf_counter()
    ingest_result = harness.ingest_turn(base_meta)
    ingest_ms = (time.perf_counter() - t1) * 1000.0

    context_preview = "\n\n".join(blocks[:3])
    if len(context_preview) > 1200:
        context_preview = context_preview[:1200] + "..."

    disentangle = ingest_result.get("disentangle")
    if not isinstance(disentangle, dict):
        disentangle = {}

    return {
        "turn": int(turn),
        "captured_at": now_ts(),
        "captured_at_iso": now_iso(),
        "message": {
            "timeline": str(message.timeline or ""),
            "ts": float(message.ts or 0.0),
            "nickname": str(message.nickname or ""),
            "user_id": str(user_id),
            "text": str(message.text or ""),
            "raw": getattr(message, "raw", None),
        },
        "room": room_info_to_dict(room_info),
        "compile_ms": round(compile_ms, 3),
        "ingest_ms": round(ingest_ms, 3),
        "context_block_count": len(blocks),
        "context_char_count": sum(len(block) for block in blocks),
        "context_preview": context_preview,
        "context_blocks": blocks,
        "retrieved_turns_from_blocks": parse_turns_from_blocks(blocks),
        "retrieved_fact_ids_from_blocks": parse_fact_ids_from_blocks(blocks),
        "ingest": ingest_result,
        "flow_flags": {
            "reason": str(disentangle.get("reason") or ""),
            "mode": str(disentangle.get("mode") or ""),
            "thread_key": str(disentangle.get("thread_key") or ""),
            "sequence_key": str(disentangle.get("sequence_key") or ""),
            "dropped": bool(disentangle.get("dropped")),
            "pending_only": bool(disentangle.get("pending_only")),
            "local_only": bool(disentangle.get("local_only")),
            "orphaned": bool(disentangle.get("orphaned")),
            "is_new": bool(disentangle.get("is_new")),
            "merged": bool(disentangle.get("merged")),
        },
    }


def main() -> int:
    args = parse_args()
    run_dir = make_run_dir(args)
    logs_dir = run_dir
    logs_dir.mkdir(parents=True, exist_ok=True)

    raw_path = logs_dir / "raw_messages.jsonl"
    trace_path = logs_dir / "memory_trace.jsonl"
    room_status_path = logs_dir / "room_status.jsonl"
    events_path = logs_dir / "run_events.jsonl"
    manifest_path = logs_dir / "manifest.json"
    summary_path = logs_dir / "summary.json"

    startup_info, startup_error = fetch_room_status(int(args.room_id))
    manifest = {
        "created_at": now_ts(),
        "created_at_iso": now_iso(),
        "repo_root": str(REPO_ROOT),
        "run_dir": str(run_dir),
        "room_id": int(args.room_id),
        "room_url": str(args.room_url or f"https://live.bilibili.com/{int(args.room_id)}"),
        "startup_room": room_info_to_dict(startup_info),
        "startup_room_error": str(startup_error or ""),
        "python": sys.executable,
        "embed_model": str(args.embed_model),
        "llama_bin_dir": str(args.llama_bin_dir),
        "memory_profile": str(args.memory_profile),
        "assistant_mode": "empty_assistant_text",
        "files": {
            "raw_messages": str(raw_path),
            "memory_trace": str(trace_path),
            "room_status": str(room_status_path),
            "run_events": str(events_path),
            "summary": str(summary_path),
            "memory_root": str((run_dir / "memory").resolve()),
        },
        "args": vars(args),
    }
    write_json(manifest_path, manifest)
    emit_room_status(room_status_path, checked_at=now_ts(), info=startup_info, error=startup_error)

    print(f"run_dir={run_dir}")
    if startup_info is not None:
        print(
            f"room={startup_info.room_id} live_status={startup_info.live_status} online={startup_info.online} "
            f"title={startup_info.title}"
        )
    elif startup_error:
        print(f"room_status_error={startup_error}")

    stop_flag = StopFlag()
    signal.signal(signal.SIGINT, stop_flag.handler)
    signal.signal(signal.SIGTERM, stop_flag.handler)

    harness = LiveMemoryHarness(repo_root=REPO_ROOT, run_dir=run_dir, args=args)
    poller = BilibiliLivePoller(room_id=int(args.room_id))

    start_ts = now_ts()
    last_status_check = 0.0
    last_room_info = startup_info
    last_room_error = startup_error
    turn = 0
    processed = 0
    room_offline = False
    compile_ms_values: list[float] = []
    ingest_ms_values: list[float] = []
    context_chars: list[int] = []
    flow_reason_counts: Counter[str] = Counter()
    flow_mode_counts: Counter[str] = Counter()
    flag_counts: Counter[str] = Counter()
    error_count = 0
    seen_keys: set[str] = set()
    seen_queue: deque[str] = deque(maxlen=4096)

    def note_seen(key: str) -> bool:
        if key in seen_keys:
            return False
        if len(seen_queue) == seen_queue.maxlen:
            old = seen_queue.popleft()
            seen_keys.discard(old)
        seen_queue.append(key)
        seen_keys.add(key)
        return True

    def record_event(event_type: str, **payload: Any) -> None:
        append_jsonl(
            events_path,
            {
                "ts": now_ts(),
                "ts_iso": now_iso(),
                "type": event_type,
                **payload,
            },
        )

    try:
        if int(args.catchup or 0) > 0:
            try:
                current = poller.fetch()
                ordered = sorted(
                    current,
                    key=lambda m: (float(m.ts or 0.0), str(m.timeline), str(m.nickname), str(m.text)),
                )
                for message in ordered[-int(args.catchup) :]:
                    key = make_message_key(int(args.room_id), message)
                    note_seen(key)
                    turn += 1
                    append_jsonl(
                        raw_path,
                        {
                            "turn": turn,
                            "catchup": True,
                            "captured_at": now_ts(),
                            "captured_at_iso": now_iso(),
                            "room_id": int(args.room_id),
                            "timeline": str(message.timeline or ""),
                            "ts": float(message.ts or 0.0),
                            "nickname": str(message.nickname or ""),
                            "user_id": extract_user_id(getattr(message, "raw", None)),
                            "text": str(message.text or ""),
                            "raw": getattr(message, "raw", None),
                        },
                    )
                    trace = process_message(
                        harness=harness,
                        turn=turn,
                        room_id=int(args.room_id),
                        message=message,
                        room_info=last_room_info,
                    )
                    trace["catchup"] = True
                    append_jsonl(trace_path, trace)
                    processed += 1
                    compile_ms_values.append(float(trace["compile_ms"]))
                    ingest_ms_values.append(float(trace["ingest_ms"]))
                    context_chars.append(int(trace["context_char_count"]))
                    flow = trace.get("flow_flags", {})
                    flow_reason_counts[str(flow.get("reason") or "")] += 1
                    flow_mode_counts[str(flow.get("mode") or "")] += 1
                    for key_name in ("dropped", "pending_only", "local_only", "orphaned", "is_new", "merged"):
                        if bool(flow.get(key_name)):
                            flag_counts[key_name] += 1
            except Exception as e:
                error_count += 1
                record_event("catchup_error", error=str(e))

        while True:
            now = now_ts()
            if stop_flag.stop_requested:
                record_event("stop_signal", signal=stop_flag.signal_name)
                break
            if float(args.stop_after or 0.0) > 0.0 and (now - start_ts) >= float(args.stop_after):
                record_event("stop_after", seconds=float(args.stop_after))
                break
            if int(args.max_messages or 0) > 0 and processed >= int(args.max_messages):
                record_event("max_messages", count=int(args.max_messages))
                break

            if (now - last_status_check) >= float(args.live_check_interval):
                last_status_check = now
                last_room_info, last_room_error = fetch_room_status(int(args.room_id))
                emit_room_status(room_status_path, checked_at=now, info=last_room_info, error=last_room_error)
                if last_room_info is not None and int(last_room_info.live_status) != 1:
                    room_offline = True
                    record_event(
                        "room_offline",
                        live_status=int(last_room_info.live_status),
                        title=str(last_room_info.title),
                        online=int(last_room_info.online),
                    )
                    if bool(args.exit_when_offline):
                        break

            try:
                messages = poller.poll_new()
            except Exception as e:
                error_count += 1
                record_event("poll_error", error=str(e))
                time.sleep(float(args.interval))
                continue

            if not messages:
                time.sleep(float(args.interval))
                continue

            for message in messages:
                key = make_message_key(int(args.room_id), message)
                if not note_seen(key):
                    continue
                turn += 1
                raw_payload = {
                    "turn": turn,
                    "catchup": False,
                    "captured_at": now_ts(),
                    "captured_at_iso": now_iso(),
                    "room_id": int(args.room_id),
                    "timeline": str(message.timeline or ""),
                    "ts": float(message.ts or 0.0),
                    "nickname": str(message.nickname or ""),
                    "user_id": extract_user_id(getattr(message, "raw", None)),
                    "text": str(message.text or ""),
                    "raw": getattr(message, "raw", None),
                }
                append_jsonl(raw_path, raw_payload)

                try:
                    trace = process_message(
                        harness=harness,
                        turn=turn,
                        room_id=int(args.room_id),
                        message=message,
                        room_info=last_room_info,
                    )
                except Exception as e:
                    error_count += 1
                    record_event("process_error", turn=turn, error=str(e), raw=raw_payload)
                    continue

                append_jsonl(trace_path, trace)
                processed += 1
                compile_ms_values.append(float(trace["compile_ms"]))
                ingest_ms_values.append(float(trace["ingest_ms"]))
                context_chars.append(int(trace["context_char_count"]))

                flow = trace.get("flow_flags", {})
                flow_reason_counts[str(flow.get("reason") or "")] += 1
                flow_mode_counts[str(flow.get("mode") or "")] += 1
                for key_name in ("dropped", "pending_only", "local_only", "orphaned", "is_new", "merged"):
                    if bool(flow.get(key_name)):
                        flag_counts[key_name] += 1

                if processed % int(args.status_every) == 0:
                    print(
                        f"processed={processed} turn={turn} blocks={trace['context_block_count']} "
                        f"reason={flow.get('reason') or ''} mode={flow.get('mode') or ''}"
                    )

                if int(args.max_messages or 0) > 0 and processed >= int(args.max_messages):
                    record_event("max_messages", count=int(args.max_messages))
                    break

            if int(args.max_messages or 0) > 0 and processed >= int(args.max_messages):
                break

            time.sleep(float(args.interval))
    finally:
        try:
            harness.shutdown()
        except Exception as e:
            error_count += 1
            record_event("shutdown_error", error=str(e))

    last_room = room_info_to_dict(last_room_info)
    summary = {
        "finished_at": now_ts(),
        "finished_at_iso": now_iso(),
        "duration_s": round(max(0.0, now_ts() - start_ts), 3),
        "room_id": int(args.room_id),
        "room_offline_seen": bool(room_offline),
        "processed_messages": int(processed),
        "turns_attempted": int(turn),
        "error_count": int(error_count),
        "compile_ms": summarize_ms(compile_ms_values),
        "ingest_ms": summarize_ms(ingest_ms_values),
        "avg_context_chars": round(sum(context_chars) / len(context_chars), 2) if context_chars else 0.0,
        "empty_context_rate": round(
            sum(1 for value in context_chars if int(value) <= 0) / float(len(context_chars)),
            4,
        )
        if context_chars
        else 0.0,
        "flow_reason_counts": dict(sorted(flow_reason_counts.items())),
        "flow_mode_counts": dict(sorted(flow_mode_counts.items())),
        "flag_counts": dict(sorted(flag_counts.items())),
        "last_room": last_room,
        "last_room_error": str(last_room_error or ""),
        "run_dir": str(run_dir),
    }
    write_json(summary_path, summary)

    print(
        f"done processed={processed} errors={error_count} room_offline={int(room_offline)} "
        f"summary={summary_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
