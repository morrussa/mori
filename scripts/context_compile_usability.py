#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import capture_bilibili_memory_live as livecap
from mori_live_stream.bilibili_room import RoomInfo


EMOTICON_RE = re.compile(r"\[[^\]]+\]")
SPACE_RE = re.compile(r"\s+")
PURE_CHEER_RE = re.compile(r"^(6+|8+|9+|0+|[哈啊哇哦草乐]+)$")

SHORT_REACTION_SET = {
    "666",
    "6666",
    "66",
    "88",
    "888",
    "8888",
    "红了",
    "真红了",
    "这是真红了",
    "太强了",
    "太强",
    "太秀了",
    "确实",
    "逆天",
    "离谱",
    "牛",
    "妙",
    "草",
    "乐",
    "绷不住",
    "笑死",
    "有节目效果",
    "这也行",
}

DEICTIC_HINTS = ("这", "那", "也", "还", "就", "真", "太", "节目效果", "红了", "强")
FLAG_KEYS = ("dropped", "pending_only", "local_only", "orphaned", "is_new", "merged")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="context-compile-usability",
        description="Replay real chat history with current mori_memory and judge context-compilation usability.",
    )
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--source-run-dir", default="", help="Captured live run directory with raw_messages.jsonl + manifest.json.")
    source.add_argument("--trace-path", default="", help="Existing memory_trace.jsonl to evaluate without replay.")

    p.add_argument("--output-run-dir", default="", help="Replay output directory. Required only for replay mode if you want a fixed path.")
    p.add_argument("--summary-out", default="", help="Optional exact summary output path.")

    p.add_argument("--window-hours", type=float, default=3.0, help="Replay only the first N hours from the source trace (0 = all).")
    p.add_argument("--max-messages", type=int, default=0, help="Replay only the first N selected messages (0 = all).")
    p.add_argument("--status-every", type=int, default=100, help="Print one progress line every N replayed messages.")
    p.add_argument("--sample-limit", type=int, default=12, help="Max samples per sample bucket.")

    p.add_argument("--strategy-profile", default="live_room", help="Strategy profile passed into mori_memory.")
    p.add_argument("--memory-profile", default="", help="Override memory profile. Default: source manifest value or capture.")
    p.add_argument("--llama-bin-dir", default="", help="Override llama.cpp build/bin path.")
    p.add_argument("--embed-model", default="", help="Override embedding model path.")
    p.add_argument("--embed-ctx-size", type=int, default=0, help="Override embedding server ctx size.")
    p.add_argument("--embed-gpu-layers", default="", help='Override embedding gpu layers, for example "all" or "0".')
    p.add_argument("--embed-startup-timeout", type=int, default=0, help="Override embedding server startup timeout seconds.")

    p.add_argument("--max-streams", type=int, default=0, help="Override disentangle.max_streams.")
    p.add_argument("--assign-threshold", type=float, default=-1.0, help="Override disentangle.assign_threshold.")
    p.add_argument("--pending-threshold", type=float, default=-1.0, help="Override disentangle.pending_threshold.")
    p.add_argument("--pending-margin", type=float, default=-1.0, help="Override disentangle.pending_margin.")
    p.add_argument("--commit-idle-turns", type=int, default=0, help="Override disentangle.commit_idle_turns.")
    p.add_argument("--commit-chunk-turns", type=int, default=0, help="Override disentangle.commit_chunk_turns.")
    p.add_argument("--pending-context-turns", type=int, default=0, help="Override disentangle.pending_context_turns.")

    p.add_argument("--recent-turn-span", type=int, default=8, help="A retrieved turn within this span counts as recent context.")
    p.add_argument("--compile-p95-pass-ms", type=float, default=40.0, help="Pass threshold for compile p95 latency.")
    p.add_argument("--compile-p95-warn-ms", type=float, default=60.0, help="Warn threshold for compile p95 latency.")
    p.add_argument("--avg-context-pass-chars", type=float, default=350.0, help="Pass threshold for average compiled chars.")
    p.add_argument("--avg-context-warn-chars", type=float, default=500.0, help="Warn threshold for average compiled chars.")
    p.add_argument("--bloat-chars", type=int, default=600, help="Per-turn char count beyond this is considered bloated.")
    p.add_argument("--bloat-blocks", type=int, default=6, help="Per-turn block count beyond this is considered bloated.")
    p.add_argument("--bloat-rate-pass", type=float, default=0.02, help="Pass threshold for bloated-turn rate.")
    p.add_argument("--bloat-rate-warn", type=float, default=0.08, help="Warn threshold for bloated-turn rate.")
    p.add_argument("--reaction-recent-pass-rate", type=float, default=0.45, help="Pass threshold for recent-context recall on short reaction turns.")
    p.add_argument("--reaction-recent-warn-rate", type=float, default=0.25, help="Warn threshold for recent-context recall on short reaction turns.")
    p.add_argument("--retrieval-recency-pass-rate", type=float, default=0.85, help="Pass threshold for retrievals staying recent.")
    p.add_argument("--retrieval-recency-warn-rate", type=float, default=0.70, help="Warn threshold for retrievals staying recent.")
    p.add_argument("--top-reason-pass-share", type=float, default=0.50, help="Pass threshold for dominant flow-reason share.")
    p.add_argument("--top-reason-warn-share", type=float, default=0.75, help="Warn threshold for dominant flow-reason share.")

    return p.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            if isinstance(data, dict):
                rows.append(data)
    return rows


def default_summary_path(args: argparse.Namespace, *, trace_path: Path | None, output_run_dir: Path | None) -> Path:
    if str(args.summary_out or "").strip():
        return Path(str(args.summary_out)).expanduser().resolve()
    if output_run_dir is not None:
        return (output_run_dir / "summary.json").resolve()
    assert trace_path is not None
    return trace_path.with_name(trace_path.stem + ".usability_summary.json").resolve()


def make_output_run_dir(args: argparse.Namespace, source_run_dir: Path) -> Path:
    if str(args.output_run_dir or "").strip():
        return Path(str(args.output_run_dir)).expanduser().resolve()
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    return (source_run_dir / f"usability_replay_{stamp}").resolve()


def normalize_text(text: str) -> str:
    s = EMOTICON_RE.sub("", str(text or ""))
    s = SPACE_RE.sub("", s)
    return s.strip()


def is_context_hungry(text: str) -> bool:
    s = normalize_text(text)
    if not s:
        return True
    if s in SHORT_REACTION_SET:
        return True
    if PURE_CHEER_RE.fullmatch(s):
        return True
    if len(s) <= 2:
        return True
    if len(s) <= 4:
        return True
    if len(s) <= 6 and any(token in s for token in DEICTIC_HINTS):
        return True
    return False


def build_room_info(manifest: dict[str, Any]) -> RoomInfo | None:
    room = manifest.get("startup_room")
    if not isinstance(room, dict):
        return None
    raw = room.get("raw")
    return RoomInfo(
        room_id=int(room.get("room_id") or 0),
        live_status=int(room.get("live_status") or 0),
        title=str(room.get("title") or ""),
        online=int(room.get("online") or 0),
        live_time=str(room.get("live_time") or ""),
        raw=raw if isinstance(raw, dict) else {},
    )


class ReplayHarness(livecap.LiveMemoryHarness):
    def _configure(self) -> None:
        lines = [
            'local config = require("module.config")',
            "config.reset()",
            (
                f'if config.apply_strategy_profile ~= nil then '
                f'assert(config.apply_strategy_profile("{str(self.args.strategy_profile)}")) '
                f'end'
            ),
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


def build_harness_args(args: argparse.Namespace, manifest: dict[str, Any]) -> SimpleNamespace:
    manifest_args = manifest.get("args")
    if not isinstance(manifest_args, dict):
        manifest_args = {}

    def pick_str(cli_value: str, manifest_key: str, fallback: str) -> str:
        cli_value = str(cli_value or "").strip()
        if cli_value:
            return cli_value
        manifest_value = str(manifest_args.get(manifest_key) or manifest.get(manifest_key) or "").strip()
        return manifest_value or fallback

    def pick_int(cli_value: int, manifest_key: str, fallback: int) -> int:
        if int(cli_value or 0) > 0:
            return int(cli_value)
        manifest_value = manifest_args.get(manifest_key)
        return int(manifest_value or fallback)

    ns = SimpleNamespace()
    ns.strategy_profile = str(args.strategy_profile or "live_room")
    ns.memory_profile = pick_str(args.memory_profile, "memory_profile", "capture")
    ns.llama_bin_dir = pick_str(args.llama_bin_dir, "llama_bin_dir", "")
    ns.embed_model = pick_str(args.embed_model, "embed_model", "")
    ns.embed_ctx_size = pick_int(args.embed_ctx_size, "embed_ctx_size", 8192)
    ns.embed_gpu_layers = pick_str(args.embed_gpu_layers, "embed_gpu_layers", "all")
    ns.embed_startup_timeout = pick_int(args.embed_startup_timeout, "embed_startup_timeout", 600)
    ns.max_streams = int(args.max_streams or 0)
    ns.assign_threshold = float(args.assign_threshold)
    ns.pending_threshold = float(args.pending_threshold)
    ns.pending_margin = float(args.pending_margin)
    ns.commit_idle_turns = int(args.commit_idle_turns or 0)
    ns.commit_chunk_turns = int(args.commit_chunk_turns or 0)
    ns.pending_context_turns = int(args.pending_context_turns or 0)
    return ns


def select_messages(raw_rows: list[dict[str, Any]], *, window_hours: float, max_messages: int) -> list[dict[str, Any]]:
    if not raw_rows:
        return []
    first_ts = float(raw_rows[0].get("ts") or 0.0)
    selected: list[dict[str, Any]] = []
    cutoff = first_ts + float(window_hours) * 3600.0 if float(window_hours or 0.0) > 0.0 else 0.0
    for row in raw_rows:
        ts = float(row.get("ts") or 0.0)
        if cutoff > 0.0 and ts > cutoff:
            break
        selected.append(row)
        if int(max_messages or 0) > 0 and len(selected) >= int(max_messages):
            break
    return selected


def lte_status(value: float, pass_max: float, warn_max: float) -> str:
    if value <= pass_max:
        return "pass"
    if value <= warn_max:
        return "warn"
    return "fail"


def gte_status(value: float, pass_min: float, warn_min: float) -> str:
    if value >= pass_min:
        return "pass"
    if value >= warn_min:
        return "warn"
    return "fail"


def sample_row(row: dict[str, Any], *, recent_turn_span: int) -> dict[str, Any]:
    turn = int(row.get("turn") or 0)
    message = row.get("message")
    if not isinstance(message, dict):
        message = {}
    flow = row.get("flow_flags")
    if not isinstance(flow, dict):
        flow = {}
    retrieved = [int(t) for t in row.get("retrieved_turns_from_blocks") or [] if int(t) > 0]
    ages = [turn - t for t in retrieved if turn > t]
    return {
        "turn": turn,
        "text": str(message.get("text") or ""),
        "reason": str(flow.get("reason") or ""),
        "mode": str(flow.get("mode") or ""),
        "compile_ms": float(row.get("compile_ms") or 0.0),
        "context_block_count": int(row.get("context_block_count") or 0),
        "context_char_count": int(row.get("context_char_count") or 0),
        "retrieved_turns": retrieved,
        "nearest_retrieved_age": min(ages) if ages else None,
        "has_recent_retrieval": any(age <= int(recent_turn_span) for age in ages),
        "context_preview": str(row.get("context_preview") or ""),
    }


def evaluate_rows(rows: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    compile_ms_values: list[float] = []
    context_chars: list[int] = []
    context_blocks: list[int] = []
    flow_reason_counts: Counter[str] = Counter()
    flow_mode_counts: Counter[str] = Counter()
    flag_counts: Counter[str] = Counter()

    reaction_total = 0
    reaction_recent = 0
    reaction_missing = 0
    reaction_stale = 0

    retrieval_total = 0
    retrieval_recent = 0

    bloated_turns = 0
    helped_samples: list[dict[str, Any]] = []
    missed_samples: list[dict[str, Any]] = []
    stale_samples: list[dict[str, Any]] = []
    bloated_samples: list[dict[str, Any]] = []

    for row in rows:
        compile_ms_values.append(float(row.get("compile_ms") or 0.0))
        char_count = int(row.get("context_char_count") or 0)
        block_count = int(row.get("context_block_count") or 0)
        context_chars.append(char_count)
        context_blocks.append(block_count)

        flow = row.get("flow_flags")
        if not isinstance(flow, dict):
            flow = {}
        reason = str(flow.get("reason") or "<empty>")
        mode = str(flow.get("mode") or "<empty>")
        flow_reason_counts[reason] += 1
        flow_mode_counts[mode] += 1
        for key in FLAG_KEYS:
            if flow.get(key) is True:
                flag_counts[key] += 1

        message = row.get("message")
        if not isinstance(message, dict):
            message = {}
        text = str(message.get("text") or "")
        turn = int(row.get("turn") or 0)
        retrieved = [int(t) for t in row.get("retrieved_turns_from_blocks") or [] if int(t) > 0 and int(t) < turn]
        ages = [turn - t for t in retrieved if turn > t]
        recent = [age for age in ages if age <= int(args.recent_turn_span)]

        if retrieved:
            retrieval_total += 1
            if recent:
                retrieval_recent += 1

        if is_context_hungry(text):
            reaction_total += 1
            sample = sample_row(row, recent_turn_span=int(args.recent_turn_span))
            if recent:
                reaction_recent += 1
                if len(helped_samples) < int(args.sample_limit):
                    helped_samples.append(sample)
            elif retrieved:
                reaction_stale += 1
                if len(stale_samples) < int(args.sample_limit):
                    stale_samples.append(sample)
            else:
                reaction_missing += 1
                if len(missed_samples) < int(args.sample_limit):
                    missed_samples.append(sample)

        if char_count > int(args.bloat_chars) or block_count > int(args.bloat_blocks):
            bloated_turns += 1
            bloated_samples.append(sample_row(row, recent_turn_span=int(args.recent_turn_span)))

    bloated_samples.sort(key=lambda item: (item["context_char_count"], item["context_block_count"]), reverse=True)
    bloated_samples = bloated_samples[: int(args.sample_limit)]

    compile_p95 = livecap.stats_percentile(compile_ms_values, 95.0) if compile_ms_values else 0.0
    avg_chars = (sum(context_chars) / len(context_chars)) if context_chars else 0.0
    bloat_rate = (bloated_turns / len(rows)) if rows else 0.0
    reaction_recent_rate = (reaction_recent / reaction_total) if reaction_total else 0.0
    retrieval_recent_rate = (retrieval_recent / retrieval_total) if retrieval_total else 0.0

    top_reason = ""
    top_reason_count = 0
    if flow_reason_counts:
        top_reason, top_reason_count = flow_reason_counts.most_common(1)[0]
    top_reason_share = (top_reason_count / len(rows)) if rows else 0.0

    dominant_reason_check = {
        "status": lte_status(
            top_reason_share,
            float(args.top_reason_pass_share),
            float(args.top_reason_warn_share),
        ),
        "top_reason": top_reason,
        "share": round(top_reason_share, 4),
        "count": int(top_reason_count),
    }

    checks = {
        "non_empty_flow_reason": {
            "status": "pass" if "<empty>" not in flow_reason_counts else "fail",
            "empty_turns": int(flow_reason_counts.get("<empty>", 0)),
        },
        "non_empty_flow_mode": {
            "status": "pass" if "<empty>" not in flow_mode_counts else "fail",
            "empty_turns": int(flow_mode_counts.get("<empty>", 0)),
        },
        "compile_latency_p95": {
            "status": lte_status(compile_p95, float(args.compile_p95_pass_ms), float(args.compile_p95_warn_ms)),
            "p95_ms": round(compile_p95, 3),
            "pass_if_lte_ms": float(args.compile_p95_pass_ms),
            "warn_if_lte_ms": float(args.compile_p95_warn_ms),
        },
        "avg_context_budget": {
            "status": lte_status(avg_chars, float(args.avg_context_pass_chars), float(args.avg_context_warn_chars)),
            "avg_chars": round(avg_chars, 2),
            "pass_if_lte_chars": float(args.avg_context_pass_chars),
            "warn_if_lte_chars": float(args.avg_context_warn_chars),
        },
        "bloated_turn_rate": {
            "status": lte_status(bloat_rate, float(args.bloat_rate_pass), float(args.bloat_rate_warn)),
            "rate": round(bloat_rate, 4),
            "bloated_turns": int(bloated_turns),
            "total_turns": int(len(rows)),
            "char_threshold": int(args.bloat_chars),
            "block_threshold": int(args.bloat_blocks),
        },
        "reaction_recent_context_recall": {
            "status": gte_status(
                reaction_recent_rate,
                float(args.reaction_recent_pass_rate),
                float(args.reaction_recent_warn_rate),
            ),
            "rate": round(reaction_recent_rate, 4),
            "recent_context_turns": int(reaction_recent),
            "reaction_like_turns": int(reaction_total),
            "missing_context_turns": int(reaction_missing),
            "stale_context_turns": int(reaction_stale),
            "recent_turn_span": int(args.recent_turn_span),
        },
        "retrieval_recency": {
            "status": gte_status(
                retrieval_recent_rate,
                float(args.retrieval_recency_pass_rate),
                float(args.retrieval_recency_warn_rate),
            ),
            "rate": round(retrieval_recent_rate, 4),
            "recent_retrieval_turns": int(retrieval_recent),
            "retrieval_turns": int(retrieval_total),
            "recent_turn_span": int(args.recent_turn_span),
        },
        "dominant_flow_reason_share": dominant_reason_check,
    }

    summary = {
        "generated_at": time.time(),
        "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        "evaluated_turns": int(len(rows)),
        "compile_ms": livecap.summarize_ms(compile_ms_values),
        "context_char_stats": {
            "count": len(context_chars),
            "avg_chars": round(avg_chars, 2),
            "p50_chars": round(livecap.stats_percentile(context_chars, 50.0), 2) if context_chars else 0.0,
            "p95_chars": round(livecap.stats_percentile(context_chars, 95.0), 2) if context_chars else 0.0,
            "max_chars": int(max(context_chars)) if context_chars else 0,
        },
        "context_block_stats": {
            "count": len(context_blocks),
            "avg_blocks": round((sum(context_blocks) / len(context_blocks)), 2) if context_blocks else 0.0,
            "p50_blocks": round(livecap.stats_percentile(context_blocks, 50.0), 2) if context_blocks else 0.0,
            "p95_blocks": round(livecap.stats_percentile(context_blocks, 95.0), 2) if context_blocks else 0.0,
            "max_blocks": int(max(context_blocks)) if context_blocks else 0,
        },
        "flow_reason_counts": dict(sorted(flow_reason_counts.items())),
        "flow_mode_counts": dict(sorted(flow_mode_counts.items())),
        "flag_counts": dict(sorted(flag_counts.items())),
        "checks": checks,
        "samples": {
            "reaction_helped": helped_samples,
            "reaction_missed": missed_samples,
            "reaction_stale": stale_samples,
            "bloated_turns": bloated_samples,
        },
        "heuristics": {
            "context_hungry_rule": "short / deictic / cheer-like turns are treated as turns that need history to become meaningful",
            "recent_context_rule": f"retrieved turn age <= {int(args.recent_turn_span)}",
        },
    }
    return summary


def print_summary(summary: dict[str, Any]) -> None:
    print("Usability checks")
    checks = summary.get("checks")
    if not isinstance(checks, dict):
        return
    for key in (
        "non_empty_flow_reason",
        "non_empty_flow_mode",
        "compile_latency_p95",
        "avg_context_budget",
        "bloated_turn_rate",
        "reaction_recent_context_recall",
        "retrieval_recency",
        "dominant_flow_reason_share",
    ):
        value = checks.get(key)
        if not isinstance(value, dict):
            continue
        status = str(value.get("status") or "")
        print(f"[{status}] {key}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}")


def run_replay(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any], Path, Path]:
    source_run_dir = Path(str(args.source_run_dir)).expanduser().resolve()
    manifest_path = source_run_dir / "manifest.json"
    raw_path = source_run_dir / "raw_messages.jsonl"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"missing manifest: {manifest_path}")
    if not raw_path.is_file():
        raise FileNotFoundError(f"missing raw messages: {raw_path}")

    manifest = load_json(manifest_path)
    raw_rows = load_jsonl(raw_path)
    selected_rows = select_messages(raw_rows, window_hours=float(args.window_hours), max_messages=int(args.max_messages))
    if not selected_rows:
        raise RuntimeError("no selected messages to replay")

    output_run_dir = make_output_run_dir(args, source_run_dir)
    output_run_dir.mkdir(parents=True, exist_ok=True)
    trace_path = (output_run_dir / "memory_trace.jsonl").resolve()
    summary_path = default_summary_path(args, trace_path=None, output_run_dir=output_run_dir)

    harness_args = build_harness_args(args, manifest)
    room_info = build_room_info(manifest)
    repo_root = Path(str(manifest.get("repo_root") or livecap.REPO_ROOT)).resolve()
    room_id = int(manifest.get("room_id") or 0)

    rows: list[dict[str, Any]] = []
    harness = ReplayHarness(repo_root=repo_root, run_dir=output_run_dir, args=harness_args)
    try:
        for idx, raw_row in enumerate(selected_rows, start=1):
            msg = SimpleNamespace(
                timeline=str(raw_row.get("timeline") or ""),
                ts=float(raw_row.get("ts") or 0.0),
                nickname=str(raw_row.get("nickname") or ""),
                text=str(raw_row.get("text") or ""),
                raw=raw_row.get("raw"),
            )
            trace_row = livecap.process_message(
                harness=harness,
                turn=int(raw_row.get("turn") or idx),
                room_id=room_id,
                message=msg,
                room_info=room_info,
            )
            livecap.append_jsonl(trace_path, trace_row)
            rows.append(trace_row)
            if int(args.status_every or 0) > 0 and idx % int(args.status_every) == 0:
                print(
                    f"replayed={idx} turn={trace_row.get('turn')} "
                    f"compile_p95_so_far={round(livecap.stats_percentile([float(r.get('compile_ms') or 0.0) for r in rows], 95.0), 3)}"
                )
    finally:
        harness.shutdown()

    summary = evaluate_rows(rows, args)
    summary.update(
        {
            "source_run_dir": str(source_run_dir),
            "output_run_dir": str(output_run_dir),
            "window_hours": float(args.window_hours),
            "selected_messages": int(len(selected_rows)),
            "selected_turn_min": int(selected_rows[0].get("turn") or 0),
            "selected_turn_max": int(selected_rows[-1].get("turn") or 0),
            "files": {
                "trace": str(trace_path),
                "summary": str(summary_path),
            },
        }
    )

    replay_manifest = {
        "created_at": time.time(),
        "created_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        "repo_root": str(repo_root),
        "source_run_dir": str(source_run_dir),
        "output_run_dir": str(output_run_dir),
        "strategy_profile": str(harness_args.strategy_profile),
        "memory_profile": str(harness_args.memory_profile),
        "files": {
            "trace": str(trace_path),
            "summary": str(summary_path),
            "source_manifest": str(manifest_path),
            "source_raw_messages": str(raw_path),
        },
        "window": {
            "hours": float(args.window_hours),
            "selected_messages": int(len(selected_rows)),
            "selected_turn_min": int(selected_rows[0].get("turn") or 0),
            "selected_turn_max": int(selected_rows[-1].get("turn") or 0),
        },
        "heuristic_thresholds": {
            "recent_turn_span": int(args.recent_turn_span),
            "compile_p95_pass_ms": float(args.compile_p95_pass_ms),
            "compile_p95_warn_ms": float(args.compile_p95_warn_ms),
            "avg_context_pass_chars": float(args.avg_context_pass_chars),
            "avg_context_warn_chars": float(args.avg_context_warn_chars),
            "bloat_chars": int(args.bloat_chars),
            "bloat_blocks": int(args.bloat_blocks),
            "bloat_rate_pass": float(args.bloat_rate_pass),
            "bloat_rate_warn": float(args.bloat_rate_warn),
            "reaction_recent_pass_rate": float(args.reaction_recent_pass_rate),
            "reaction_recent_warn_rate": float(args.reaction_recent_warn_rate),
            "retrieval_recency_pass_rate": float(args.retrieval_recency_pass_rate),
            "retrieval_recency_warn_rate": float(args.retrieval_recency_warn_rate),
            "top_reason_pass_share": float(args.top_reason_pass_share),
            "top_reason_warn_share": float(args.top_reason_warn_share),
        },
    }

    livecap.write_json(summary_path, summary)
    livecap.write_json(output_run_dir / "manifest.json", replay_manifest)
    return rows, summary, trace_path, summary_path


def evaluate_trace_only(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any], Path]:
    trace_path = Path(str(args.trace_path)).expanduser().resolve()
    if not trace_path.is_file():
        raise FileNotFoundError(f"missing trace: {trace_path}")
    rows = load_jsonl(trace_path)
    if not rows:
        raise RuntimeError("trace is empty")
    summary = evaluate_rows(rows, args)
    summary["trace_path"] = str(trace_path)
    summary_path = default_summary_path(args, trace_path=trace_path, output_run_dir=None)
    livecap.write_json(summary_path, summary)
    return rows, summary, summary_path


def main() -> int:
    args = parse_args()
    if str(args.source_run_dir or "").strip():
        _rows, summary, trace_path, summary_path = run_replay(args)
        print(f"trace={trace_path}")
        print(f"summary={summary_path}")
    else:
        _rows, summary, summary_path = evaluate_trace_only(args)
        print(f"summary={summary_path}")
    print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
