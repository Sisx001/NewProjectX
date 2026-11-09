"""Extension manifest contract (host API 1.x), semver ranges and safe package reading.

Isolation model: no third-party code ever runs inside the VOLTORA process. Extensions are either declarative
(components, widgets, settings, admin links, aliases of core read tools) or remote (VOLTORA calls the extension's
HTTPS endpoint with HMAC-signed requests limited to `allowedHosts`, with timeouts and quotas).
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ...config import HOST_API_VERSION
from ...core.errors import AppError
from ...core.rbac import PERMISSIONS
from ...core.services import _canon
from ..ai.schema import check_schema

MAX_MANIFEST_BYTES = 256_000
MAX_ZIP_BYTES = 5_000_000
MAX_ZIP_ENTRIES = 200
ID_RE = r"^[a-z0-9][a-z0-9-]*(\.[a-z0-9][a-z0-9-]*)+$"
VERSION_RE = r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$"
SLOTS = {"cart.summary", "cart.drawer", "product.below_price", "product.below_buy", "header.announcement",
         "footer.extra", "checkout.summary", "account.dashboard", "home.section"}


# ---------------------------------------------------------------- semver
def parse_version(v: str) -> tuple[int, int, int]:
    m = re.match(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", v.strip())
    if not m:
        raise ValueError(f"Invalid version {v!r}")
    return int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0)


def _comparator(tok: str) -> list[tuple[str, tuple[int, int, int]]]:
    tok = tok.strip()
    if tok in ("*", "x", ""):
        return []
    if tok.startswith("^"):
        lo = parse_version(tok[1:])
        hi = (lo[0] + 1, 0, 0) if lo[0] > 0 else (0, lo[1] + 1, 0)
        return [(">=", lo), ("<", hi)]
    if tok.startswith("~"):
        lo = parse_version(tok[1:])
        return [(">=", lo), ("<", (lo[0], lo[1] + 1, 0))]
    m = re.match(r"^(>=|<=|>|<|=)?\s*(.+)$", tok)
    op, ver = (m.group(1) or "="), m.group(2)
    parts = ver.lstrip("v").split(".")
    if op == "=" and (len(parts) < 3 or any(p in ("x", "*") for p in parts)):
        nums = [int(p) for p in parts if p not in ("x", "*")]
        if len(nums) == 1:
            return [(">=", (nums[0], 0, 0)), ("<", (nums[0] + 1, 0, 0))]
        if len(nums) == 2:
            return [(">=", (nums[0], nums[1], 0)), ("<", (nums[0], nums[1] + 1, 0))]
    return [(op, parse_version(ver))]


def satisfies(version: str, rng: str) -> bool:
    v = parse_version(version)
    for alt in rng.split("||"):
        toks = re.findall(r"(?:>=|<=|>|<|=|\^|~)?\s*[v\dx*][\w.*-]*", alt)
        ok = True
        for tok in toks or ["*"]:
            for op, ref in _comparator(tok.replace(" ", "")):
                ok &= {">=": v >= ref, "<=": v <= ref, ">": v > ref, "<": v < ref, "=": v == ref}[op]
        if ok:
            return True
    return False


def valid_range(rng: str) -> bool:
    try:
        satisfies("1.0.0", rng)
        return bool(rng.strip())
    except (ValueError, KeyError, AttributeError):
        return False


# ---------------------------------------------------------------- manifest model
class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SecretSpec(_M):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")
    label: str = Field(min_length=1, max_length=120)
    required: bool = True
    description: str = Field("", max_length=500)


class ToolHandler(_M):
    type: Literal["remote", "core_alias"]
    url: str | None = Field(None, max_length=500)
    tool: str | None = None
    fixed_args: dict[str, Any] = Field(default_factory=dict)
    args_from_settings: dict[str, str] = Field(default_factory=dict)


class AiToolSpec(_M):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")
    description: str = Field(min_length=5, max_length=500)
    input_schema: dict = Field(default_factory=lambda: {"type": "object", "properties": {}})
    permission: str
    side_effect: Literal["read", "write"] = "read"
    handler: ToolHandler


class TelegramTarget(_M):
    token_secret: str
    chat_id_setting: str


class EventSubscription(_M):
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,40}$")
    events: list[str] = Field(default_factory=list, max_length=50)
    events_setting: str | None = None
    format: Literal["voltora", "slack", "telegram"] = "voltora"
    url: str | None = Field(None, max_length=500)
    url_setting: str | None = None
    url_secret: str | None = None
    telegram: TelegramTarget | None = None
    when_setting: dict[str, Any] = Field(default_factory=dict)  # only active when settings match


class AdapterSpec(_M):
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,40}$")
    label: str = Field(min_length=2, max_length=120)
    url: str = Field(max_length=500)
    capabilities: list[str] = Field(default_factory=list, max_length=30)
    countries: list[str] = Field(default_factory=lambda: ["BD"])
    docs: str | None = Field(None, max_length=500)


class AdminLink(_M):
    label: str = Field(min_length=2, max_length=80)
    url: str | None = Field(None, max_length=500)
    path: str | None = Field(None, pattern=r"^/admin/[a-z0-9/_-]*$")


class Widget(_M):
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,40}$")
    label: str = Field(min_length=2, max_length=120)
    slot: str
    component: str
    props: dict[str, Any] = Field(default_factory=dict)
    props_from_settings: dict[str, str] = Field(default_factory=dict)


class Extensions(_M):
    builder_components: list[dict] = Field(default_factory=list, max_length=50)
    storefront_widgets: list[Widget] = Field(default_factory=list, max_length=50)
    ai_tools: list[AiToolSpec] = Field(default_factory=list, max_length=50)
    event_subscriptions: list[EventSubscription] = Field(default_factory=list, max_length=20)
    payment_providers: list[AdapterSpec] = Field(default_factory=list, max_length=10)
    couriers: list[AdapterSpec] = Field(default_factory=list, max_length=10)
    admin_links: list[AdminLink] = Field(default_factory=list, max_length=20)


class Hooks(_M):
    install: str | None = None
    uninstall: str | None = None
    enable: str | None = None
    disable: str | None = None
    health: str | None = None


class Manifest(_M):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_: int = Field(1, alias="schema")
    id: str = Field(pattern=ID_RE, max_length=100)
    name: str = Field(min_length=2, max_length=120)
    version: str = Field(pattern=VERSION_RE)
    publisher: str = Field(min_length=2, max_length=120)
    description: str = Field("", max_length=2000)
    hostApi: str = Field(max_length=60)  # noqa: N815 - manifest field name
    dependencies: dict[str, str] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list, max_length=50)
    permissions: list[str] = Field(default_factory=list, max_length=40)
    settings: dict = Field(default_factory=lambda: {"type": "object", "properties": {}})
    secrets: list[SecretSpec] = Field(default_factory=list, max_length=20)
    allowedHosts: list[str] = Field(default_factory=list, max_length=20)  # noqa: N815
    extensions: Extensions = Field(default_factory=Extensions)
    hooks: Hooks = Field(default_factory=Hooks)
    migrations: list = Field(default_factory=list)
    integrity: str | None = Field(None, pattern=r"^sha256-[0-9a-f]{64}$")
    license: str = Field("proprietary", max_length=60)
    docs: str = Field("", max_length=20000)
    changelog: str = Field("", max_length=20000)
    homepage: str | None = Field(None, max_length=300)

    @field_validator("allowedHosts")
    @classmethod
    def _hosts(cls, v):
        for h in v:
            if not re.match(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", h):
                raise ValueError(f"invalid host {h!r}")
        return v


def integrity_of(raw: dict) -> str:
    body = {k: v for k, v in raw.items() if k != "integrity"}
    return "sha256-" + hashlib.sha256(_canon(body).encode()).hexdigest()


def remote_urls(m: Manifest) -> list[tuple[str, str]]:
    """(purpose, url) of every endpoint VOLTORA would call that is operated by the extension publisher."""
    out = [(f"hook:{k}", v) for k, v in m.hooks.model_dump().items() if v]
    out += [(f"ai_tool:{t.name}", t.handler.url) for t in m.extensions.ai_tools if t.handler.type == "remote"
            and t.handler.url]
    out += [(f"event:{s.key}", s.url) for s in m.extensions.event_subscriptions if s.url]
    out += [(f"payment:{a.key}", a.url) for a in m.extensions.payment_providers]
    out += [(f"courier:{a.key}", a.url) for a in m.extensions.couriers]
    return out


def _err(msg: str, details=None) -> AppError:
    return AppError("invalid_manifest", msg, status=422, details=details)


def validate_manifest(raw: Any) -> Manifest:
    if not isinstance(raw, dict):
        raise _err("Manifest must be a JSON object")
    if len(_canon(raw)) > MAX_MANIFEST_BYTES:
        raise _err("Manifest is too large")
    try:
        m = Manifest.model_validate(raw)
    except ValidationError as exc:
        raise _err("Manifest does not match the extension contract",
                   [{"loc": ".".join(str(x) for x in e["loc"]), "msg": e["msg"]} for e in exc.errors()]) from exc
    problems: list[str] = []
    if m.integrity and m.integrity != integrity_of(raw):
        problems.append("integrity hash does not match the manifest contents")
    if not valid_range(m.hostApi):
        problems.append(f"hostApi {m.hostApi!r} is not a valid version range")
    for dep, rng in m.dependencies.items():
        if not re.match(ID_RE, dep) or not valid_range(rng):
            problems.append(f"dependency {dep} has an invalid id or range")
        if dep == m.id:
            problems.append("an extension cannot depend on itself")
    bad = [p for p in m.permissions if p not in PERMISSIONS]
    if bad:
        problems.append(f"unknown permissions {bad} (\"*\" is never grantable to extensions)")
    if m.migrations:
        problems.append("database migrations are not supported for extensions in host API 1.x")
    try:
        check_schema(m.settings)
        if m.settings.get("type") != "object":
            problems.append("settings schema must be an object")
    except ValueError as exc:
        problems.append(f"settings schema: {exc}")
    setting_keys = set((m.settings.get("properties") or {}).keys())
    secret_keys = {s.key for s in m.secrets}
    from ..webhooks.outbound import validate_url

    for purpose, url in remote_urls(m):
        try:
            if not url.startswith("https://"):
                raise AppError("unsafe_url", "must use https")
            validate_url(url, allowed_hosts=m.allowedHosts, resolve=False)
        except AppError as exc:
            problems.append(f"{purpose}: {exc.message}")
    from ..ai.tools import TOOLS

    for t in m.extensions.ai_tools:
        if t.permission not in m.permissions:
            problems.append(f"ai tool {t.name} uses permission {t.permission} that the manifest does not request")
        try:
            check_schema(t.input_schema)
        except ValueError as exc:
            problems.append(f"ai tool {t.name} input schema: {exc}")
        if t.handler.type == "remote" and not t.handler.url:
            problems.append(f"ai tool {t.name}: remote handler needs a url")
        if t.handler.type == "core_alias":
            core = TOOLS.get(t.handler.tool or "")
            if core is None or core.side_effect != "read":
                problems.append(f"ai tool {t.name}: core_alias must reference an existing read-only core tool")
            elif core.permission != t.permission:
                problems.append(f"ai tool {t.name}: permission must equal the aliased tool's ({core.permission})")
            if t.side_effect != "read":
                problems.append(f"ai tool {t.name}: aliases are read-only")
            for s in t.handler.args_from_settings.values():
                if s not in setting_keys:
                    problems.append(f"ai tool {t.name}: unknown setting {s}")
    for s in m.extensions.event_subscriptions:
        targets = [x for x in (s.url, s.url_setting, s.url_secret, s.telegram) if x]
        if len(targets) != 1:
            problems.append(f"event subscription {s.key}: exactly one of url, url_setting, url_secret, telegram")
        if s.url_setting and s.url_setting not in setting_keys:
            problems.append(f"event subscription {s.key}: unknown setting {s.url_setting}")
        if s.url_secret and s.url_secret not in secret_keys:
            problems.append(f"event subscription {s.key}: unknown secret {s.url_secret}")
        if s.telegram and (s.telegram.token_secret not in secret_keys or s.telegram.chat_id_setting not in setting_keys):
            problems.append(f"event subscription {s.key}: telegram target references unknown secret/setting")
        if s.format == "telegram" and not s.telegram:
            problems.append(f"event subscription {s.key}: telegram format needs a telegram target")
        if not s.events and not s.events_setting:
            problems.append(f"event subscription {s.key}: list events or events_setting")
        if s.events_setting and s.events_setting not in setting_keys:
            problems.append(f"event subscription {s.key}: unknown setting {s.events_setting}")
    comp_types = set()
    prefix = f"plugin.{m.id}."
    for c in m.extensions.builder_components:
        t = str(c.get("type", ""))
        if not t.startswith(prefix):
            problems.append(f"builder component {t!r} must be namespaced as {prefix}<name>")
        problems += _check_component(c)
        comp_types.add(t)
    for w in m.extensions.storefront_widgets:
        if w.slot not in SLOTS:
            problems.append(f"widget {w.key}: unknown slot {w.slot} (known: {sorted(SLOTS)})")
        if w.component not in comp_types:
            problems.append(f"widget {w.key}: component {w.component} is not declared in builder_components")
        for s in w.props_from_settings.values():
            if s not in setting_keys:
                problems.append(f"widget {w.key}: unknown setting {s}")
    for link in m.extensions.admin_links:
        if bool(link.url) == bool(link.path):
            problems.append(f"admin link {link.label}: give exactly one of url or path")
        elif link.url:
            try:
                validate_url(link.url, allowed_hosts=m.allowedHosts, resolve=False)
            except AppError as exc:
                problems.append(f"admin link {link.label}: {exc.message}")
    if problems:
        raise _err("Manifest failed validation: " + "; ".join(problems[:10]), problems)
    return m


def _check_component(c: dict) -> list[str]:
    try:
        from ..content.registry import ComponentDef, _check_prop, _prop_from_dict
    except ImportError:
        return [] if c.get("type") and c.get("label") else ["builder component needs type and label"]
    try:
        d = dict(c)
        d["props"] = [_prop_from_dict(p) for p in d.get("props", [])]
        cd = ComponentDef(**d)
        for p in cd.props:
            _check_prop(p)
        from ..content.registry import CATEGORIES, STYLE_GROUPS

        if cd.category not in CATEGORIES:
            return [f"component {cd.type}: unknown category {cd.category}"]
        if any(s not in STYLE_GROUPS for s in cd.styles):
            return [f"component {cd.type}: unknown style group"]
    except (TypeError, ValueError) as exc:
        return [f"component {c.get('type')!r}: {exc}"]
    return []


def compatible(m: Manifest) -> bool:
    return satisfies(HOST_API_VERSION, m.hostApi)


# ---------------------------------------------------------------- package reading
def read_package(filename: str, data: bytes) -> tuple[dict, str, str]:
    """Returns (manifest dict, sha256 of the package, readme text). Never extracts files to disk."""
    if len(data) > MAX_ZIP_BYTES:
        raise AppError("too_large", "Extension packages are limited to 5 MB")
    sha = hashlib.sha256(data).hexdigest()
    name = filename.lower()
    if name.endswith(".json"):
        try:
            return json.loads(data.decode("utf-8")), sha, ""
        except (UnicodeDecodeError, ValueError) as exc:
            raise _err("manifest.json is not valid UTF-8 JSON") from exc
    if not name.endswith(".zip"):
        raise AppError("unsupported_file", "Upload a manifest .json or a .zip containing manifest.json")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise AppError("bad_zip", "The file is not a valid zip archive") from exc
    infos = zf.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise AppError("bad_zip", "The archive has too many files")
    total = 0
    for i in infos:
        n = i.filename
        if n.startswith(("/", "\\")) or ".." in n.replace("\\", "/").split("/") or ":" in n:
            raise AppError("bad_zip", f"Unsafe path in archive: {n}")
        if (i.external_attr >> 16) & 0o170000 == 0o120000:
            raise AppError("bad_zip", f"Symbolic links are not allowed: {n}")
        total += i.file_size
        if total > MAX_ZIP_BYTES * 4 or (i.compress_size and i.file_size / i.compress_size > 200):
            raise AppError("bad_zip", "The archive expands too much (possible zip bomb)")
    names = [i.filename for i in infos if not i.is_dir()]
    manifest_name = "manifest.json" if "manifest.json" in names else None
    if manifest_name is None:
        tops = {n.split("/", 1)[0] for n in names}
        if len(tops) == 1 and f"{next(iter(tops))}/manifest.json" in names:
            manifest_name = f"{next(iter(tops))}/manifest.json"
    if manifest_name is None:
        raise _err("The archive has no manifest.json at its root")
    raw = zf.read(manifest_name)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise _err("Manifest is too large")
    readme_name = manifest_name.replace("manifest.json", "README.md")
    readme = zf.read(readme_name).decode("utf-8", "replace")[:20000] if readme_name in names else ""
    try:
        return json.loads(raw.decode("utf-8")), sha, readme
    except (UnicodeDecodeError, ValueError) as exc:
        raise _err("manifest.json is not valid UTF-8 JSON") from exc
