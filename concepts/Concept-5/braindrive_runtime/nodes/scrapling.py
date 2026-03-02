from __future__ import annotations

import ast
import asyncio
import inspect
import ipaddress
import re
import socket
import time
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional
from urllib import error, request
from urllib.parse import urlsplit, urlunsplit

from ..protocol import make_error, make_response
from .base import ProtocolNode, cap

_EXTRACTION_TYPES = {"markdown", "html", "text"}


def _env_bool(env: Mapping[str, str] | None, key: str, default: bool) -> bool:
    if env is None:
        return default
    raw = str(env.get(key, str(default))).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _env_int(env: Mapping[str, str] | None, key: str, default: int, minimum: int = 0) -> int:
    if env is None:
        return default
    try:
        value = int(str(env.get(key, str(default))).strip())
    except ValueError:
        value = default
    return max(minimum, value)


def _env_float(env: Mapping[str, str] | None, key: str, default: float, minimum: float = 0.0) -> float:
    if env is None:
        return default
    try:
        value = float(str(env.get(key, str(default))).strip())
    except ValueError:
        value = default
    return max(minimum, value)


def _env_csv(env: Mapping[str, str] | None, key: str) -> List[str]:
    if env is None:
        return []
    raw = str(env.get(key, "")).strip()
    if not raw:
        return []
    out = []
    for item in raw.split(","):
        token = item.strip().lower().strip(".")
        if token:
            out.append(token)
    return out


class _HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: List[str] = []

    def handle_data(self, data: str) -> None:
        cleaned = data.strip()
        if cleaned:
            self._parts.append(cleaned)

    def text(self) -> str:
        return " ".join(self._parts)


class ScraplingBackend:
    def __init__(self) -> None:
        self._mcp_server: Any | None = None
        self._load_mcp_server()

    def _load_mcp_server(self) -> None:
        try:
            from scrapling.core.ai import ScraplingMCPServer  # type: ignore
        except Exception:
            self._mcp_server = None
            return
        try:
            self._mcp_server = ScraplingMCPServer()
        except Exception:
            self._mcp_server = None

    @staticmethod
    def _decode_body(raw: bytes, content_type: str) -> str:
        charset = "utf-8"
        lowered = content_type.lower()
        if "charset=" in lowered:
            charset = lowered.split("charset=", 1)[1].split(";", 1)[0].strip() or "utf-8"
        try:
            return raw.decode(charset, errors="replace")
        except LookupError:
            return raw.decode("utf-8", errors="replace")

    @staticmethod
    def _extract_text(html: str) -> str:
        parser = _HtmlTextExtractor()
        parser.feed(html)
        parser.close()
        return unescape(parser.text())

    def _fallback_get(
        self,
        *,
        url: str,
        extraction_type: str,
        timeout_sec: float,
    ) -> Dict[str, Any]:
        req = request.Request(
            url=url,
            headers={
                "User-Agent": "BrainDrive-ScraplingNode/0.1",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            method="GET",
        )
        try:
            with request.urlopen(req, timeout=timeout_sec) as response:
                status = int(getattr(response, "status", 200))
                content_type = str(response.headers.get("Content-Type", "text/plain"))
                body = response.read()
        except error.HTTPError as exc:
            status = int(exc.code)
            content_type = str(exc.headers.get("Content-Type", "text/plain")) if exc.headers else "text/plain"
            body = exc.read()
        except error.URLError as exc:
            raise RuntimeError(str(exc.reason) if getattr(exc, "reason", None) else str(exc)) from exc

        html = self._decode_body(body, content_type)
        if extraction_type == "html":
            content = html
        else:
            # markdown fallback maps to plain text when parser extras are unavailable.
            content = self._extract_text(html)
        return {"status": status, "url": url, "content": [content]}

    def _call_mcp(self, method_name: str, kwargs: Dict[str, Any]) -> Any:
        if self._mcp_server is None:
            raise RuntimeError("Scrapling MCP server unavailable")

        method = getattr(self._mcp_server, method_name, None)
        if method is None:
            raise RuntimeError(f"Scrapling MCP method unavailable: {method_name}")

        try:
            result = method(**kwargs)
        except TypeError:
            signature = inspect.signature(method)
            allowed = set(signature.parameters.keys())
            filtered = {key: value for key, value in kwargs.items() if key in allowed}
            result = method(**filtered)

        if inspect.isawaitable(result):
            return asyncio.run(result)
        return result

    def get(
        self,
        *,
        url: str,
        extraction_type: str,
        css_selector: str,
        main_content_only: bool,
        timeout_sec: float,
    ) -> Any:
        kwargs = {
            "url": url,
            "extraction_type": extraction_type,
            "css_selector": css_selector,
            "main_content_only": main_content_only,
            "timeout": timeout_sec,
        }
        if self._mcp_server is not None:
            return self._call_mcp("get", kwargs)
        return self._fallback_get(url=url, extraction_type=extraction_type, timeout_sec=timeout_sec)

    def bulk_get(
        self,
        *,
        urls: List[str],
        extraction_type: str,
        css_selector: str,
        main_content_only: bool,
        timeout_sec: float,
    ) -> Any:
        kwargs = {
            "urls": urls,
            "extraction_type": extraction_type,
            "css_selector": css_selector,
            "main_content_only": main_content_only,
            "timeout": timeout_sec,
        }
        if self._mcp_server is not None:
            return self._call_mcp("bulk_get", kwargs)
        return [
            self._fallback_get(url=url, extraction_type=extraction_type, timeout_sec=timeout_sec)
            for url in urls
        ]

    def fetch(
        self,
        *,
        url: str,
        extraction_type: str,
        css_selector: str,
        main_content_only: bool,
        timeout_ms: int,
    ) -> Any:
        kwargs = {
            "url": url,
            "extraction_type": extraction_type,
            "css_selector": css_selector,
            "main_content_only": main_content_only,
            "timeout_ms": timeout_ms,
        }
        if self._mcp_server is not None:
            return self._call_mcp("fetch", kwargs)
        return self._fallback_get(url=url, extraction_type=extraction_type, timeout_sec=max(timeout_ms / 1000.0, 1.0))

    def bulk_fetch(
        self,
        *,
        urls: List[str],
        extraction_type: str,
        css_selector: str,
        main_content_only: bool,
        timeout_ms: int,
    ) -> Any:
        kwargs = {
            "urls": urls,
            "extraction_type": extraction_type,
            "css_selector": css_selector,
            "main_content_only": main_content_only,
            "timeout_ms": timeout_ms,
        }
        if self._mcp_server is not None:
            return self._call_mcp("bulk_fetch", kwargs)
        timeout_sec = max(timeout_ms / 1000.0, 1.0)
        return [
            self._fallback_get(url=url, extraction_type=extraction_type, timeout_sec=timeout_sec)
            for url in urls
        ]

    def stealthy_fetch(
        self,
        *,
        url: str,
        extraction_type: str,
        css_selector: str,
        main_content_only: bool,
        timeout_ms: int,
    ) -> Any:
        kwargs = {
            "url": url,
            "extraction_type": extraction_type,
            "css_selector": css_selector,
            "main_content_only": main_content_only,
            "timeout_ms": timeout_ms,
        }
        if self._mcp_server is not None:
            return self._call_mcp("stealthy_fetch", kwargs)
        return self._fallback_get(url=url, extraction_type=extraction_type, timeout_sec=max(timeout_ms / 1000.0, 1.0))

    def bulk_stealthy_fetch(
        self,
        *,
        urls: List[str],
        extraction_type: str,
        css_selector: str,
        main_content_only: bool,
        timeout_ms: int,
    ) -> Any:
        kwargs = {
            "urls": urls,
            "extraction_type": extraction_type,
            "css_selector": css_selector,
            "main_content_only": main_content_only,
            "timeout_ms": timeout_ms,
        }
        if self._mcp_server is not None:
            return self._call_mcp("bulk_stealthy_fetch", kwargs)
        timeout_sec = max(timeout_ms / 1000.0, 1.0)
        return [
            self._fallback_get(url=url, extraction_type=extraction_type, timeout_sec=timeout_sec)
            for url in urls
        ]


class ScraplingNode(ProtocolNode):
    node_id = "node.web.scrapling"
    node_version = "0.1.0"
    priority = 130

    def __init__(self, ctx, backend: Optional[ScraplingBackend] = None) -> None:
        super().__init__(ctx)
        env = ctx.env
        self.enabled = _env_bool(env, "BRAINDRIVE_SCRAPLING_ENABLED", True)
        self.allow_private_network = _env_bool(env, "BRAINDRIVE_SCRAPLING_ALLOW_PRIVATE_NET", False)
        self.allowed_domains = _env_csv(env, "BRAINDRIVE_SCRAPLING_ALLOWED_DOMAINS")
        self.blocked_domains = _env_csv(env, "BRAINDRIVE_SCRAPLING_BLOCKED_DOMAINS")
        self.max_urls = _env_int(env, "BRAINDRIVE_SCRAPLING_MAX_URLS", 10, minimum=1)
        self.max_content_chars = _env_int(env, "BRAINDRIVE_SCRAPLING_MAX_CONTENT_CHARS", 120000, minimum=100)
        self.timeout_sec = _env_float(env, "BRAINDRIVE_SCRAPLING_TIMEOUT_SEC", 30.0, minimum=0.1)
        self.browser_timeout_ms = _env_int(env, "BRAINDRIVE_SCRAPLING_BROWSER_TIMEOUT_MS", 30000, minimum=100)
        self.stealth_enabled = _env_bool(env, "BRAINDRIVE_SCRAPLING_STEALTH_ENABLED", True)
        self.default_save_to_library = _env_bool(env, "BRAINDRIVE_SCRAPLING_DEFAULT_SAVE", True)
        raw_default_dir = str((env or {}).get("BRAINDRIVE_SCRAPLING_DEFAULT_SAVE_DIR", "scraping")).strip()
        default_parts = self._clean_relative_parts(raw_default_dir)
        self.default_save_directory = "/".join(default_parts) if default_parts else "scraping"
        self.backend = backend or ScraplingBackend()

    def capabilities(self) -> List:
        return [
            cap(
                name="web.scrape.get",
                description="Fetch webpage content using Scrapling static get path",
                input_schema={"type": "object", "required": ["url"]},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["scrape https://example.com"],
                idempotency="idempotent",
                side_effect_scope="external",
            ),
            cap(
                name="web.scrape.bulk_get",
                description="Fetch webpage content for multiple URLs using Scrapling static get path",
                input_schema={"type": "object", "required": ["urls"]},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["scrape https://example.com and https://example.org"],
                idempotency="idempotent",
                side_effect_scope="external",
            ),
            cap(
                name="web.scrape.fetch",
                description="Fetch webpage content using browser-rendered dynamic mode",
                input_schema={"type": "object", "required": ["url"]},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["fetch dynamic content from https://example.com"],
                idempotency="idempotent",
                side_effect_scope="external",
            ),
            cap(
                name="web.scrape.bulk_fetch",
                description="Fetch multiple webpages using browser-rendered dynamic mode",
                input_schema={"type": "object", "required": ["urls"]},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["bulk fetch dynamic pages"],
                idempotency="idempotent",
                side_effect_scope="external",
            ),
            cap(
                name="web.scrape.stealth_fetch",
                description="Fetch webpage using stealth browser mode",
                input_schema={"type": "object", "required": ["url"]},
                risk_class="read",
                required_extensions=[],
                approval_required=True,
                examples=["use stealth scrape on https://example.com"],
                idempotency="idempotent",
                side_effect_scope="external",
            ),
            cap(
                name="web.scrape.bulk_stealth_fetch",
                description="Fetch multiple webpages using stealth browser mode",
                input_schema={"type": "object", "required": ["urls"]},
                risk_class="read",
                required_extensions=[],
                approval_required=True,
                examples=["bulk stealth scrape urls"],
                idempotency="idempotent",
                side_effect_scope="external",
            ),
        ]

    @staticmethod
    def _matches_domain(host: str, candidate: str) -> bool:
        normalized_host = host.lower().strip(".")
        normalized_candidate = candidate.lower().strip(".")
        return normalized_host == normalized_candidate or normalized_host.endswith("." + normalized_candidate)

    def _in_domain_policy(self, host: str, entries: Iterable[str]) -> bool:
        return any(self._matches_domain(host, item) for item in entries)

    def _resolve_host_ips(self, host: str) -> List[ipaddress._BaseAddress]:
        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise ValueError(f"unable to resolve host: {host}") from exc
        out = []
        seen = set()
        for item in infos:
            sockaddr = item[4]
            if not isinstance(sockaddr, tuple) or not sockaddr:
                continue
            raw_ip = str(sockaddr[0]).strip()
            try:
                ip = ipaddress.ip_address(raw_ip)
            except ValueError:
                continue
            key = str(ip)
            if key in seen:
                continue
            seen.add(key)
            out.append(ip)
        if not out:
            raise ValueError(f"unable to resolve host: {host}")
        return out

    @staticmethod
    def _is_public_ip(ip: ipaddress._BaseAddress) -> bool:
        return not (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )

    def _validate_url(self, raw: Any) -> str:
        url = str(raw).strip()
        if not url:
            raise ValueError("url is required")

        parsed = urlsplit(url)
        scheme = parsed.scheme.lower().strip()
        if scheme not in {"http", "https"}:
            raise ValueError("only http/https URLs are allowed")
        if not parsed.netloc:
            raise ValueError("url host is required")
        if parsed.username or parsed.password:
            raise ValueError("urls with embedded credentials are not allowed")

        host = str(parsed.hostname or "").strip().lower().strip(".")
        if not host:
            raise ValueError("url host is required")

        if self._in_domain_policy(host, self.blocked_domains):
            raise ValueError("url host blocked by policy")
        if self.allowed_domains and not self._in_domain_policy(host, self.allowed_domains):
            raise ValueError("url host not present in allowlist")

        if not self.allow_private_network:
            if host in {"localhost", "localhost.localdomain", "metadata.google.internal", "metadata"}:
                raise ValueError("private network targets are blocked by policy")
            try:
                host_ip = ipaddress.ip_address(host)
                host_ips = [host_ip]
            except ValueError:
                host_ips = self._resolve_host_ips(host)
            for ip in host_ips:
                if not self._is_public_ip(ip):
                    raise ValueError("private network targets are blocked by policy")
            if any(str(ip) == "169.254.169.254" for ip in host_ips):
                raise ValueError("metadata endpoint is blocked by policy")

        path = parsed.path or "/"
        return urlunsplit((scheme, parsed.netloc, path, parsed.query, ""))

    @staticmethod
    def _normalize_extraction_type(value: Any) -> str:
        extraction = str(value or "markdown").strip().lower()
        if extraction not in _EXTRACTION_TYPES:
            raise ValueError("extraction_type must be one of markdown|html|text")
        return extraction

    @staticmethod
    def _parse_scrapling_repr(raw_text: str) -> Dict[str, Any]:
        text = str(raw_text).strip()
        if not text or "status=" not in text or "content=" not in text:
            return {}

        out: Dict[str, Any] = {}
        status_match = re.search(r"\bstatus\s*=\s*(\d{3})\b", text)
        if status_match:
            try:
                out["status"] = int(status_match.group(1))
            except ValueError:
                pass

        url_match = re.search(r"\burl\s*=\s*(['\"])(.*?)\1", text, flags=re.DOTALL)
        if url_match:
            parsed_url = str(url_match.group(2)).strip()
            if parsed_url:
                out["url"] = parsed_url

        content_match = re.search(r"\bcontent\s*=\s*(\[[\s\S]*?\])\s+url\s*=", text, flags=re.DOTALL)
        if content_match is None:
            content_match = re.search(r"\bcontent\s*=\s*(\[[\s\S]*\])$", text, flags=re.DOTALL)
        if content_match:
            raw_list = content_match.group(1)
            try:
                decoded = ast.literal_eval(raw_list)
            except Exception:
                decoded = None
            if isinstance(decoded, list):
                out["content"] = [str(item) for item in decoded]
            elif decoded is not None:
                out["content"] = [str(decoded)]

        return out

    @staticmethod
    def _normalize_result_item(raw: Any, fallback_url: str) -> Dict[str, Any]:
        if isinstance(raw, dict):
            raw_status = raw.get("status", raw.get("status_code", 0))
            try:
                status = int(raw_status)
            except (ValueError, TypeError):
                status = 0
            url = str(raw.get("url", fallback_url)).strip() or fallback_url
            raw_content = raw.get("content", raw.get("text", raw.get("html", "")))
        elif any(hasattr(raw, key) for key in ("status", "status_code", "url", "content", "text", "html")):
            raw_status = getattr(raw, "status", getattr(raw, "status_code", 0))
            try:
                status = int(raw_status)
            except (ValueError, TypeError):
                status = 0
            raw_url = getattr(raw, "url", fallback_url)
            url = str(raw_url).strip() or fallback_url
            raw_content = getattr(raw, "content", getattr(raw, "text", getattr(raw, "html", "")))
        else:
            status = 0
            url = fallback_url
            raw_content = raw

        parse_candidate = ""
        if isinstance(raw_content, list) and len(raw_content) == 1 and isinstance(raw_content[0], str):
            parse_candidate = raw_content[0]
        elif isinstance(raw_content, str):
            parse_candidate = raw_content
        elif raw_content is not None:
            parse_candidate = str(raw_content)
        elif isinstance(raw, str):
            parse_candidate = raw

        if parse_candidate:
            parsed = ScraplingNode._parse_scrapling_repr(parse_candidate)
            if parsed:
                if status == 0 and isinstance(parsed.get("status"), int):
                    status = int(parsed["status"])
                if (url == fallback_url or not url) and isinstance(parsed.get("url"), str):
                    candidate_url = str(parsed["url"]).strip()
                    if candidate_url:
                        url = candidate_url
                if "content" in parsed:
                    raw_content = parsed["content"]

        if isinstance(raw_content, list):
            content = [str(item) for item in raw_content if str(item) != ""]
            if not content and raw_content:
                content = [str(raw_content[0])]
        elif raw_content is None:
            content = []
        else:
            content = [str(raw_content)]

        return {
            "status": status,
            "url": url,
            "content": content,
        }

    def _normalize_results(self, raw: Any, requested_urls: List[str]) -> List[Dict[str, Any]]:
        if isinstance(raw, dict) and isinstance(raw.get("results"), list):
            items = raw.get("results", [])
        elif isinstance(raw, list):
            items = raw
        else:
            items = [raw]

        results: List[Dict[str, Any]] = []
        for index, item in enumerate(items):
            fallback = requested_urls[min(index, len(requested_urls) - 1)]
            results.append(self._normalize_result_item(item, fallback))
        return results

    def _truncate_results(self, results: List[Dict[str, Any]], limit: int) -> tuple[List[Dict[str, Any]], bool]:
        remaining = max(0, int(limit))
        truncated = False
        out: List[Dict[str, Any]] = []

        for item in results:
            normalized = dict(item)
            chunks = item.get("content", [])
            if not isinstance(chunks, list):
                chunks = [str(chunks)]
            output_chunks: List[str] = []

            for chunk in chunks:
                value = str(chunk)
                if remaining <= 0:
                    if value:
                        truncated = True
                    continue
                if len(value) <= remaining:
                    output_chunks.append(value)
                    remaining -= len(value)
                    continue
                output_chunks.append(value[:remaining])
                remaining = 0
                truncated = True

            if len(output_chunks) < len(chunks):
                truncated = True

            normalized["content"] = output_chunks
            out.append(normalized)
        return out, truncated

    @staticmethod
    def _coerce_bool(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        lowered = str(value).strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        return default

    @staticmethod
    def _clean_relative_parts(raw_path: str) -> List[str]:
        token = str(raw_path).strip().replace("\\", "/")
        if not token:
            return []
        if token.startswith("/"):
            raise ValueError("save_directory must be relative to library root")
        out: List[str] = []
        for piece in token.split("/"):
            part = piece.strip()
            if not part or part == ".":
                continue
            if part == "..":
                raise ValueError("save_directory cannot contain '..'")
            out.append(part)
        return out

    def _resolve_save_directory(self, raw_value: Any) -> tuple[Path, str]:
        requested = str(raw_value).strip() if raw_value is not None else self.default_save_directory
        parts = self._clean_relative_parts(requested)
        if not parts:
            parts = self._clean_relative_parts(self.default_save_directory) or ["scraping"]

        directory = self.ctx.library_root.joinpath(*parts)
        try:
            directory.resolve().relative_to(self.ctx.library_root.resolve())
        except ValueError as exc:
            raise ValueError("save_directory must resolve inside library root") from exc
        return directory, "/".join(parts)

    @staticmethod
    def _slug(value: str, *, fallback: str = "page", max_len: int = 48) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9]+", "-", str(value).strip().lower()).strip("-")
        if not normalized:
            normalized = fallback
        return normalized[:max_len]

    @staticmethod
    def _extension_for_extraction(extraction_type: str) -> str:
        if extraction_type == "html":
            return "html"
        if extraction_type == "text":
            return "txt"
        return "md"

    def _build_result_filename(
        self,
        *,
        source_url: str,
        extraction_type: str,
        result_index: int,
        message_id: str,
    ) -> str:
        parsed = urlsplit(source_url)
        host_part = self._slug(parsed.hostname or "page", fallback="page")
        path_part = self._slug(parsed.path or "root", fallback="root")
        short_id = self._slug(message_id, fallback="msg", max_len=12)
        timestamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        ext = self._extension_for_extraction(extraction_type)
        return f"{timestamp}-{short_id}-{result_index + 1:02d}-{host_part}-{path_part}.{ext}"

    def _save_results_to_library(
        self,
        *,
        results: List[Dict[str, Any]],
        extraction_type: str,
        message_id: str,
        save_directory_raw: Any,
    ) -> Dict[str, Any]:
        directory, relative_directory = self._resolve_save_directory(save_directory_raw)
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(f"unable to create save_directory: {relative_directory}") from exc

        files: List[Dict[str, Any]] = []
        for index, item in enumerate(results):
            source_url = str(item.get("url", "")).strip() or f"result-{index + 1}"
            filename = self._build_result_filename(
                source_url=source_url,
                extraction_type=extraction_type,
                result_index=index,
                message_id=message_id,
            )
            destination = directory / filename
            raw_chunks = item.get("content", [])
            if isinstance(raw_chunks, list):
                text = "\n\n".join(str(chunk) for chunk in raw_chunks)
            elif raw_chunks is None:
                text = ""
            else:
                text = str(raw_chunks)

            try:
                destination.write_text(text, encoding="utf-8")
            except OSError as exc:
                raise RuntimeError(f"unable to write scrape file: {destination.name}") from exc

            relative_path = destination.relative_to(self.ctx.library_root).as_posix()
            files.append(
                {
                    "path": relative_path,
                    "url": source_url,
                    "bytes": len(text.encode("utf-8")),
                }
            )

        return {
            "saved": True,
            "directory": relative_directory,
            "files": files,
        }

    def _event_payload(
        self,
        *,
        intent: str,
        mode: str,
        urls: List[str],
        started: float,
        status_codes: List[int],
        truncated: bool,
    ) -> Dict[str, Any]:
        return {
            "intent": intent,
            "mode": mode,
            "url_count": len(urls),
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 2),
            "status_codes": status_codes,
            "truncated": bool(truncated),
            "policy_flags": {
                "allow_private_network": self.allow_private_network,
                "allowed_domains_configured": bool(self.allowed_domains),
                "blocked_domains_configured": bool(self.blocked_domains),
                "stealth_enabled": self.stealth_enabled,
            },
        }

    def handle(self, message: Dict[str, Any]) -> Dict[str, Any]:
        intent = str(message.get("intent", ""))
        payload = message.get("payload", {})
        if not isinstance(payload, dict):
            return make_error("E_BAD_MESSAGE", "payload must be object", message.get("message_id"))

        intent_map: Dict[str, tuple[str, bool]] = {
            "web.scrape.get": ("get", False),
            "web.scrape.bulk_get": ("bulk_get", True),
            "web.scrape.fetch": ("fetch", False),
            "web.scrape.bulk_fetch": ("bulk_fetch", True),
            "web.scrape.stealth_fetch": ("stealth_fetch", False),
            "web.scrape.bulk_stealth_fetch": ("bulk_stealth_fetch", True),
        }
        mode_info = intent_map.get(intent)
        if mode_info is None:
            return make_error("E_NO_ROUTE", "Unsupported intent", message.get("message_id"))

        if not self.enabled:
            return make_error("E_NODE_UNAVAILABLE", "Scrapling capability is disabled", message.get("message_id"))

        mode, is_bulk = mode_info
        if "stealth" in mode and not self.stealth_enabled:
            return make_error("E_NODE_UNAVAILABLE", "Stealth scraping is disabled", message.get("message_id"))

        forbidden_fields = {"page_action", "script", "javascript", "callback", "on_response"}
        for field in forbidden_fields:
            if field in payload:
                return make_error("E_BAD_MESSAGE", f"{field} is not allowed", message.get("message_id"))

        started = time.perf_counter()
        try:
            extraction_type = self._normalize_extraction_type(payload.get("extraction_type", "markdown"))
            css_selector = str(payload.get("css_selector", "")).strip()
            main_content_only = bool(payload.get("main_content_only", True))

            requested_limit = payload.get("max_content_chars", self.max_content_chars)
            try:
                requested_limit_int = int(requested_limit)
            except (ValueError, TypeError):
                raise ValueError("max_content_chars must be an integer") from None
            if requested_limit_int <= 0:
                raise ValueError("max_content_chars must be greater than zero")
            max_content_chars = min(requested_limit_int, self.max_content_chars)

            timeout_sec = float(payload.get("timeout", self.timeout_sec))
            if timeout_sec <= 0:
                raise ValueError("timeout must be greater than zero")
            timeout_ms = int(payload.get("timeout_ms", self.browser_timeout_ms))
            if timeout_ms <= 0:
                raise ValueError("timeout_ms must be greater than zero")

            if is_bulk:
                urls_raw = payload.get("urls", [])
                if not isinstance(urls_raw, list) or not urls_raw:
                    raise ValueError("urls must be a non-empty list")
                if len(urls_raw) > self.max_urls:
                    raise ValueError(f"bulk requests support at most {self.max_urls} urls")
                urls = [self._validate_url(item) for item in urls_raw]
            else:
                urls = [self._validate_url(payload.get("url", ""))]

            self.ctx.persistence.emit_event(
                "workflow",
                "scrapling.request.started",
                {
                    "intent": intent,
                    "mode": mode,
                    "url_count": len(urls),
                },
            )

            if mode == "get":
                raw = self.backend.get(
                    url=urls[0],
                    extraction_type=extraction_type,
                    css_selector=css_selector,
                    main_content_only=main_content_only,
                    timeout_sec=timeout_sec,
                )
            elif mode == "bulk_get":
                raw = self.backend.bulk_get(
                    urls=urls,
                    extraction_type=extraction_type,
                    css_selector=css_selector,
                    main_content_only=main_content_only,
                    timeout_sec=timeout_sec,
                )
            elif mode == "fetch":
                raw = self.backend.fetch(
                    url=urls[0],
                    extraction_type=extraction_type,
                    css_selector=css_selector,
                    main_content_only=main_content_only,
                    timeout_ms=timeout_ms,
                )
            elif mode == "bulk_fetch":
                raw = self.backend.bulk_fetch(
                    urls=urls,
                    extraction_type=extraction_type,
                    css_selector=css_selector,
                    main_content_only=main_content_only,
                    timeout_ms=timeout_ms,
                )
            elif mode == "stealth_fetch":
                raw = self.backend.stealthy_fetch(
                    url=urls[0],
                    extraction_type=extraction_type,
                    css_selector=css_selector,
                    main_content_only=main_content_only,
                    timeout_ms=timeout_ms,
                )
            else:
                raw = self.backend.bulk_stealthy_fetch(
                    urls=urls,
                    extraction_type=extraction_type,
                    css_selector=css_selector,
                    main_content_only=main_content_only,
                    timeout_ms=timeout_ms,
                )

            results = self._normalize_results(raw, urls)
            trimmed, truncated = self._truncate_results(results, max_content_chars)
            status_codes = [int(item.get("status", 0)) for item in trimmed]
            save_to_library = self._coerce_bool(payload.get("save_to_library"), self.default_save_to_library)
            storage: Dict[str, Any] = {"saved": False, "directory": "", "files": []}
            if save_to_library:
                storage = self._save_results_to_library(
                    results=trimmed,
                    extraction_type=extraction_type,
                    message_id=str(message.get("message_id", "")),
                    save_directory_raw=payload.get("save_directory", self.default_save_directory),
                )

            self.ctx.persistence.emit_event(
                "workflow",
                "scrapling.request.completed",
                self._event_payload(
                    intent=intent,
                    mode=mode,
                    urls=urls,
                    started=started,
                    status_codes=status_codes,
                    truncated=truncated,
                ),
            )

            return make_response(
                "web.scrape.completed",
                {
                    "mode": mode,
                    "results": trimmed,
                    "truncated": truncated,
                    "limits": {"max_content_chars": max_content_chars},
                    "storage": storage,
                },
                message.get("message_id"),
            )
        except TimeoutError:
            event_payload = self._event_payload(
                intent=intent,
                mode=mode,
                urls=[],
                started=started,
                status_codes=[],
                truncated=False,
            )
            self.ctx.persistence.emit_event("workflow", "scrapling.request.failed", event_payload)
            return make_error("E_NODE_TIMEOUT", "Scrapling request timed out", message.get("message_id"), retryable=True)
        except ValueError as exc:
            event_payload = self._event_payload(
                intent=intent,
                mode=mode,
                urls=[],
                started=started,
                status_codes=[],
                truncated=False,
            )
            event_payload["reason"] = str(exc)
            self.ctx.persistence.emit_event("workflow", "scrapling.request.blocked", event_payload)
            self.ctx.persistence.emit_event("workflow", "scrapling.policy.denied", event_payload)
            return make_error("E_BAD_MESSAGE", str(exc), message.get("message_id"))
        except Exception as exc:
            event_payload = self._event_payload(
                intent=intent,
                mode=mode,
                urls=[],
                started=started,
                status_codes=[],
                truncated=False,
            )
            event_payload["reason"] = str(exc)
            self.ctx.persistence.emit_event("workflow", "scrapling.request.failed", event_payload)
            return make_error(
                "E_NODE_ERROR",
                f"Scrapling execution failed: {type(exc).__name__}",
                message.get("message_id"),
                details={"error": str(exc)},
            )
