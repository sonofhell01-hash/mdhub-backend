import json
import mimetypes
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from http.cookiejar import Cookie, CookieJar
from typing import Any

from src.core.config import settings


@dataclass(frozen=True)
class MidiaSimplesLoginResult:
    authenticated: bool
    base_url: str
    final_url: str
    user_name: str | None = None


class MidiaSimplesSession:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or settings.midiasimples_base_url).rstrip("/")
        self.cookie_jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookie_jar))

    def request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 45,
    ) -> tuple[int, str, dict[str, str], str]:
        body = None
        req_headers = {
            "User-Agent": "MD-HUB-FINAL/2026",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
        }
        if headers:
            req_headers.update(headers)
        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            req_headers["Content-Type"] = "application/x-www-form-urlencoded"

        request = urllib.request.Request(self.base_url + path, data=body, headers=req_headers, method=method)
        try:
            response = self.opener.open(request, timeout=timeout)
            return response.status, response.geturl(), dict(response.headers), response.read().decode("utf-8-sig", errors="replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.geturl(), dict(exc.headers), exc.read().decode("utf-8-sig", errors="replace")

    def multipart_request(
        self,
        method: str,
        path: str,
        fields: dict[str, Any],
        files: list[dict[str, Any]],
        headers: dict[str, str] | None = None,
    ) -> tuple[int, str, dict[str, str], str]:
        body, content_type = encode_multipart(fields, files)
        req_headers = {
            "User-Agent": "MD-HUB-FINAL/2026",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
            "Content-Type": content_type,
        }
        if headers:
            req_headers.update(headers)
        request = urllib.request.Request(self.base_url + path, data=body, headers=req_headers, method=method)
        try:
            response = self.opener.open(request, timeout=90)
            return response.status, response.geturl(), dict(response.headers), response.read().decode("utf-8-sig", errors="replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.geturl(), dict(exc.headers), exc.read().decode("utf-8-sig", errors="replace")

    def login(self, email: str, password: str) -> MidiaSimplesLoginResult:
        status, _, _, html = self.request("GET", "/login")
        if status != 200:
            raise RuntimeError(f"Falha ao abrir /login: HTTP {status}")

        token = extract_csrf(html)
        if not token:
            raise RuntimeError("CSRF nao encontrado na tela de login.")

        status, final_url, _, body = self.request(
            "POST",
            "/login",
            data={"_token": token, "email": email, "password": password, "remember": "on"},
            headers={"Referer": f"{self.base_url}/login"},
        )
        if status >= 400 or final_url.endswith("/login") or "login-page" in body:
            raise RuntimeError("Login nao autenticou. Verifique usuario/senha.")

        return MidiaSimplesLoginResult(
            authenticated=True,
            base_url=self.base_url,
            final_url=final_url,
            user_name=extract_user_name(body),
        )

    def is_login_response(self, final_url: str, body: str) -> bool:
        url = (final_url or "").rstrip("/")
        body = body or ""
        return (
            url.endswith("/login")
            or 'action="https://api.arklok.midiasimples.com.br/login"' in body
            or "login-page" in body
            or ('name="password"' in body and 'name="email"' in body)
        )

    def validate_authenticated(self, path: str = "/colaboradores-tim", timeout: int = 45) -> tuple[bool, str]:
        status, final_url, _headers, body = self.request(
            "GET",
            path,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": f"{self.base_url}/",
            },
            timeout=timeout,
        )
        if status != 200:
            return False, f"HTTP {status} ao validar sessao em {path}."
        if self.is_login_response(final_url, body):
            return False, "MidiaSimples redirecionou para login ao validar a sessao."
        return True, final_url

    def datatable(
        self,
        path: str,
        *,
        search: str = "",
        start: int = 0,
        length: int = 10,
        order_by_id_desc: bool = False,
    ) -> dict[str, Any]:
        params = {
            "draw": "1",
            "start": str(start),
            "length": str(length),
            "search[value]": search,
            "search[regex]": "false",
        }
        if order_by_id_desc:
            params.update(
                {
                    "columns[0][data]": "id",
                    "columns[0][name]": "id",
                    "columns[0][searchable]": "true",
                    "columns[0][orderable]": "true",
                    "columns[0][search][value]": "",
                    "columns[0][search][regex]": "false",
                    "order[0][column]": "0",
                    "order[0][dir]": "desc",
                }
            )
        query = urllib.parse.urlencode(params)
        status, _, _, body = self.request(
            "GET",
            f"{path}?{query}",
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{self.base_url}{path}",
            },
        )
        if status != 200:
            raise RuntimeError(f"Falha ao consultar {path}: HTTP {status}")
        return json.loads(body)

    def get_devolucoes(self, *, search: str = "", start: int = 0, length: int = 10) -> dict[str, Any]:
        return self.datatable("/termo-de-devolucao", search=search, start=start, length=length)

    def get_concessoes(self, *, search: str = "", start: int = 0, length: int = 10) -> dict[str, Any]:
        return self.datatable("/colaboradores-tim", search=search, start=start, length=length)

    def get_emprestimos(self, *, search: str = "", start: int = 0, length: int = 10) -> dict[str, Any]:
        return self.datatable("/loan-term", search=search, start=start, length=length)

    def export_state(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "cookies": [
                {
                    "version": cookie.version,
                    "name": cookie.name,
                    "value": cookie.value,
                    "port": cookie.port,
                    "port_specified": cookie.port_specified,
                    "domain": cookie.domain,
                    "domain_specified": cookie.domain_specified,
                    "domain_initial_dot": cookie.domain_initial_dot,
                    "path": cookie.path,
                    "path_specified": cookie.path_specified,
                    "secure": cookie.secure,
                    "expires": cookie.expires,
                    "discard": cookie.discard,
                    "comment": cookie.comment,
                    "comment_url": cookie.comment_url,
                    "rest": dict(cookie._rest),
                    "rfc2109": cookie.rfc2109,
                }
                for cookie in self.cookie_jar
            ],
        }

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> "MidiaSimplesSession":
        session = cls(base_url=state.get("base_url"))
        for item in state.get("cookies") or []:
            try:
                session.cookie_jar.set_cookie(
                    Cookie(
                        version=int(item.get("version") or 0),
                        name=str(item.get("name") or ""),
                        value=str(item.get("value") or ""),
                        port=item.get("port"),
                        port_specified=bool(item.get("port_specified")),
                        domain=str(item.get("domain") or ""),
                        domain_specified=bool(item.get("domain_specified")),
                        domain_initial_dot=bool(item.get("domain_initial_dot")),
                        path=str(item.get("path") or "/"),
                        path_specified=bool(item.get("path_specified", True)),
                        secure=bool(item.get("secure")),
                        expires=item.get("expires"),
                        discard=bool(item.get("discard")),
                        comment=item.get("comment"),
                        comment_url=item.get("comment_url"),
                        rest=dict(item.get("rest") or {}),
                        rfc2109=bool(item.get("rfc2109")),
                    )
                )
            except Exception:
                continue
        return session


def extract_csrf(html: str) -> str | None:
    for pattern in (
        r'name="_token"\s+value="([^"]+)"',
        r"name=\"_token\"\s+value='([^']+)'",
        r'<meta\s+name="csrf-token"\s+content="([^"]+)"',
    ):
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def encode_multipart(fields: dict[str, Any], files: list[dict[str, Any]]) -> tuple[bytes, str]:
    boundary = f"----MDHubFinalBoundary{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        chunks.append(str(value or "").encode("utf-8"))
        chunks.append(b"\r\n")

    for item in files:
        field_name = str(item.get("field_name") or "file")
        filename = str(item.get("filename") or "arquivo.bin")
        content = item.get("content") or b""
        if isinstance(content, str):
            content = content.encode("utf-8")
        mime = str(item.get("content_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream")
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
            f"Content-Type: {mime}\r\n\r\n".encode("utf-8")
        )
        chunks.append(content)
        chunks.append(b"\r\n")

    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def extract_user_name(html: str) -> str | None:
    patterns = (
        r'class="[^"]*user-panel[^"]*"[\s\S]*?<span[^>]*>([^<]+)</span>',
        r'<a[^>]*class="[^"]*nav-link[^"]*"[^>]*>\s*([A-ZÀ-Úa-zà-ú ]{3,})\s*</a>',
    )
    for pattern in patterns:
        match = re.search(pattern, html)
        if match:
            return " ".join(match.group(1).split())
    return None


def normalize_datatable_response(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("data") or []
    return {
        "total": int(payload.get("recordsTotal") or 0),
        "filtered": int(payload.get("recordsFiltered") or 0),
        "count": len(rows),
        "data": rows,
    }
