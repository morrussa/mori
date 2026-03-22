from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import json
import random
import subprocess
import threading
from pathlib import Path
from typing import Any, Literal

from mori_tts.lux_tts import LuxTTS

TTSBackendName = Literal["lux", "zipvoice"]

DEFAULT_TTS_BACKEND: TTSBackendName = "zipvoice"

DEFAULT_ZIPVOICE_PYTHON_BIN = "/home/morusa/dataset/train/zipvoice-env/bin/python"
DEFAULT_ZIPVOICE_REPO = "/home/morusa/dataset/train/ZipVoice"
DEFAULT_ZIPVOICE_MODEL_DIR = "/home/morusa/AI/mori/model/zipvoice_zhja_iter3000_avg2"
DEFAULT_ZIPVOICE_CHECKPOINT_NAME = "iter-3000-avg-2.pt"
DEFAULT_ZIPVOICE_ZH_TOKENIZER = "emilia"
DEFAULT_ZIPVOICE_ZH_LANG = "en-us"
DEFAULT_ZIPVOICE_JA_TOKENIZER = "openjtalk"
DEFAULT_ZIPVOICE_JA_LANG = "ja"
DEFAULT_ZIPVOICE_ZH_PROMPT_TEXT = ""
DEFAULT_ZIPVOICE_ZH_PROMPT_WAV = ""
DEFAULT_ZIPVOICE_JA_PROMPT_TEXT = ""
DEFAULT_ZIPVOICE_JA_PROMPT_WAV = ""
DEFAULT_ZIPVOICE_REMOVE_LONG_SIL = False
DEFAULT_ZIPVOICE_NUM_THREAD = 1
DEFAULT_ZIPVOICE_LANG_DETECTOR = "auto"
DEFAULT_ZIPVOICE_LANG_MIN_CONF = 0.60
DEFAULT_ZIPVOICE_PROMPT_MANIFEST = ""
DEFAULT_ZIPVOICE_PROMPT_POLICY = "intent_hash"

_ZIPVOICE_LANG_DETECTOR_CHOICES = {"auto", "heuristic", "lingua"}
_ZIPVOICE_PROMPT_POLICY_CHOICES = {"intent_hash", "round_robin", "random"}
_PROMPT_MANIFEST_TEXT_KEYS = {"text", "prompt_text", "transcript"}
_PROMPT_MANIFEST_WAV_KEYS = {"wav_path", "path", "audio_path", "audio", "wav", "prompt_wav", "wav_or_mp3_path"}
_PROMPT_MANIFEST_ID_KEYS = {"id", "uniq_id", "prompt_id", "key", "name"}
_PROMPT_MANIFEST_ROUTE_KEYS = {"lang", "route", "language"}
_PROMPT_MANIFEST_ENABLED_KEYS = {"enabled", "enable", "active", "use", "keep"}


def _split_path_list(value: str) -> list[str]:
    s = str(value or "").strip()
    if not s:
        return []
    return [x.strip() for x in s.split(",") if x.strip()]


def _bool_text(value: str, default: bool = True) -> bool:
    s = str(value or "").strip().lower()
    if not s:
        return default
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _sniff_manifest_dialect(lines: list[str]) -> csv.Dialect:
    sample = "\n".join(lines[:8])
    try:
        return csv.Sniffer().sniff(sample, delimiters="\t,")
    except Exception:
        base = lines[0] if lines else ""
        delimiter = "\t" if "\t" in base else ","

        class _FallbackDialect(csv.excel):
            pass

        _FallbackDialect.delimiter = delimiter
        return _FallbackDialect


def _normalize_header_cell(value: str) -> str:
    return str(value or "").strip().lower().lstrip("\ufeff")


def _find_first_header(headers: dict[str, int], keys: set[str]) -> int:
    for key in keys:
        idx = headers.get(key)
        if idx is not None:
            return int(idx)
    return -1


def _has_prompt_manifest_header(row: list[str]) -> bool:
    headers = {_normalize_header_cell(cell) for cell in row}
    return bool(headers & _PROMPT_MANIFEST_TEXT_KEYS) and bool(headers & _PROMPT_MANIFEST_WAV_KEYS)


def _row_value(row: list[str], idx: int, default: str = "") -> str:
    if idx < 0 or idx >= len(row):
        return default
    return str(row[idx] or "").strip()


def _normalize_prompt_route(value: str) -> Literal["", "zh", "ja"]:
    lang = _normalize_lang_tag(value)
    if lang == "ja":
        return "ja"
    if lang in {"zh", "en"}:
        return "zh"
    return ""


@dataclass(frozen=True)
class _PromptEntry:
    prompt_id: str
    text: str
    wav_path: Path
    route: Literal["", "zh", "ja"]
    manifest_path: Path | None
    source: Literal["manifest", "config"]


def _resolve_prompt_audio_path(value: str | Path) -> Path:
    path = Path(str(value or "")).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    else:
        path = path.resolve()
    return path


def _build_fixed_prompt_entry(
    *,
    route: Literal["zh", "ja"],
    prompt_text: str,
    prompt_wav_path: str | Path,
) -> _PromptEntry | None:
    text = str(prompt_text or "").strip()
    wav_raw = str(prompt_wav_path or "").strip()
    if not text and not wav_raw:
        return None
    if not text or not wav_raw:
        raise ValueError(
            f"ZipVoice fixed {route} prompt requires both prompt_text and prompt_wav."
        )
    wav_path = _resolve_prompt_audio_path(wav_raw)
    if not wav_path.is_file():
        raise FileNotFoundError(f"ZipVoice fixed {route} prompt wav not found: {wav_path}")
    return _PromptEntry(
        prompt_id=f"fixed_{route}_{wav_path.stem}",
        text=text,
        wav_path=wav_path,
        route=route,
        manifest_path=None,
        source="config",
    )


def _read_prompt_entries_from_manifest(manifest: Path) -> list[_PromptEntry]:
    lines = [
        line
        for line in manifest.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        return []

    reader = csv.reader(lines, dialect=_sniff_manifest_dialect(lines))
    rows = [list(row) for row in reader if row]
    if not rows:
        return []

    items: list[_PromptEntry] = []
    header_map: dict[str, int] = {}
    start_idx = 0
    if _has_prompt_manifest_header(rows[0]):
        header_map = {_normalize_header_cell(cell): idx for idx, cell in enumerate(rows[0])}
        start_idx = 1

    for row in rows[start_idx:]:
        if not any(str(cell or "").strip() for cell in row):
            continue
        if header_map:
            prompt_id = _row_value(row, _find_first_header(header_map, _PROMPT_MANIFEST_ID_KEYS))
            prompt_text = _row_value(row, _find_first_header(header_map, _PROMPT_MANIFEST_TEXT_KEYS))
            prompt_path_raw = _row_value(row, _find_first_header(header_map, _PROMPT_MANIFEST_WAV_KEYS))
            prompt_route = _normalize_prompt_route(_row_value(row, _find_first_header(header_map, _PROMPT_MANIFEST_ROUTE_KEYS)))
            enabled = _bool_text(_row_value(row, _find_first_header(header_map, _PROMPT_MANIFEST_ENABLED_KEYS)), default=True)
        else:
            prompt_id = _row_value(row, 0)
            prompt_text = _row_value(row, 1)
            prompt_path_raw = _row_value(row, 2)
            prompt_route = _normalize_prompt_route(_row_value(row, 3))
            enabled = True

        if not enabled or not prompt_text or not prompt_path_raw:
            continue
        prompt_path = Path(prompt_path_raw).expanduser()
        if not prompt_path.is_absolute():
            prompt_path = (manifest.parent / prompt_path).resolve()
        else:
            prompt_path = prompt_path.resolve()
        if not prompt_path.is_file():
            continue
        items.append(
            _PromptEntry(
                prompt_id=prompt_id or prompt_path.stem,
                text=prompt_text,
                wav_path=prompt_path,
                route=prompt_route,
                manifest_path=manifest,
                source="manifest",
            )
        )
    return items


def _load_prompt_pool_from_manifests(manifest_paths: str | Path) -> list[_PromptEntry]:
    items: list[_PromptEntry] = []
    seen: set[tuple[str, str]] = set()
    for raw in _split_path_list(str(manifest_paths or "")):
        manifest = Path(raw).expanduser().resolve()
        if not manifest.is_file():
            raise FileNotFoundError(f"ZipVoice prompt manifest not found: {manifest}")
        for entry in _read_prompt_entries_from_manifest(manifest):
            k = (str(entry.wav_path), str(entry.route))
            if k in seen:
                continue
            seen.add(k)
            items.append(entry)
    return items


def _normalize_lang_tag(value: str) -> str:
    s = str(value or "").strip().lower().replace("_", "-")
    if not s:
        return ""
    if s == "jp" or s.startswith("ja"):
        return "ja"
    if s.startswith("zh") or s.startswith("cmn"):
        return "zh"
    if s.startswith("en"):
        return "en"
    return ""


def _detect_with_script_rules(text: str) -> str:
    s = str(text or "")
    if not s.strip():
        return "zh"
    kana_count = 0
    han_count = 0
    latin_count = 0
    for ch in s:
        if ("\u3040" <= ch <= "\u309f") or ("\u30a0" <= ch <= "\u30ff"):
            kana_count += 1
        elif "\u4e00" <= ch <= "\u9fff":
            han_count += 1
        elif ("a" <= ch <= "z") or ("A" <= ch <= "Z"):
            latin_count += 1

    if kana_count > 0:
        return "ja"
    if han_count > 0:
        return "zh"
    if latin_count > 0:
        return "en"
    return "zh"


_HIRA_ROMAJI_1 = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
    "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so",
    "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
    "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo",
    "や": "ya", "ゆ": "yu", "よ": "yo",
    "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro",
    "わ": "wa", "を": "o", "ん": "n",
    "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go",
    "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do",
    "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
    "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o",
}

_HIRA_ROMAJI_2 = {
    "きゃ": "kya", "きゅ": "kyu", "きょ": "kyo",
    "しゃ": "sha", "しゅ": "shu", "しょ": "sho",
    "ちゃ": "cha", "ちゅ": "chu", "ちょ": "cho",
    "にゃ": "nya", "にゅ": "nyu", "にょ": "nyo",
    "ひゃ": "hya", "ひゅ": "hyu", "ひょ": "hyo",
    "みゃ": "mya", "みゅ": "myu", "みょ": "myo",
    "りゃ": "rya", "りゅ": "ryu", "りょ": "ryo",
    "ぎゃ": "gya", "ぎゅ": "gyu", "ぎょ": "gyo",
    "じゃ": "ja", "じゅ": "ju", "じょ": "jo",
    "びゃ": "bya", "びゅ": "byu", "びょ": "byo",
    "ぴゃ": "pya", "ぴゅ": "pyu", "ぴょ": "pyo",
    "てぃ": "ti", "でぃ": "di", "とぅ": "tu", "どぅ": "du",
    "ふぁ": "fa", "ふぃ": "fi", "ふぇ": "fe", "ふぉ": "fo", "ふゅ": "fyu",
    "うぃ": "wi", "うぇ": "we", "うぉ": "wo",
}


def _kata_to_hira(ch: str) -> str:
    if len(ch) != 1:
        return ch
    code = ord(ch)
    if 0x30A1 <= code <= 0x30F6:
        return chr(code - 0x60)
    return ch


def _kana_to_romaji(text: str) -> str:
    s = "".join(_kata_to_hira(ch) for ch in str(text or ""))
    out: list[str] = []
    i = 0
    geminate = False

    while i < len(s):
        ch = s[i]
        if ch == "っ":
            geminate = True
            i += 1
            continue
        if ch == "ー":
            if out:
                prev = out[-1]
                for v in ("a", "i", "u", "e", "o"):
                    if prev.endswith(v):
                        out.append(v)
                        break
            i += 1
            continue

        pair = s[i : i + 2]
        if pair in _HIRA_ROMAJI_2:
            roma = _HIRA_ROMAJI_2[pair]
            i += 2
        else:
            roma = _HIRA_ROMAJI_1.get(ch, ch)
            i += 1

        if geminate and roma and roma[0].isalpha():
            roma = roma[0] + roma
            geminate = False
        out.append(roma)

    return "".join(out)


@dataclass(frozen=True)
class _ZipVoiceRoute:
    key: Literal["zh", "ja"]
    tokenizer: str
    lang: str


class _LinguaDetector:
    def __init__(self) -> None:
        self._detector: Any | None = None
        self._lang_map: dict[Any, str] = {}
        try:
            from lingua import Language, LanguageDetectorBuilder
        except Exception:
            return

        langs = [Language.CHINESE, Language.JAPANESE, Language.ENGLISH]
        self._detector = LanguageDetectorBuilder.from_languages(*langs).build()
        self._lang_map = {
            Language.CHINESE: "zh",
            Language.JAPANESE: "ja",
            Language.ENGLISH: "en",
        }

    @property
    def available(self) -> bool:
        return self._detector is not None

    def detect(self, text: str, *, min_conf: float) -> str:
        if self._detector is None:
            return ""
        s = str(text or "").strip()
        if not s:
            return ""
        try:
            scores = self._detector.compute_language_confidence_values(s)
        except Exception:
            return ""
        if not scores:
            return ""
        top = scores[0]
        top_lang = self._lang_map.get(getattr(top, "language", None), "")
        top_score = float(getattr(top, "value", 0.0) or 0.0)
        second_score = float(getattr(scores[1], "value", 0.0) or 0.0) if len(scores) > 1 else 0.0
        if not top_lang:
            return ""
        if top_score < float(min_conf):
            return ""
        # Reject unstable top1 scores and let rule fallback handle it.
        if (top_score - second_score) < 0.12:
            return ""
        return top_lang


class ZipVoiceLangDetector:
    def __init__(self, *, mode: str = DEFAULT_ZIPVOICE_LANG_DETECTOR, min_conf: float = DEFAULT_ZIPVOICE_LANG_MIN_CONF) -> None:
        m = str(mode or DEFAULT_ZIPVOICE_LANG_DETECTOR).strip().lower()
        if m not in _ZIPVOICE_LANG_DETECTOR_CHOICES:
            raise ValueError(
                f"Unsupported zipvoice lang detector: {mode!r}. Expected one of {sorted(_ZIPVOICE_LANG_DETECTOR_CHOICES)}."
            )
        self._mode = m
        self._min_conf = min(0.99, max(0.0, float(min_conf)))
        self._lingua = _LinguaDetector() if m in {"auto", "lingua"} else None

    def detect(self, text: str) -> str:
        # Hard rule first: kana strongly implies Japanese.
        by_rule = _detect_with_script_rules(text)
        if by_rule == "ja":
            return "ja"
        if self._mode == "heuristic":
            return by_rule

        lingua = self._lingua
        if lingua is not None:
            by_lingua = lingua.detect(text, min_conf=self._min_conf)
            if by_lingua:
                return by_lingua
            if self._mode == "lingua":
                return by_rule
        return by_rule


class ZipVoiceTTS:
    def __init__(
        self,
        *,
        python_bin: str | Path = DEFAULT_ZIPVOICE_PYTHON_BIN,
        zipvoice_repo: str | Path = DEFAULT_ZIPVOICE_REPO,
        model_dir: str | Path = DEFAULT_ZIPVOICE_MODEL_DIR,
        checkpoint_name: str = DEFAULT_ZIPVOICE_CHECKPOINT_NAME,
        zh_tokenizer: str = DEFAULT_ZIPVOICE_ZH_TOKENIZER,
        zh_lang: str = DEFAULT_ZIPVOICE_ZH_LANG,
        ja_tokenizer: str = DEFAULT_ZIPVOICE_JA_TOKENIZER,
        ja_lang: str = DEFAULT_ZIPVOICE_JA_LANG,
        zh_prompt_text: str = DEFAULT_ZIPVOICE_ZH_PROMPT_TEXT,
        zh_prompt_wav: str | Path = DEFAULT_ZIPVOICE_ZH_PROMPT_WAV,
        ja_prompt_text: str = DEFAULT_ZIPVOICE_JA_PROMPT_TEXT,
        ja_prompt_wav: str | Path = DEFAULT_ZIPVOICE_JA_PROMPT_WAV,
        remove_long_sil: bool = DEFAULT_ZIPVOICE_REMOVE_LONG_SIL,
        num_thread: int = DEFAULT_ZIPVOICE_NUM_THREAD,
        lang_detector: str = DEFAULT_ZIPVOICE_LANG_DETECTOR,
        lang_min_conf: float = DEFAULT_ZIPVOICE_LANG_MIN_CONF,
        prompt_manifest: str | Path = DEFAULT_ZIPVOICE_PROMPT_MANIFEST,
        prompt_policy: str = DEFAULT_ZIPVOICE_PROMPT_POLICY,
    ) -> None:
        self._python_bin = Path(python_bin).expanduser().resolve()
        self._zipvoice_repo = Path(zipvoice_repo).expanduser().resolve()
        self._model_dir = Path(model_dir).expanduser().resolve()
        self._checkpoint_name = str(checkpoint_name).strip() or DEFAULT_ZIPVOICE_CHECKPOINT_NAME
        self._zh_tokenizer = str(zh_tokenizer or DEFAULT_ZIPVOICE_ZH_TOKENIZER).strip()
        self._zh_lang = str(zh_lang or DEFAULT_ZIPVOICE_ZH_LANG).strip()
        self._ja_tokenizer = str(ja_tokenizer or DEFAULT_ZIPVOICE_JA_TOKENIZER).strip()
        self._ja_lang = str(ja_lang or DEFAULT_ZIPVOICE_JA_LANG).strip()
        self._fixed_route_prompts: dict[Literal["zh", "ja"], _PromptEntry | None] = {
            "zh": _build_fixed_prompt_entry(
                route="zh",
                prompt_text=zh_prompt_text,
                prompt_wav_path=zh_prompt_wav,
            ),
            "ja": _build_fixed_prompt_entry(
                route="ja",
                prompt_text=ja_prompt_text,
                prompt_wav_path=ja_prompt_wav,
            ),
        }
        self._remove_long_sil = bool(remove_long_sil)
        self._num_thread = max(1, int(num_thread))
        self._lang_detector = ZipVoiceLangDetector(mode=lang_detector, min_conf=lang_min_conf)
        self._prompt_manifest = str(prompt_manifest or "").strip()
        self._prompt_policy = str(prompt_policy or DEFAULT_ZIPVOICE_PROMPT_POLICY).strip().lower()
        if self._prompt_policy not in _ZIPVOICE_PROMPT_POLICY_CHOICES:
            raise ValueError(
                f"Unsupported zipvoice prompt policy: {prompt_policy!r}. Expected one of {sorted(_ZIPVOICE_PROMPT_POLICY_CHOICES)}."
            )
        self._prompt_pool = _load_prompt_pool_from_manifests(self._prompt_manifest)
        self._pool_lock = threading.Lock()
        self._pool_rr_idx = {"zh": 0, "ja": 0}
        self._worker_lock = threading.Lock()
        self._worker_req_id = 0
        self._worker_proc: subprocess.Popen[str] | None = None
        self._worker_script = (Path(__file__).resolve().parent / "zipvoice_worker.py").resolve()
        self._prompt_cache_stats: dict[Literal["zh", "ja"], dict[str, Any]] = {}

        if not self._python_bin.is_file():
            raise FileNotFoundError(f"ZipVoice python not found: {self._python_bin}")
        if not self._zipvoice_repo.is_dir():
            raise NotADirectoryError(f"ZipVoice repo not found: {self._zipvoice_repo}")
        if not self._model_dir.is_dir():
            raise NotADirectoryError(f"ZipVoice model_dir not found: {self._model_dir}")
        for fn in [self._checkpoint_name, "model.json", "tokens.txt"]:
            f = self._model_dir / fn
            if not f.is_file():
                raise FileNotFoundError(f"ZipVoice model file missing: {f}")
        if self._prompt_manifest and not self._prompt_pool:
            raise ValueError(f"ZipVoice prompt manifest has no usable entries: {self._prompt_manifest}")
        if not self._has_prompt_for_route("zh"):
            raise ValueError(
                "ZipVoice requires a zh fixed prompt or zh-capable entries in tts_zipvoice_prompt_manifest."
            )
        if not self._has_prompt_for_route("ja"):
            raise ValueError(
                "ZipVoice requires a ja fixed prompt or ja-capable entries in tts_zipvoice_prompt_manifest."
            )
        if not self._worker_script.is_file():
            raise FileNotFoundError(f"ZipVoice worker script not found: {self._worker_script}")

        self._start_worker()
        self._prewarm_route_prompt_cache()

    @property
    def backend_name(self) -> str:
        return "zipvoice"

    def _read_worker_message(self) -> dict[str, Any]:
        proc = self._worker_proc
        if proc is None or proc.stdout is None:
            raise RuntimeError("ZipVoice worker is not running")
        line = proc.stdout.readline()
        if not line:
            code = proc.poll()
            raise RuntimeError(f"ZipVoice worker exited unexpectedly (code={code})")
        msg = json.loads(line)
        if not isinstance(msg, dict):
            raise RuntimeError("ZipVoice worker returned non-object message")
        return msg

    def _start_worker(self) -> None:
        cmd = [
            str(self._python_bin),
            "-u",
            str(self._worker_script),
            "--zipvoice-repo",
            str(self._zipvoice_repo),
            "--model-dir",
            str(self._model_dir),
            "--checkpoint-name",
            self._checkpoint_name,
            "--num-thread",
            str(self._num_thread),
        ]
        proc = subprocess.Popen(
            cmd,
            cwd=str(self._zipvoice_repo),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            bufsize=1,
        )
        self._worker_proc = proc
        ready = self._read_worker_message()
        if not ready.get("ready", False):
            raise RuntimeError(f"ZipVoice worker failed to start: {ready}")

    def _write_worker_request(self, req: dict[str, Any]) -> dict[str, Any]:
        proc = self._worker_proc
        if proc is None or proc.stdin is None:
            self._start_worker()
            proc = self._worker_proc
        if proc is None or proc.stdin is None:
            raise RuntimeError("ZipVoice worker could not be started")

        self._worker_req_id += 1
        req_id = str(self._worker_req_id)
        msg = {"id": req_id, **req}

        try:
            proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except Exception as e:
            self._stop_worker()
            raise RuntimeError(f"ZipVoice worker write failed: {e}")

        while True:
            resp = self._read_worker_message()
            if str(resp.get("id", "")) != req_id:
                continue
            return resp

    def _stop_worker(self) -> None:
        proc = self._worker_proc
        self._worker_proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None and proc.stdin is not None:
                self._worker_req_id += 1
                rid = f"shutdown-{self._worker_req_id}"
                req = {"id": rid, "cmd": "shutdown"}
                proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
                proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.wait(timeout=1.5)
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=1.5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def __del__(self) -> None:
        try:
            self._stop_worker()
        except Exception:
            pass

    def _infer_with_worker(
        self,
        *,
        out_wav: Path,
        tokenizer: str,
        lang: str,
        prompt_wav: Path,
        prompt_text: str,
        text: str,
        num_steps: int,
        guidance_scale: float,
        t_shift: float,
        speed: float,
    ) -> None:
        req = {
            "tokenizer": tokenizer,
            "lang": lang,
            "prompt_wav": str(prompt_wav),
            "prompt_text": prompt_text,
            "text": text,
            "out_wav": str(out_wav),
            "num_step": max(1, int(num_steps)),
            "guidance_scale": float(guidance_scale),
            "t_shift": float(t_shift),
            "speed": float(speed),
            "target_rms": 0.1,
            "feat_scale": 0.1,
            "max_duration": 40.0,
            "remove_long_sil": bool(self._remove_long_sil),
        }
        msg = self._write_worker_request(req)
        if bool(msg.get("ok", False)):
            return
        err = str(msg.get("error", "unknown error"))
        raise RuntimeError(
            "ZipVoice inference failed.\n"
            f"tokenizer={tokenizer} lang={lang}\n"
            f"prompt_wav={prompt_wav}\n"
            f"out={out_wav}\n"
            f"{err}"
        )

    def _prewarm_prompt_entry(
        self,
        *,
        route_key: Literal["zh", "ja"],
        tokenizer: str,
        lang: str,
        prompt_wav: Path,
        prompt_text: str,
    ) -> None:
        msg = self._write_worker_request(
            {
                "cmd": "prewarm",
                "route": route_key,
                "tokenizer": tokenizer,
                "lang": lang,
                "prompt_wav": str(prompt_wav),
                "prompt_text": prompt_text,
            }
        )
        if not bool(msg.get("ok", False)):
            err = str(msg.get("error", "unknown error"))
            raise RuntimeError(
                "ZipVoice prompt prewarm failed.\n"
                f"route={route_key} tokenizer={tokenizer} lang={lang}\n"
                f"prompt_wav={prompt_wav}\n"
                f"{err}"
            )
        self._prompt_cache_stats[route_key] = {
            "prompt_duration": float(msg.get("prompt_duration", 0.0) or 0.0),
            "prompt_frames": int(msg.get("prompt_frames", 0) or 0),
            "prompt_feature_dim": int(msg.get("prompt_feature_dim", 0) or 0),
            "prompt_feature_bytes": int(msg.get("prompt_feature_bytes", 0) or 0),
            "prompt_tokens": int(msg.get("prompt_tokens", 0) or 0),
        }

    def _prewarmable_prompt_for_route(self, route_key: Literal["zh", "ja"]) -> _PromptEntry | None:
        fixed = self._fixed_route_prompts.get(route_key)
        if fixed is not None:
            return fixed
        pool = self._candidate_prompt_pool(self._prompt_pool, route_key)
        if len(pool) == 1:
            return pool[0]
        return None

    def _prewarm_route_prompt_cache(self) -> None:
        for route_key in ("zh", "ja"):
            entry = self._prewarmable_prompt_for_route(route_key)
            if entry is None:
                continue
            route = self._route_by_key(route_key)
            with self._worker_lock:
                self._prewarm_prompt_entry(
                    route_key=route_key,
                    tokenizer=route.tokenizer,
                    lang=route.lang,
                    prompt_wav=entry.wav_path,
                    prompt_text=entry.text,
                )

    def _route_by_key(self, key: str) -> _ZipVoiceRoute:
        if key == "ja":
            return _ZipVoiceRoute(
                key="ja",
                tokenizer=self._ja_tokenizer,
                lang=self._ja_lang,
            )
        return _ZipVoiceRoute(
            key="zh",
            tokenizer=self._zh_tokenizer,
            lang=self._zh_lang,
        )

    def _route(self, *, text: str, lang_hint: str = "") -> _ZipVoiceRoute:
        lang = _normalize_lang_tag(lang_hint)
        if lang == "ja":
            return self._route_by_key("ja")
        if lang in {"zh", "en"}:
            return self._route_by_key("zh")
        detected = self._lang_detector.detect(text)
        if detected == "ja":
            return self._route_by_key("ja")
        return self._route_by_key("zh")

    @staticmethod
    def _adapt_text_for_route(*, text: str, tokenizer: str, lang: str) -> str:
        s = str(text or "")
        zl = str(lang or "").strip().lower()
        zt = str(tokenizer or "").strip().lower()
        # zh route frequently explodes on mixed kana; transliterate kana to romaji first.
        if ("zh" in zl or zt in {"emilia", "pinyin", "jieba"}) and any(
            ("\u3040" <= ch <= "\u309f") or ("\u30a0" <= ch <= "\u30ff") for ch in s
        ):
            return _kana_to_romaji(s)
        return s

    @staticmethod
    def _candidate_prompt_pool(pool: list[_PromptEntry], route_key: Literal["zh", "ja"]) -> list[_PromptEntry]:
        if not pool:
            return []
        return [entry for entry in pool if entry.route in {"", route_key}]

    def _fixed_prompt_for_route(self, route_key: Literal["zh", "ja"]) -> _PromptEntry | None:
        return self._fixed_route_prompts.get(route_key)

    def _has_prompt_for_route(self, route_key: Literal["zh", "ja"]) -> bool:
        if self._fixed_prompt_for_route(route_key) is not None:
            return True
        return bool(self._candidate_prompt_pool(self._prompt_pool, route_key))

    def _choose_prompt_entry(
        self,
        *,
        pool: list[_PromptEntry],
        pool_name: Literal["zh", "ja"],
        prompt_key: str = "",
    ) -> _PromptEntry | None:
        if not pool:
            return None
        n = len(pool)
        pol = self._prompt_policy
        if pol == "random":
            return pool[random.randrange(n)]
        if pol == "round_robin":
            with self._pool_lock:
                idx = int(self._pool_rr_idx.get(pool_name, 0)) % n
                self._pool_rr_idx[pool_name] = int(self._pool_rr_idx.get(pool_name, 0)) + 1
            return pool[idx]

        # intent_hash: stable prompt per intent to avoid timbre jumping across chunks.
        key = str(prompt_key or "").strip()
        if not key:
            with self._pool_lock:
                idx = int(self._pool_rr_idx.get(pool_name, 0)) % n
                self._pool_rr_idx[pool_name] = int(self._pool_rr_idx.get(pool_name, 0)) + 1
            return pool[idx]
        h = hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(h, byteorder="big", signed=False) % n
        return pool[idx]

    def _choose_prompt_for_route(
        self,
        *,
        route_key: Literal["zh", "ja"],
        prompt_key: str = "",
    ) -> _PromptEntry | None:
        fixed = self._fixed_prompt_for_route(route_key)
        if fixed is not None:
            return fixed
        return self._choose_prompt_entry(
            pool=self._candidate_prompt_pool(self._prompt_pool, route_key),
            pool_name=route_key,
            prompt_key=prompt_key,
        )

    def _prompt_pool_name_for_route(self, route_key: Literal["zh", "ja"], prompt_entry: _PromptEntry) -> str:
        if prompt_entry.source == "config":
            return "fixed"
        if len(self._candidate_prompt_pool(self._prompt_pool, route_key)) == 1:
            return "singleton"
        return "shared"

    def synthesize_to_wav(
        self,
        *,
        text: str,
        out_wav_path: str | Path,
        prompt_wav_path: str | Path,
        prompt_duration: float = 0.0,
        prompt_rms: float = 0.0,
        num_steps: int = 8,
        guidance_scale: float = 1.0,
        t_shift: float = 0.5,
        speed: float = 1.0,
        return_smooth: bool = False,
        lang_hint: str = "",
        prompt_key: str = "",
    ) -> Path | dict[str, Any]:
        del prompt_wav_path, prompt_duration, prompt_rms, return_smooth
        out_wav = Path(out_wav_path).expanduser().resolve()
        out_wav.parent.mkdir(parents=True, exist_ok=True)

        route = self._route(text=str(text or ""), lang_hint=lang_hint)
        tokenizer = route.tokenizer
        lang = route.lang
        tts_text = self._adapt_text_for_route(text=str(text or ""), tokenizer=tokenizer, lang=lang)
        chosen = self._choose_prompt_for_route(route_key=route.key, prompt_key=prompt_key)
        if chosen is None:
            raise ValueError(
                f"ZipVoice prompt manifest is missing for route={route.key}. "
                "Add matching lang/route entries to tts_zipvoice_prompt_manifest."
            )
        prompt_entry = chosen
        prompt_wav = prompt_entry.wav_path
        prompt_text = prompt_entry.text
        if not prompt_wav.is_file():
            raise FileNotFoundError(f"ZipVoice prompt wav not found: {prompt_wav}")

        with self._worker_lock:
            self._infer_with_worker(
                out_wav=out_wav,
                tokenizer=tokenizer,
                lang=lang,
                prompt_wav=prompt_wav,
                prompt_text=prompt_text,
                text=tts_text,
                num_steps=max(1, int(num_steps)),
                guidance_scale=float(guidance_scale),
                t_shift=float(t_shift),
                speed=float(speed),
            )
        if not out_wav.is_file():
            raise FileNotFoundError(f"ZipVoice did not generate wav file: {out_wav}")
        return {
            "wav_path": str(out_wav),
            "tts_route": route.key,
            "tts_tokenizer": tokenizer,
            "tts_lang": lang,
            "prompt_id": prompt_entry.prompt_id,
            "prompt_route": prompt_entry.route or route.key,
            "prompt_wav_path": str(prompt_wav),
            "prompt_manifest_path": str(prompt_entry.manifest_path or ""),
            "prompt_pool_name": self._prompt_pool_name_for_route(route.key, prompt_entry),
        }


def build_tts_engine(
    *,
    backend: str,
    tts_model: str | Path,
    tts_device: str,
    tts_threads: int,
    tts_zipvoice_python_bin: str | Path,
    tts_zipvoice_repo: str | Path,
    tts_zipvoice_model_dir: str | Path,
    tts_zipvoice_checkpoint_name: str,
    tts_zipvoice_zh_tokenizer: str,
    tts_zipvoice_zh_lang: str,
    tts_zipvoice_ja_tokenizer: str,
    tts_zipvoice_ja_lang: str,
    tts_zipvoice_zh_prompt_text: str,
    tts_zipvoice_zh_prompt_wav: str | Path,
    tts_zipvoice_ja_prompt_text: str,
    tts_zipvoice_ja_prompt_wav: str | Path,
    tts_zipvoice_remove_long_sil: bool,
    tts_zipvoice_num_thread: int,
    tts_zipvoice_lang_detector: str,
    tts_zipvoice_lang_min_conf: float,
    tts_zipvoice_prompt_manifest: str | Path,
    tts_zipvoice_prompt_policy: str,
) -> Any:
    b = str(backend or DEFAULT_TTS_BACKEND).strip().lower()
    if b == "zipvoice":
        return ZipVoiceTTS(
            python_bin=tts_zipvoice_python_bin,
            zipvoice_repo=tts_zipvoice_repo,
            model_dir=tts_zipvoice_model_dir,
            checkpoint_name=tts_zipvoice_checkpoint_name,
            zh_tokenizer=tts_zipvoice_zh_tokenizer,
            zh_lang=tts_zipvoice_zh_lang,
            ja_tokenizer=tts_zipvoice_ja_tokenizer,
            ja_lang=tts_zipvoice_ja_lang,
            zh_prompt_text=tts_zipvoice_zh_prompt_text,
            zh_prompt_wav=tts_zipvoice_zh_prompt_wav,
            ja_prompt_text=tts_zipvoice_ja_prompt_text,
            ja_prompt_wav=tts_zipvoice_ja_prompt_wav,
            remove_long_sil=tts_zipvoice_remove_long_sil,
            num_thread=tts_zipvoice_num_thread,
            lang_detector=tts_zipvoice_lang_detector,
            lang_min_conf=tts_zipvoice_lang_min_conf,
            prompt_manifest=tts_zipvoice_prompt_manifest,
            prompt_policy=tts_zipvoice_prompt_policy,
        )

    if b != "lux":
        raise ValueError(f"Unsupported --tts-backend: {backend!r}. Expected lux or zipvoice.")
    return LuxTTS(model=tts_model, device=tts_device, threads=tts_threads)
