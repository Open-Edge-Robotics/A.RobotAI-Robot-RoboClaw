"""웹 정적 리소스 경로 해석 — path traversal 방지 순수 로직.

ROS2 / 네트워크 import 금지. HTTP 핸들러가 URL path 를 안전한 로컬 파일 경로로 변환한다.
"""

import os
import re

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


#: index.html 이 참조하는 로컬 리소스 경로를 뽑는 정규식(원격 URL 은 제외한다).
_LOCAL_REF_RE = re.compile(
    r"(?:src|href)\s*=\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)


def referenced_web_assets(web_root: str, index_name: str = "index.html") -> list[str]:
    """index.html 이 참조하는 로컬 리소스 요청 경로를 중복 없이 나열한다(정렬).

    설치가 오래되어 ``install/.../web/assets`` 에 새 JS 가 빠지면 브라우저는 404 를
    받고 해당 패널만 조용히 동작하지 않는다. 시작 시 이 목록을 대조해 경고한다.
    """
    index_path = os.path.join(web_root, index_name)
    if not os.path.isfile(index_path):
        return []
    try:
        with open(index_path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return []
    refs = set()
    for raw in _LOCAL_REF_RE.findall(text):
        ref = raw.strip()
        if not ref.startswith("/") or ref.startswith("//"):
            continue  # 원격/프로토콜 상대 URL 은 로컬 리소스가 아니다
        refs.add(ref.split("?", 1)[0].split("#", 1)[0])
    return sorted(refs)


def missing_web_assets(web_root: str, index_name: str = "index.html") -> list[str]:
    """index.html 이 참조하지만 web_root 아래에 실제로 없는 요청 경로를 돌려준다."""
    return [
        ref
        for ref in referenced_web_assets(web_root, index_name)
        if safe_join_web_path(web_root, ref) is None
    ]
