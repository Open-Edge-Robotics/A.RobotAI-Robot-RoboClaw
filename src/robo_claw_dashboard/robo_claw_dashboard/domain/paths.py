"""웹 정적 리소스 경로 해석 — path traversal 방지 순수 로직.

ROS2 / 네트워크 import 금지. HTTP 핸들러가 URL path 를 안전한 로컬 파일 경로로 변환한다.
"""

import os

_MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}

DEFAULT_MIME = "application/octet-stream"


def mime_type(path: str) -> str:
    """파일 경로 확장자에 대응하는 MIME 타입을 반환한다."""
    ext = os.path.splitext(path)[1].lower()
    return _MIME_TYPES.get(ext, DEFAULT_MIME)


def safe_join_web_path(web_root: str, request_path: str) -> str | None:
    """요청 URL path 를 web_root 내부의 로컬 파일 경로로 안전하게 변환한다.

    path traversal(``..``/절대 경로)과 null byte 가 포함되면 None 을 반환한다.
    ``/`` 는 ``{web_root}/index.html`` 로 해석한다.
    """
    if not os.path.isdir(web_root):
        return None
    if not isinstance(request_path, str):
        return None
    if "\x00" in request_path:
        return None
    if ".." in request_path.split("/"):
        return None

    rel = request_path.lstrip("/")
    if rel.endswith("/"):
        rel = rel.rstrip("/")
    candidate = (
        os.path.join(web_root, rel, "index.html") if not rel else os.path.join(web_root, rel)
    )
    resolved = os.path.normpath(candidate)
    root = os.path.normpath(web_root)

    if resolved != root and not resolved.startswith(root + os.sep):
        return None
    if not os.path.isfile(resolved):
        return None
    return resolved
