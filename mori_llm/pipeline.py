from __future__ import annotations

import atexit
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .llama_cpp_cli import iter_lua_sequence, resolve_llama_cpp_bin_dir


class LlamaServerError(RuntimeError):
    pass


@dataclass(frozen=True)
class SpecConfig:
    enabled: bool = False
    draft_gpu_layers: int = 0
    draft_max: int = 0
    draft_min: int = 0
    draft_p_min: float = 0.0
    draft_ctx_size: int = 0

    @staticmethod
    def from_lua(value: object | None) -> "SpecConfig":
        if value is None:
            return SpecConfig()

        def _get(name: str, default: object) -> object:
            try:
                return value[name]  # type: ignore[index]
            except Exception:
                return default

        try:
            enabled = bool(_get("enabled", False))
            return SpecConfig(
                enabled=enabled,
                draft_gpu_layers=int(_get("draft_gpu_layers", 0) or 0),
                draft_max=int(_get("draft_max", 0) or 0),
                draft_min=int(_get("draft_min", 0) or 0),
                draft_p_min=float(_get("draft_p_min", 0.0) or 0.0),
                draft_ctx_size=int(_get("draft_ctx_size", 0) or 0),
            )
        except Exception:
            return SpecConfig()


class LlamaCppServerClient:
    def __init__(
        self,
        *,
        server_bin: Path,
        model_path: Path,
        ctx_size: int,
        host: str = "127.0.0.1",
        port: int | None = None,
        enable_webui: bool = False,
        enable_jinja: bool = True,
        api_key: str = "",
        embeddings: bool = False,
        gpu_layers: str = "all",
        draft_model_path: Path | None = None,
        spec_cfg: SpecConfig | None = None,
        startup_timeout_s: int = 600,
    ) -> None:
        self.server_bin = server_bin.resolve()
        self.model_path = model_path.resolve()
        self.ctx_size = int(ctx_size)
        self.embeddings = bool(embeddings)
        self.server_host = str(host or "127.0.0.1")
        self.request_host = "127.0.0.1" if self.server_host in ("0.0.0.0", "::") else self.server_host
        self.port = int(port) if port else self._find_free_port(self.request_host)
        self.base_url = f"http://{self.request_host}:{self.port}"
        self.enable_webui = bool(enable_webui) and not self.embeddings
        self.enable_jinja = bool(enable_jinja) and not self.embeddings
        self.api_key = str(api_key or "").strip()
        self.gpu_layers = str(gpu_layers or "all").strip()
        self.draft_model_path = draft_model_path.resolve() if draft_model_path else None
        self.spec_cfg = spec_cfg or SpecConfig()
        self.startup_timeout_s = int(startup_timeout_s)
        self.model_name = self.model_path.name or "local-model"
        self.process: subprocess.Popen[str] | None = None

        if not self.server_bin.is_file():
            raise FileNotFoundError(f"llama-server not found: {self.server_bin}")
        if not self.model_path.is_file():
            raise FileNotFoundError(f"model not found: {self.model_path}")

        self._start()

    @staticmethod
    def _find_free_port(host: str) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, 0))
            return int(sock.getsockname()[1])

    def _start(self) -> None:
        cmd: list[str] = [
            str(self.server_bin),
            "--model",
            str(self.model_path),
            "--host",
            self.server_host,
            "--port",
            str(self.port),
            "--ctx-size",
            str(self.ctx_size),
            "--gpu-layers",
            self.gpu_layers,
            "--no-webui",
        ]

        if self.enable_jinja and not self.embeddings:
            cmd.append("--jinja")

        if self.embeddings:
            cmd.append("--embeddings")
        else:
            cmd.extend(["--reasoning-format", "none"])

        if self.api_key:
            cmd.extend(["--api-key", self.api_key])

        if self.draft_model_path and self.spec_cfg.enabled and not self.embeddings:
            cmd.extend(["--model-draft", str(self.draft_model_path)])
            if self.spec_cfg.draft_ctx_size > 0:
                cmd.extend(["--ctx-size-draft", str(int(self.spec_cfg.draft_ctx_size))])
            if self.spec_cfg.draft_max > 0:
                cmd.extend(["--draft-max", str(int(self.spec_cfg.draft_max))])
            if self.spec_cfg.draft_min > 0:
                cmd.extend(["--draft-min", str(int(self.spec_cfg.draft_min))])
            if self.spec_cfg.draft_p_min > 0:
                cmd.extend(["--draft-p-min", str(float(self.spec_cfg.draft_p_min))])
            if self.spec_cfg.draft_gpu_layers >= 0:
                cmd.extend(["--gpu-layers-draft", str(int(self.spec_cfg.draft_gpu_layers))])

        env = os.environ.copy()
        lib_dir = str(self.server_bin.parent)
        old_ld_path = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = f"{lib_dir}:{old_ld_path}" if old_ld_path else lib_dir

        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            env=env,
            text=True,
        )
        self._wait_until_ready()

    def _wait_until_ready(self) -> None:
        deadline = time.time() + self.startup_timeout_s
        while time.time() < deadline:
            if self.process is not None and self.process.poll() is not None:
                raise LlamaServerError(
                    f"llama-server exited early (code={self.process.returncode}) for model: {self.model_path}"
                )
            try:
                status, _body = self._raw_http("GET", "/health", timeout_s=2)
                if status == 200:
                    return
            except Exception:
                pass
            time.sleep(0.25)
        raise TimeoutError(f"Timed out waiting llama-server on {self.base_url}")

    def _raw_http(self, method: str, endpoint: str, payload: object | None = None, timeout_s: int = 600) -> tuple[int, str]:
        url = f"{self.base_url}{endpoint}"
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return int(resp.status), body
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            return int(e.code), body

    def _request_json(self, method: str, endpoint: str, payload: object | None = None, timeout_s: int = 600) -> dict[str, Any]:
        status, body = self._raw_http(method, endpoint, payload=payload, timeout_s=timeout_s)
        if status >= 400:
            raise LlamaServerError(f"llama-server request failed ({status}) {endpoint}: {body[:4000]}")
        if not body:
            return {}
        try:
            obj = json.loads(body)
        except json.JSONDecodeError as e:
            raise LlamaServerError(f"Invalid JSON from llama-server {endpoint}: {e}; body={body[:1000]}") from e
        return obj if isinstance(obj, dict) else {}

    def create_chat_completion(
        self,
        *,
        messages: list[dict[str, str]],
        max_tokens: int = 256,
        temperature: float = 0.7,
        top_p: float | None = None,
        stop: list[str] | None = None,
        seed: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
        }
        if top_p is not None:
            payload["top_p"] = float(top_p)
        if stop:
            payload["stop"] = list(stop)
        if seed is not None:
            payload["seed"] = int(seed)
        return self._request_json("POST", "/v1/chat/completions", payload=payload, timeout_s=3600)

    def create_embedding(self, *, texts: list[str]) -> dict[str, Any]:
        payload = {"model": self.model_name, "input": texts, "encoding_format": "float"}
        return self._request_json("POST", "/v1/embeddings", payload=payload, timeout_s=600)

    def stop(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()


class MoriPipeline:
    def __init__(
        self,
        *,
        llama_bin_dir: str | os.PathLike[str] | None = None,
        large_ctx_size: int = 8192,
        embed_ctx_size: int = 8192,
        host: str = "127.0.0.1",
        large_port: int | None = None,
        embed_port: int | None = None,
        large_gpu_layers: str = "all",
        embed_gpu_layers: str = "0",
        enable_jinja: bool = True,
        api_key: str = "",
    ) -> None:
        self._bin_dir = resolve_llama_cpp_bin_dir(llama_bin_dir)
        self._server_bin = (self._bin_dir / "llama-server").resolve()

        self._host = host
        self._large_port = large_port
        self._embed_port = embed_port

        self._large_ctx_size = int(large_ctx_size)
        self._embed_ctx_size = int(embed_ctx_size)
        self._large_gpu_layers = str(large_gpu_layers)
        self._embed_gpu_layers = str(embed_gpu_layers)
        self._enable_jinja = bool(enable_jinja)
        self._api_key = str(api_key or "").strip()

        self._llm_large: LlamaCppServerClient | None = None
        self._llm_embed: LlamaCppServerClient | None = None

        atexit.register(self.shutdown)

        self.load_models = lambda _self, large_model_path, embedding_model_path, draft_model_path="", spec_cfg=None: (
            self.load_models_py(
                large_model_path=str(large_model_path),
                embedding_model_path=str(embedding_model_path),
                draft_model_path=str(draft_model_path or ""),
                spec_cfg=spec_cfg,
            )
        )
        self.generate_chat_sync = lambda _self, messages, params=None: self.generate_chat_sync_py(
            messages=messages, params=params
        )
        self.get_embedding = lambda _self, text, mode="query": self.get_embedding_py(text=text, mode=mode)
        self.get_embeddings = lambda _self, texts, mode="query": self.get_embeddings_py(texts=texts, mode=mode)

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

    @staticmethod
    def _extract_chat_text(output: dict[str, Any]) -> str:
        choices = output.get("choices") if isinstance(output, dict) else None
        if not choices or not isinstance(choices, list) or not isinstance(choices[0], dict):
            raise LlamaServerError(f"Invalid chat completion response: {output}")
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
            return "".join(parts)
        return str(content)

    def load_models_py(
        self,
        *,
        large_model_path: str,
        embedding_model_path: str,
        draft_model_path: str = "",
        spec_cfg: object | None = None,
    ) -> None:
        self.shutdown()

        spec = SpecConfig.from_lua(spec_cfg)
        draft = Path(draft_model_path).expanduser().resolve() if draft_model_path else None

        self._llm_large = LlamaCppServerClient(
            server_bin=self._server_bin,
            model_path=Path(large_model_path).expanduser(),
            ctx_size=self._large_ctx_size,
            host=self._host,
            port=self._large_port,
            enable_webui=False,
            enable_jinja=self._enable_jinja,
            api_key=self._api_key,
            embeddings=False,
            gpu_layers=self._large_gpu_layers,
            draft_model_path=draft,
            spec_cfg=spec,
        )
        self._llm_embed = LlamaCppServerClient(
            server_bin=self._server_bin,
            model_path=Path(embedding_model_path).expanduser(),
            ctx_size=self._embed_ctx_size,
            host=self._host,
            port=self._embed_port,
            enable_webui=False,
            enable_jinja=False,
            api_key=self._api_key,
            embeddings=True,
            gpu_layers=self._embed_gpu_layers,
        )

    def get_embedding_py(self, *, text: object, mode: object = "query") -> list[float]:
        vecs = self.get_embeddings_py(texts=[text], mode=mode)
        return vecs[0] if vecs else []

    def get_embeddings_py(self, *, texts: object, mode: object = "query") -> list[list[float]]:
        if self._llm_embed is None:
            raise LlamaServerError("Embedding model not loaded (call load_models first).")

        mode_str = str(mode or "query")
        prefixed: list[str] = []
        for item in iter_lua_sequence(texts):
            txt = self._prefix(str(item or ""), mode_str)
            if txt:
                prefixed.append(txt)
        if not prefixed:
            return []

        response = self._llm_embed.create_embedding(texts=prefixed)
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

    def generate_chat_sync_py(self, *, messages: object, params: object | None = None) -> str:
        if self._llm_large is None:
            raise LlamaServerError("Chat model not loaded (call load_models first).")

        messages_list: list[dict[str, str]] = []
        for msg in iter_lua_sequence(messages):
            try:
                role = str(msg.get("role") if isinstance(msg, dict) else msg["role"])  # type: ignore[index]
                content = str(msg.get("content") if isinstance(msg, dict) else msg["content"])  # type: ignore[index]
            except Exception:
                continue
            role = role.strip()
            if not role:
                continue
            messages_list.append({"role": role, "content": content})
        if not messages_list:
            raise ValueError("Invalid messages format.")

        p = params or {}
        try:
            max_tokens = int(p.get("max_tokens", 256)) if isinstance(p, dict) else int(p["max_tokens"])  # type: ignore[index]
        except Exception:
            max_tokens = 256
        try:
            temperature = (
                float(p.get("temperature", 0.7)) if isinstance(p, dict) else float(p["temperature"])  # type: ignore[index]
            )
        except Exception:
            temperature = 0.7
        try:
            top_p_raw = p.get("top_p") if isinstance(p, dict) else p["top_p"]  # type: ignore[index]
            top_p = float(top_p_raw) if top_p_raw is not None else None
        except Exception:
            top_p = None
        try:
            seed_raw = p.get("seed") if isinstance(p, dict) else p["seed"]  # type: ignore[index]
            seed = int(seed_raw) if seed_raw is not None else None
            if seed is not None and seed < 0:
                seed = None
        except Exception:
            seed = None
        try:
            stop_raw = p.get("stop", []) if isinstance(p, dict) else p["stop"]  # type: ignore[index]
            stop = [str(s) for s in iter_lua_sequence(stop_raw) if s is not None]
        except Exception:
            stop = []

        output = self._llm_large.create_chat_completion(
            messages=messages_list,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            stop=stop or None,
            seed=seed,
        )
        return self._extract_chat_text(output)

    def shutdown(self) -> None:
        if self._llm_large is not None:
            self._llm_large.stop()
            self._llm_large = None
        if self._llm_embed is not None:
            self._llm_embed.stop()
            self._llm_embed = None
