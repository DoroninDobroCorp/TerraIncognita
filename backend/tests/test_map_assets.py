"""Static checks for the map UI (map/app.html + css/ + js/ + PWA files). No browser needed.

What they protect: the file split stays consistent (nothing referenced is missing), the UI stays free of
inline handlers (so the strict script CSP keeps working), untrusted text cannot reach innerHTML, the
service worker's precache list matches app.html, and the colour themes keep their contrast.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import struct
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.middleware import DEFAULT_CSP

MAP_DIR = Path(__file__).resolve().parent.parent.parent / "map"
APP_HTML = MAP_DIR / "app.html"
LEGACY_PAGES = ("explorer.html", "index.html", "debug.html")

client = TestClient(app)


# ── helpers ─────────────────────────────────────────────────────────────────


class _Page(HTMLParser):
    """Collects tags, ids and inline script bodies of an HTML document."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.inline_scripts: list[str] = []
        self._in_inline_script = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.tags.append((tag, a))
        if tag == "script" and "src" not in a:
            self._in_inline_script = True
            self.inline_scripts.append("")

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_inline_script = False

    def handle_data(self, data):
        if self._in_inline_script:
            self.inline_scripts[-1] += data

    def find(self, tag: str) -> list[dict[str, str | None]]:
        return [a for t, a in self.tags if t == tag]

    @property
    def ids(self) -> list[str]:
        return [a["id"] for _, a in self.tags if a.get("id")]


def _parse(path: Path) -> _Page:
    page = _Page()
    page.feed(path.read_text(encoding="utf-8"))
    return page


@pytest.fixture(scope="module")
def app_page() -> _Page:
    return _parse(APP_HTML)


def _js_files() -> list[Path]:
    return sorted((MAP_DIR / "js").glob("*.js"))


def _js_text(exclude: tuple[str, ...] = ()) -> str:
    """All js/*.js joined, minus comment-only lines (headers document syntax like data-action="name")."""
    text = "\n".join(p.read_text(encoding="utf-8") for p in _js_files() if p.name not in exclude)
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//"))


def _local(ref: str) -> bool:
    return not re.match(r"^(https?:)?//", ref) and not ref.startswith(("data:", "#", "mailto:"))


def _local_refs(page: _Page) -> list[str]:
    refs = [a["src"] for a in page.find("script") if a.get("src")]
    refs += [a["href"] for a in page.find("link") if a.get("href")]
    return [r for r in refs if _local(r)]


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
    return struct.unpack(">II", data[16:24])


def _sw_text() -> str:
    return (MAP_DIR / "sw.js").read_text(encoding="utf-8")


def _sw_list(name: str) -> list[str]:
    block = re.search(rf"const {name} = \[(.*?)\n\];", _sw_text(), re.S)
    assert block, f"{name} not found in sw.js"
    return re.findall(r"'([^']+)'", block.group(1))


# ── app.html structure ──────────────────────────────────────────────────────


def test_app_html_shell(app_page: _Page):
    text = APP_HTML.read_text(encoding="utf-8")
    assert text.startswith("<!DOCTYPE html>")
    assert re.search(r'<html\s+lang="[a-z]{2}"', text)
    viewport = next(a["content"] for a in app_page.find("meta") if a.get("name") == "viewport")
    assert "width=device-width" in viewport
    assert "user-scalable=no" not in viewport and "maximum-scale" not in viewport, "pinch zoom must stay possible"
    assert any(a.get("rel") == "manifest" for a in app_page.find("link"))
    assert any(a.get("rel") == "apple-touch-icon" for a in app_page.find("link"))
    assert any(a.get("name") == "theme-color" for a in app_page.find("meta"))


def test_every_local_reference_exists(app_page: _Page):
    refs = _local_refs(app_page)
    assert "css/app.css" in refs and "js/main.js" in refs
    for ref in refs:
        assert (MAP_DIR / ref.split("?")[0].split("#")[0]).is_file(), f"app.html references missing file {ref}"


def test_every_asset_is_tracked_by_git():
    """The repo-wide .gitignore has *.png etc.; an ignored icon works locally and breaks the deployed PWA."""
    root = MAP_DIR.parent
    if not shutil.which("git") or not (root / ".git").exists():
        pytest.skip("not a git checkout")
    wanted = {r.split("?")[0] for r in _local_refs(_parse(APP_HTML))}
    wanted |= {p.name and f"js/{p.name}" for p in _js_files()}
    wanted |= set(_sw_list("SHELL_FILES"))
    manifest = json.loads((MAP_DIR / "manifest.webmanifest").read_text(encoding="utf-8"))
    wanted |= {i["src"] for i in manifest["icons"]} | {"manifest.webmanifest", "sw.js", "README.md"}
    for rel in sorted(wanted):
        done = subprocess.run(["git", "ls-files", "--error-unmatch", f"map/{rel}"], cwd=root, capture_output=True)
        assert done.returncode == 0, f"map/{rel} is not tracked by git (ignored by .gitignore?) — `git add -f` it"


def test_external_resources_carry_sri(app_page: _Page):
    external = [a for a in app_page.find("script") if a.get("src") and not _local(a["src"])]
    external += [a for a in app_page.find("link") if a.get("href") and not _local(a["href"])]
    assert external, "expected the pinned maplibre-gl files"
    for a in external:
        url = a.get("src") or a.get("href")
        assert str(a.get("integrity", "")).startswith("sha256-"), f"{url} lacks SRI"
        assert a.get("crossorigin") == "anonymous", f"{url} lacks crossorigin=anonymous"
        assert "@" in url and not url.endswith("@latest"), f"{url} is not version-pinned"


def test_script_order_is_dependency_order(app_page: _Page):
    srcs = [a["src"] for a in app_page.find("script") if a.get("src") and _local(a["src"])]
    names = [s.rsplit("/", 1)[-1] for s in srcs]
    assert names[0] == "theme-init.js", "theme-init.js must run in <head> before the stylesheet paints"
    body = names[1:]
    for earlier, later in [("config.js", "util.js"), ("util.js", "actions.js"), ("actions.js", "ui.js"),
                           ("ui.js", "api.js"), ("api.js", "map.js"), ("map.js", "places.js")]:
        assert body.index(earlier) < body.index(later), f"{earlier} must load before {later}"
    assert body[-1] == "main.js", "main.js boots the app and must load last"
    for p in _js_files():
        assert p.name in names, f"js/{p.name} exists but app.html does not load it"


def test_ids_are_unique_and_aria_targets_exist(app_page: _Page):
    ids = app_page.ids
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate ids: {dupes}"
    known = set(ids)
    for _, a in app_page.tags:
        for attr in ("aria-controls", "aria-labelledby"):
            for target in (a.get(attr) or "").split():
                assert target in known, f"{attr}={target} points at nothing"
        if a.get("for"):
            assert a["for"] in known, f"label for={a['for']} points at nothing"


def test_tabs_have_panels(app_page: _Page):
    ids = set(app_page.ids)
    tabs = [a for a in app_page.find("button") if a.get("role") == "tab"]
    assert len(tabs) >= 15
    for t in tabs:
        assert f"{t['data-group']}tab-{t['data-tab']}" in ids, f"tab {t['id']} has no panel"
        assert t.get("aria-controls") == f"{t['data-group']}tab-{t['data-tab']}"


# ── no inline code, no raw HTML sinks ───────────────────────────────────────


def test_no_inline_scripts_or_handlers_in_app_html(app_page: _Page):
    assert app_page.inline_scripts == [], "app.html must not contain inline <script> blocks"
    for tag, a in app_page.tags:
        for name, value in a.items():
            assert not name.startswith("on"), f"<{tag}> has inline handler attribute {name}"
            assert not str(value or "").lower().lstrip().startswith("javascript:"), f"<{tag} {name}> uses a javascript: URL"


def test_no_inline_handlers_in_js_templates():
    pattern = re.compile(r"""\bon[a-z]+\s*=\s*["'\\]""")
    for p in _js_files():
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            assert not pattern.search(line), f"js/{p.name}:{n} builds an inline event handler: {line.strip()[:80]}"


def test_html_sinks_are_confined_to_util_js():
    """Untrusted text may only reach the DOM through html``/escHtml/setHtml (all defined in util.js)."""
    banned = [r"\.innerHTML\s*[+]?=", r"\.outerHTML\s*=", r"insertAdjacentHTML\s*\(", r"document\.write\s*\(",
              r"\beval\s*\(", r"new\s+Function\s*\("]
    for p in _js_files():
        if p.name == "util.js":
            continue
        text = p.read_text(encoding="utf-8")
        for pat in banned:
            assert not re.search(pat, text), f"js/{p.name} uses {pat} — go through setHtml()/appendHtml() in util.js"
        # setHtml/appendHtml must receive html``/raw()/a SafeHtml variable, never a bare or untagged string
        for m in re.finditer(r"(?:setHtml|appendHtml)\([^()]*?,\s*([`'\"])", text):
            raise AssertionError(f"js/{p.name}: setHtml/appendHtml called with an untagged string near {m.group(0)[-40:]!r}")
        for m in re.finditer(r"\.setHTML\(([^)]*)", text):  # maplibre popups: only our escaped popupHtml()
            assert m.group(1).startswith("popupHtml("), f"js/{p.name}: Popup.setHTML must be fed by popupHtml()"


def test_safe_html_helpers_exist():
    util = (MAP_DIR / "js" / "util.js").read_text(encoding="utf-8")
    for needle in ("function escHtml", "function html(", "function setHtml", "function safeUrl", "function safeColor"):
        assert needle in util


# ── action registry ─────────────────────────────────────────────────────────


def _registered(kind: str) -> set[str]:
    """Names passed to register<Kind>({...}) — line based: multi-line calls end with '});' at column 0."""
    names: set[str] = set()
    lines = _js_text().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.startswith(f"register{kind}({{"):
            continue
        if line.rstrip().endswith("});"):  # one-liner: registerEnter({ search: (el) => ... });
            m = re.match(r"register\w+\(\{\s*(?:'([\w-]+)'|([A-Za-z_]\w*))\s*:", line)
            names.add(m.group(1) or m.group(2))
            continue
        while i < len(lines) and not lines[i].startswith("});"):
            m = re.match(r"^ {2}(?:'([\w-]+)'|([A-Za-z_]\w*))\s*:", lines[i])
            if m:
                names.add(m.group(1) or m.group(2))
            i += 1
    return names


def test_every_used_action_is_registered():
    html_text = APP_HTML.read_text(encoding="utf-8")
    js = _js_text()
    both = html_text + "\n" + js
    actions = set(re.findall(r'data-action="([\w-]+)"', both))
    actions |= set(re.findall(r"errorStateHtml\([^,\n]+,[^,\n]+,\s*'([\w-]+)'", js))          # retry buttons
    actions |= set(re.findall(r"action:\s*'([\w-]+)'", js))                                   # toast action buttons
    actions |= set(re.findall(r"switchHtml\('([\w-]+)'", js))                                 # settings switches
    for kind, attr in (("Change", "data-change"), ("Input", "data-input"), ("Enter", "data-enter")):
        used = set(re.findall(rf'{attr}="([\w-]+)"', both))
        missing = used - _registered(kind)
        assert not missing, f"{attr} handlers not registered: {sorted(missing)}"
    missing = actions - _registered("Actions")
    assert not missing, f"data-action names without a registered handler: {sorted(missing)}"


def test_registered_actions_are_used():
    """Dead registry entries rot; every handler must be reachable from markup."""
    both = APP_HTML.read_text(encoding="utf-8") + "\n" + _js_text()
    used = set(re.findall(r'data-action="([\w-]+)"', both))
    used |= set(re.findall(r"errorStateHtml\([^,\n]+,[^,\n]+,\s*'([\w-]+)'", both))
    used |= set(re.findall(r"action:\s*'([\w-]+)'", both))
    used |= set(re.findall(r"switchHtml\('([\w-]+)'", both))                       # settings switches
    unused = _registered("Actions") - used
    assert not unused, f"registered but never referenced: {sorted(unused)}"


def test_tab_hooks_target_real_tabs(app_page: _Page):
    tabs = {f"{t['data-group']}:{t['data-tab']}" for t in app_page.find("button") if t.get("role") == "tab"}
    for hook in re.findall(r"TI\.tabHooks\['([^']+)'\]", _js_text()):
        assert hook in tabs, f"tab hook {hook} has no tab in app.html"


# ── PWA: manifest + service worker ──────────────────────────────────────────


def test_manifest_is_valid():
    m = json.loads((MAP_DIR / "manifest.webmanifest").read_text(encoding="utf-8"))
    for key in ("name", "short_name", "start_url", "scope", "display", "theme_color", "background_color", "icons"):
        assert m.get(key), f"manifest lacks {key}"
    assert m["start_url"] == "/map/app.html" and m["scope"] == "/map/"
    assert m["start_url"].startswith(m["scope"])
    assert m["display"] in ("standalone", "fullscreen", "minimal-ui")
    assert re.fullmatch(r"#[0-9a-fA-F]{6}", m["theme_color"]) and re.fullmatch(r"#[0-9a-fA-F]{6}", m["background_color"])
    purposes = {(i.get("purpose", "any"), i["sizes"]) for i in m["icons"]}
    for need in (("any", "192x192"), ("any", "512x512"), ("maskable", "192x192"), ("maskable", "512x512")):
        assert need in purposes, f"manifest lacks icon {need}"
    for icon in m["icons"]:
        path = MAP_DIR / icon["src"]
        assert path.is_file(), f"manifest icon {icon['src']} is missing"
        if icon["type"] == "image/png":
            w, h = icon["sizes"].split("x")
            assert _png_size(path) == (int(w), int(h)), f"{icon['src']} is not {icon['sizes']}"


def test_apple_touch_icon_is_180px(app_page: _Page):
    href = next(a["href"] for a in app_page.find("link") if a.get("rel") == "apple-touch-icon")
    assert _png_size(MAP_DIR / href) == (180, 180)


def test_service_worker_precache_matches_app_html(app_page: _Page):
    assert (MAP_DIR / "sw.js").is_file()
    shell = _sw_list("SHELL_FILES")
    for f in shell:
        assert (MAP_DIR / f).is_file(), f"sw.js precaches missing file {f}"
    expected = {r.split("?")[0] for r in _local_refs(app_page) if not r.startswith("favicon")}
    expected |= {"app.html", "favicon.svg", "manifest.webmanifest"}
    missing = {e for e in expected if e not in shell and e != "favicon.svg"}
    assert not missing, f"app.html needs these but sw.js does not precache them: {sorted(missing)}"
    assert "favicon.svg" in shell
    assert len(shell) == len(set(shell))


def test_service_worker_cdn_files_match_app_html_sri(app_page: _Page):
    cdn = re.findall(r"\{ url: '([^']+)', integrity: '([^']+)' \}", _sw_text())
    in_html = {(a.get("src") or a.get("href"), a["integrity"]) for a in app_page.find("script") + app_page.find("link")
               if a.get("integrity") and not _local(a.get("src") or a.get("href"))}
    assert set(cdn) == in_html, "sw.js CDN_FILES must mirror the SRI-pinned tags in app.html"


def test_service_worker_cache_policy():
    sw = _sw_text()
    assert re.search(r"const SHELL_VERSION = 'v\d+';", sw), "bump SHELL_VERSION when shell files change"
    assert re.search(r"TILE_CACHE_MAX = (\d+)", sw) and 100 <= int(re.search(r"TILE_CACHE_MAX = (\d+)", sw).group(1)) <= 2000
    assert "request.method !== 'GET'" in sw.replace("req.method", "request.method"), "writes must never be intercepted"
    cacheable = re.search(r"const API_CACHEABLE = \[(.*?)\];", sw, re.S).group(1)
    for private in ("chat", "visits", "trips", "stats", "explorer", "achievements", "community", "offline", "fog", "collections", "streak", "health"):
        assert private not in cacheable, f"{private} must not be cached by the service worker"
    assert "keys.filter" in sw or "names.filter" in sw  # old caches are cleaned on activate
    assert "skipWaiting" in sw and "clients.claim" in sw


def test_pwa_files_are_served():
    page = client.get("/map/app.html")
    assert page.status_code == 200
    script_src = next(d for d in page.headers["content-security-policy"].split(";") if d.strip().startswith("script-src"))
    assert "'unsafe-inline'" not in script_src
    manifest = client.get("/map/manifest.webmanifest")
    assert manifest.status_code == 200 and "json" in manifest.headers["content-type"]
    sw = client.get("/map/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"]
    for ref in _local_refs(_parse(APP_HTML)):
        assert client.get(f"/map/{ref}").status_code == 200, ref
    assert client.get("/", follow_redirects=False).headers["location"] == "/map/app.html"


# ── CSP ─────────────────────────────────────────────────────────────────────


def _csp_directive(name: str) -> str:
    return next(d.strip() for d in DEFAULT_CSP.split(";") if d.strip().startswith(name + " "))


def test_script_src_has_no_unsafe_inline_and_allows_legacy_inline_blocks_by_hash():
    script_src = _csp_directive("script-src")
    assert "'unsafe-inline'" not in script_src and "'unsafe-eval'" not in script_src
    for name in LEGACY_PAGES:
        for body in _parse(MAP_DIR / name).inline_scripts:
            digest = base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode()
            assert f"'sha256-{digest}'" in script_src, (
                f"{name} has an inline <script> whose hash is not in DEFAULT_CSP (middleware.py): 'sha256-{digest}'"
            )


def test_connect_src_covers_the_service_worker_and_tiles():
    connect = _csp_directive("connect-src")
    for host in ("https://unpkg.com", "https://tile.openstreetmap.org", "https://router.project-osrm.org"):
        assert host in connect
    assert "worker-src 'self'" in DEFAULT_CSP and "manifest-src 'self'" in DEFAULT_CSP


def test_legacy_pages_have_favicon():
    for name in LEGACY_PAGES:
        assert any(a.get("rel") == "icon" for a in _parse(MAP_DIR / name).find("link")), name


# ── themes ──────────────────────────────────────────────────────────────────


def _theme_tokens(selector_re: str) -> dict[str, str]:
    css = (MAP_DIR / "css" / "app.css").read_text(encoding="utf-8")
    block = re.search(rf"{selector_re}\s*\{{(.*?)\n\}}", css, re.S)
    assert block, f"theme block {selector_re} not found"
    return dict(re.findall(r"--([\w-]+):\s*([^;]+);", block.group(1)))


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


THEMES = {
    "dark": r":root",
    "light": r':root\[data-theme="light"\]',
    "sun": r':root\[data-theme="sun"\]',
}
SURFACES = ("bg-primary", "bg-secondary", "bg-card", "bg-input")


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_theme_contrast(theme: str):
    t = _theme_tokens(THEMES[theme])
    text_min = 7.0 if theme == "sun" else 4.5   # sun = built for direct sunlight
    for fg in ("text-primary", "text-secondary", "text-muted"):
        for bg in SURFACES:
            assert _contrast(t[fg], t[bg]) >= text_min, f"{theme}: {fg} on {bg} is {_contrast(t[fg], t[bg]):.1f}:1"
    for bg in ("bg-primary", "bg-secondary", "bg-card"):
        assert _contrast(t["accent"], t[bg]) >= 4.5, f"{theme}: accent text on {bg}"
    for fill in ("accent", "success", "warning", "danger", "info"):
        on = "on-accent" if fill == "accent" else f"on-{fill}"
        assert _contrast(t[on], t[fill]) >= text_min, f"{theme}: {on} on {fill} is {_contrast(t[on], t[fill]):.1f}:1"
    for bg in ("bg-primary", "bg-card"):
        assert _contrast(t["focus"], t[bg]) >= 3.0, f"{theme}: focus ring on {bg}"


def test_themes_define_the_same_colour_tokens():
    dark = {k for k, v in _theme_tokens(THEMES["dark"]).items() if re.match(r"#|rgba?\(", v.strip())}
    for theme in ("light", "sun"):
        tokens = set(_theme_tokens(THEMES[theme]))
        assert dark - tokens == set(), f"{theme} theme lacks tokens: {sorted(dark - tokens)}"


def test_theme_init_and_settings_know_the_same_themes():
    init = (MAP_DIR / "js" / "theme-init.js").read_text(encoding="utf-8")
    settings = (MAP_DIR / "js" / "settings.js").read_text(encoding="utf-8")
    for theme in ("light", "dark", "sun"):
        assert f"'{theme}'" in init and f"'{theme}'" in settings
    assert "ti_theme" in init and "theme: 'ti_theme'" in (MAP_DIR / "js" / "config.js").read_text(encoding="utf-8")


# ── backend contract the UI codes against ───────────────────────────────────


def test_ui_uses_the_cross_branch_contract_paths():
    js = _js_text()
    for needle in ("/api/place/enrich", "/api/daily", "/api/route/loop", "/api/collections", "/api/streak",
                   "/api/place/ask", "/api/stats/heatmap", "tz_offset_minutes", "'unusual'", "unusual_reasons",
                   "unusual_score"):
        assert needle in js, f"UI no longer references {needle}"


# ── behaviour of small pure helpers, run in node ────────────────────────────


def _run_node(script: str) -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def _js_function(name: str) -> str:
    """Source of a top-level `function name(...) {...}` from api.js (brace-matched)."""
    src = (MAP_DIR / "js" / "api.js").read_text(encoding="utf-8")
    start = src.index(f"function {name}(")
    depth, i = 0, src.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        i += 1
        if depth == 0:
            return src[start:i]


def test_missing_endpoint_detection_tells_absent_routes_from_real_404s():
    """'No places nearby' is a 404 from an endpoint that exists; it must not hide the feature."""
    script = _js_function("isMissingEndpoint") + """
    const err = (status, body) => ({ status, body });
    console.log(JSON.stringify([
      isMissingEndpoint(err(404, '{"detail":"Not Found"}')),                      // old backend: route absent
      isMissingEndpoint(err(404, '{"detail":"No places found within 50 km of here."}')), // real answer
      isMissingEndpoint(err(404, 'x'.repeat(500))),                                // proxy/static 404, not JSON
      isMissingEndpoint(err(405, '')), isMissingEndpoint(err(501, '')),
      isMissingEndpoint(err(422, '{"detail":[]}')), isMissingEndpoint(err(500, '')),
      isMissingEndpoint(null),
    ]));
    """
    assert json.loads(_run_node(script)) == [True, False, True, True, True, False, False, False]


def test_api_detail_prefers_the_servers_own_message():
    script = _js_function("apiDetail") + """
    console.log(JSON.stringify([
      apiDetail({ body: '{"detail":"Try more time or a faster mode."}', message: 'HTTP 404: {...' }),
      apiDetail({ body: '{"detail":[{"type":"x","loc":["body"],"msg":"Field required"}]}', message: 'm' }),
      apiDetail({ body: 'not json', message: 'HTTP 500: not json' }),
    ]));
    """
    assert json.loads(_run_node(script)) == [
        "Try more time or a faster mode.", "Field required", "HTTP 500: not json",
    ]


def test_ids_in_paths_go_through_enc_not_the_raw_encoder():
    """api() strips ! ' ( ) * ~ from paths and encodeURIComponent leaves them alone: o'brien would become obrien."""
    offenders = [
        f.name for f in (MAP_DIR / "js").glob("*.js")
        if f.name != "util.js" and "encodeURIComponent(" in f.read_text(encoding="utf-8")
    ]
    assert not offenders, f"use enc() instead in: {offenders}"


def _js_snippet(filename: str, start_marker: str, end_marker: str) -> str:
    src = (MAP_DIR / "js" / filename).read_text(encoding="utf-8")
    start = src.index(start_marker)
    return src[start:src.index(end_marker, start)]


def test_enc_keeps_every_character_of_an_id():
    util = (MAP_DIR / "js" / "util.js").read_text(encoding="utf-8")
    enc_src = util[util.index("function enc("):util.index("/** A date-only value")]
    sanitize = _js_function("sanitizePath")
    script = enc_src + sanitize + """
    const ids = ["o'brien", "alice(2)", "a*b~c!", "plain-id_1", "a b/c", "привет"];
    // what the request path looks like after api() sanitises it, decoded back
    console.log(JSON.stringify(ids.map(id => decodeURIComponent(sanitizePath('/x/' + enc(id)).slice(3)))));
    """
    ids = ["o'brien", "alice(2)", "a*b~c!", "plain-id_1", "a b/c", "привет"]
    assert json.loads(_run_node(script)) == ids


def test_trip_dates_show_the_stored_calendar_day_in_any_timezone():
    util = (MAP_DIR / "js" / "util.js").read_text(encoding="utf-8")
    fn = util[util.index("function utcDateStr("):util.index("function clamp(")]
    for tz in ("America/Sao_Paulo", "Pacific/Auckland", "UTC"):
        node = shutil.which("node")
        if not node:
            pytest.skip("node is not installed")
        out = subprocess.run(
            [node, "-e", fn + "console.log(utcDateStr('2025-06-10T00:00:00Z'), utcDateStr('nope') === '')"],
            capture_output=True, text=True, timeout=30, env={"TZ": tz, "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        )
        assert out.returncode == 0, out.stderr
        assert out.stdout.split() == ["6/10/2025", "true"], (tz, out.stdout)  # the 10th everywhere, never the 9th


_FLUSH_HARNESS = """
const LS = { pendingVisits: 'q' };
let db = JSON.parse(process.env.QUEUE);
const store = { getJSON: (k, d) => db, setJSON: (k, v) => { db = v; return true; } };
const TI = { net: { ok: process.env.ONLINE !== '0' } };
const calls = [], toasts = [];
const outcomes = JSON.parse(process.env.OUTCOMES);   // per call: "ok" | status number
async function api(path, opts) {
  calls.push(opts.body.place_id);
  const o = outcomes.shift();
  if (o === 'ok') return {};
  const e = new Error('HTTP ' + o); e.status = o; throw e;
}
const toast = (m) => toasts.push(m), refreshHeatmap = () => {}, loadStreak = () => {};
"""


def _flush(queue, outcomes, online=True):
    src = _js_snippet("places.js", "// ── Check-ins waiting for a connection ──", "\nregisterActions(")
    script = _FLUSH_HARNESS + src + """
    flushPendingVisits().then(() => console.log(JSON.stringify({ calls, left: db.map(v => v.place_id), toasts })));
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    out = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, timeout=30,
        env={"QUEUE": json.dumps([{"place_id": p} for p in queue]), "OUTCOMES": json.dumps(outcomes),
             "ONLINE": "1" if online else "0", "PATH": "/usr/bin:/bin"},
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_queued_checkins_are_replayed_in_order_and_cleared():
    r = _flush(["a", "b", "c"], ["ok", "ok", "ok"])
    assert r["calls"] == ["a", "b", "c"] and r["left"] == [] and "Synced 3" in r["toasts"][0]


def test_an_unreachable_server_keeps_the_rest_of_the_queue():
    r = _flush(["a", "b", "c"], ["ok", 503, "ok"])
    assert r["calls"] == ["a", "b"] and r["left"] == ["b", "c"]


def test_a_checkin_the_server_refuses_for_good_is_dropped_and_the_rest_continue():
    r = _flush(["a", "b", "c"], ["ok", 422, "ok"])
    assert r["calls"] == ["a", "b", "c"] and r["left"] == []
    assert "Synced 2" in r["toasts"][0]


def test_rate_limiting_is_not_permanent():
    r = _flush(["a", "b"], [429, "ok"])
    assert r["calls"] == ["a"] and r["left"] == ["a", "b"]


def test_nothing_is_sent_while_offline():
    r = _flush(["a"], ["ok"], online=False)
    assert r["calls"] == [] and r["left"] == ["a"]


def _statement_after(src: str, start: int) -> str:
    """Source from ``start`` to the `;` that ends the statement (brackets balanced, so the `;`
    of a nested arrow-function body does not end it early)."""
    depth = 0
    for i in range(start, len(src)):
        ch = src[i]
        if ch in "({[":
            depth += 1
        elif ch in ")}]":
            depth -= 1
        elif ch == ";" and depth <= 0:
            return src[start:i]
    return src[start:]


_TAINTED = re.compile(r"\$\{[^}]*\b(?:p|place|props|feature)\.(?:name|description)\b")


def _innerhtml_with_place_text(src: str) -> list[str]:
    return [
        stmt[:100] for m in re.finditer(r"\.innerHTML\s*=", src)
        if _TAINTED.search(stmt := _statement_after(src, m.end()))
    ]


def test_explorer_never_puts_place_names_or_descriptions_into_innerhtml():
    """Names/descriptions are world-editable (OSM); explorer.html builds such cards with the DOM API.

    (Other innerHTML uses there interpolate the app's own constants: category labels, layer names.)
    """
    src = (MAP_DIR / "explorer.html").read_text(encoding="utf-8")
    assert _innerhtml_with_place_text(src) == []
    assert "list.replaceChildren(" in src


def test_the_innerhtml_check_is_not_vacuous():
    """The pre-fix code (route cards via `list.innerHTML = routeSuggestions.map(... ${p.name} ...)`) must be caught."""
    old = (
        "list.innerHTML = routeSuggestions.map((p, i) => {\n"
        "  const cats = (p.categories || []).map(c => `${c}`).join(' ');\n"
        "  return `<div>${cats} ${p.name || 'Unknown'}</div>`;\n"
        "}).join('');"
    )
    assert _innerhtml_with_place_text(old)


def test_service_worker_trims_the_tile_cache_without_in_memory_state():
    """A put counter lives in the worker, which the browser stops after ~30 s: the cap would never bind."""
    sw = (MAP_DIR / "sw.js").read_text(encoding="utf-8")
    assert "tilePuts" not in sw
    assert "Math.random()" in sw[sw.index("async function maybeTrimTiles"):sw.index("async function trimCache")]


def test_slow_requests_get_a_timeout_that_outlives_the_servers_discovery_deadline():
    """Discovery answers after up to 25 s (40 s when nothing has arrived); the UI must not give up first."""
    for filename, call in (("offline.js", "/api/offline/places/cache"), ("places.js", "/api/discover")):
        src = (MAP_DIR / "js" / filename).read_text(encoding="utf-8")
        line = next(ln for ln in src.splitlines() if call in ln and "api(" in ln)
        assert "timeout" in line, f"{filename}: {call} relies on the 10 s default"
        timeout = int(re.search(r"timeout:\s*(\d+)", line).group(1))
        assert timeout >= 45000


def test_is_visited_honours_merged_ids():
    util = (MAP_DIR / "js" / "util.js").read_text(encoding="utf-8")
    start = util.index("function isVisited(")
    fn = util[start:util.index("function persistVisited(")]
    script = "const state = { visitedPlaces: new Set(['wiki_Q123', 'osm_9']) };" + fn + """
    console.log(JSON.stringify([
      isVisited({ id: 'osm_9' }),                                              // own id
      isVisited({ id: 'osm_way_42', metadata: { merged_ids: ['wiki_Q123'] } }), // a folded-in id
      isVisited({ id: 'osm_way_42', metadata: { merged_ids: ['x', 'y'] } }),
      isVisited({ id: 'osm_way_42', metadata: {} }), isVisited({ id: 'osm_way_42' }),
      isVisited({ id: 'osm_way_42', metadata: { merged_ids: null } }),
    ]));
    """
    assert json.loads(_run_node(script)) == [True, True, False, False, False, False]


def test_every_visited_check_in_the_ui_goes_through_is_visited():
    """A raw `visitedPlaces.has(place.id)` misses a place whose id changed when another source joined its cluster."""
    for f in (MAP_DIR / "js").glob("*.js"):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if f.name == "util.js":  # the helper itself lives there
                continue
            assert "visitedPlaces.has(p.id)" not in line and "visitedPlaces.has(place.id)" not in line, f"{f.name}:{n}"


def _fn_from(filename: str, name: str) -> str:
    """Source of `[async ]function name(...) {...}` from map/js/<filename> or map/<filename> (brace-matched)."""
    path = MAP_DIR / "js" / filename if (MAP_DIR / "js" / filename).exists() else MAP_DIR / filename
    src = path.read_text(encoding="utf-8")
    start = src.index(f"function {name}(")
    start = src.rfind("async ", max(0, start - 6), start) if src[max(0, start - 6):start] == "async " else start
    depth, i = 0, src.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        i += 1
        if depth == 0:
            return src[start:i]


def test_a_checkin_queued_while_a_flush_is_running_is_not_lost():
    src = _js_snippet("places.js", "// ── Check-ins waiting for a connection ──", "\nregisterActions(")
    script = """
    const LS = { pendingVisits: 'q' };
    let db = [{ place_id: 'a' }, { place_id: 'b' }];
    const store = { getJSON: (k, d) => JSON.parse(JSON.stringify(db)), setJSON: (k, v) => { db = JSON.parse(JSON.stringify(v)); return true; } };
    const TI = { net: { ok: true } }, toasts = [], sent = [];
    let first = true;
    async function api(path, opts) {
      sent.push(opts.body.place_id);
      if (first) { first = false; queueVisit({ place_id: 'LATE' }); }   // a check-in fails while we are mid-flush
      return {};
    }
    const toast = (m) => toasts.push(m), refreshHeatmap = () => {}, loadStreak = () => {};
    """ + src + """
    flushPendingVisits().then(() => console.log(JSON.stringify({ sent, left: db.map(v => v.place_id) })));
    """
    out = json.loads(_run_node(script))
    assert out == {"sent": ["a", "b", "LATE"], "left": []}


def test_friendly_error_words_for_rate_limits_and_a_spent_budget():
    script = _js_function("friendlyError") + """
    console.log(JSON.stringify([friendlyError({ status: 429 }, 'x'), friendlyError({ status: 503 }, 'x'),
                                friendlyError({ status: 500 }, 'fallback'), friendlyError(null, 'fallback')]));
    """
    a, b, c, d = json.loads(_run_node(script))
    assert "Too many" in a and "AI" in b and c == d == "fallback"


def test_user_id_never_contains_a_path_separator():
    util = (MAP_DIR / "js" / "util.js").read_text(encoding="utf-8")
    line = next(ln for ln in util.splitlines() if ln.startswith("function getActiveUserId"))
    script = "const USER_ID = 'explorer-1'; let typed = '';\nconst $ = () => ({ value: typed });\n" + line + """
    const cases = ['a/b', 'a\\\\b', '  padded  ', '', '/', 'x'.repeat(150)];
    console.log(JSON.stringify(cases.map(c => { typed = c; return getActiveUserId(); })));
    """
    out = json.loads(_run_node(script))
    assert out[0] == "ab" and out[1] == "ab" and out[2] == "padded"
    assert out[3] == "explorer-1" and out[4] == "explorer-1" and len(out[5]) == 100


def test_the_service_worker_prefers_a_cached_copy_to_a_server_error():
    sw = _fn_from("sw.js", "networkFirstApi")
    script = """
    const API_CACHE = 'c', API_NETWORK_TIMEOUT_MS = 1000, API_CACHE_MAX = 10;
    const stored = new Map([['GET /api/daily', { cached: true }]]);
    const cache = { match: async (r) => stored.get(r.key), put: async () => {} };
    const caches = { open: async () => cache };
    async function trimCache() {}
    let status = 200;
    const fetch = async () => ({ ok: status < 400, status, headers: { get: () => '' }, clone() { return this; }, fresh: true });
    """ + sw + """
    const ev = { request: { key: 'GET /api/daily' }, waitUntil() {} };
    (async () => {
      const out = [];
      status = 500; out.push((await networkFirstApi(ev)).cached === true);   // 5xx + cached copy -> cached copy
      status = 200; out.push((await networkFirstApi(ev)).fresh === true);    // healthy -> fresh answer
      ev.request.key = 'GET /api/other'; status = 500;
      out.push((await networkFirstApi(ev)).status === 500);                  // 5xx, nothing cached -> the error itself
      console.log(JSON.stringify(out));
    })();
    """
    assert json.loads(_run_node(script)) == [True, True, True]
