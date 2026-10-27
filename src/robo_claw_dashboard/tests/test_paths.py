import pathlib

"""대시보드 순수 규칙 — 웹 리소스 경로 해석 테스트."""

import pytest
from robo_claw_dashboard.domain.paths import mime_type, safe_join_web_path


@pytest.fixture
def web_root(tmp_path):
    root = tmp_path / "web"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<html></html>", encoding="utf-8")
    (root / "assets" / "app.js").write_text("console.log(1);", encoding="utf-8")
    (root / "assets" / "style.css").write_text("body{}", encoding="utf-8")
    (root / "data.json").write_text("{}", encoding="utf-8")
    return str(root)


class TestSafeJoinWebPath:
    def test_root_serves_index(self, web_root):
        assert safe_join_web_path(web_root, "/") == f"{web_root}/index.html"

    def test_plain_file_resolves(self, web_root):
        assert safe_join_web_path(web_root, "/assets/app.js") == f"{web_root}/assets/app.js"

    def test_missing_file_returns_none(self, web_root):
        assert safe_join_web_path(web_root, "/nope.txt") is None

    def test_parent_traversal_rejected(self, web_root):
        assert safe_join_web_path(web_root, "/../secret") is None
        assert safe_join_web_path(web_root, "/assets/../secret") is None

    def test_null_byte_rejected(self, web_root):
        assert safe_join_web_path(web_root, "/x\x00y") is None

    def test_missing_root_returns_none(self, tmp_path):
        assert safe_join_web_path(str(tmp_path / "does_not_exist"), "/") is None

    def test_absolute_escape_rejected(self, web_root):
        assert safe_join_web_path(web_root, "//etc/passwd") is None


class TestMimeType:
    def test_known_extensions(self):
        assert "text/html" in mime_type("index.html")
        assert "javascript" in mime_type("app.js")
        assert mime_type("style.css") == "text/css; charset=utf-8"
        assert mime_type("data.json") == "application/json; charset=utf-8"

    def test_unknown_extension_defaults_to_octet_stream(self):
        assert mime_type("file.bin") == "application/octet-stream"

    def test_no_extension_defaults(self):
        assert mime_type("LICENSE") == "application/octet-stream"


#: 대시보드 웹 리소스 루트 (패키지에 함께 배포된다).
WEB_ROOT = pathlib.Path(__file__).resolve().parents[1] / "web"


def test_index_html_links_vendored_bootstrap_and_app_assets():
    """Bootstrap 은 CDN 이 아니라 패키지에 포함된 파일을 참조해야 오프라인 로봇에서도 열린다."""
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    assert "/assets/vendor/bootstrap.min.css" in html
    assert "/assets/style.css" in html
    assert "/assets/app.js" in html
    assert "cdn.jsdelivr.net" not in html
    assert (WEB_ROOT / "assets" / "vendor" / "bootstrap.min.css").is_file()
