"""Versioned service metadata. Credentials are write-only and live in the local .env."""
import copy
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from config import Config, ENV_PATH, BASE_DIR

KINDS = {"text": {"llm", "vlm"}, "image": {"t2i", "i2i"}, "video": {"video"}}
MODEL_FIELDS = {"llm": "llm_model", "vlm": "vlm_model", "image_t2i": "image_t2i_model",
                "image_it2i": "image_it2i_model", "video_first_frame": "video_first_frame_model",
                "video_start_end": "video_start_end_model", "video_reference": "video_reference_model",
                "video_text": "video_text_model"}
MODE_ABILITIES = {"first_frame": "first_frame_i2v", "start_end_frame": "start_end_frame_i2v",
                  "reference": "reference_to_video", "text": "text_to_video"}
PROVIDERS = {"openai": "OpenAI", "gemini": "Gemini", "deepseek": "DeepSeek", "tudou": "土豆", "toapis": "ToAPIs",
             "dashscope": "阿里云百炼", "ark": "火山引擎", "kling": "可灵", "agnes": "Agnes"}


class SettingsError(ValueError):
    def __init__(self, code, message, status=422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def model_alias(connection_id, model):
    return f"svc/{connection_id}/{model}"


def raw_model(model):
    return model.split("/", 2)[2] if model and model.startswith("svc/") and model.count("/") >= 2 else model


def abilities(caps):
    tags = set(caps.get("adapter_ability_types", []))
    if "start_end_i2v" in tags:
        tags.add("start_end_frame_i2v")
    return tags


def secret_fields(provider):
    return ("access_key", "secret_key") if provider == "kling" else ("api_key",)


def provider_attr(provider, field):
    return "GOOGLE_GEMINI_BASE_URL" if provider == "gemini" and field == "base_url" else f"{provider}_{field}".upper()


def _url(value):
    parsed = urlsplit(str(value))
    if (parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or any(c in value for c in "\r\n\t")):
        raise SettingsError("INVALID_URL", "服务地址须为 HTTP(S) 地址，不能包含账号、密码、查询参数或片段")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise SettingsError("HTTPS_REQUIRED", "外部服务请使用 HTTPS；HTTP 仅限本机地址")
    return value.rstrip("/")


def catalog():
    from models.config_model import MODEL_CONFIG
    return [{"id": mid, **copy.deepcopy(meta)} for mid, meta in MODEL_CONFIG["models"].items()]


class ServiceSettings:
    def __init__(self, directory=None, env_path=None):
        self.directory = Path(directory or os.environ.get("YC_SETTINGS_DIR", BASE_DIR / ".runtime" / "settings"))
        self.env_path = Path(env_path or ENV_PATH)
        self._lock = threading.RLock()

    def _db(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.directory / "settings.sqlite3", timeout=10)
        db.execute("CREATE TABLE IF NOT EXISTS versions (revision INTEGER PRIMARY KEY, created_at REAL NOT NULL, document TEXT NOT NULL)")
        return db

    def _initial(self):
        cfg = Config.as_dict()
        connections = []
        defaults = {**cfg["generation"], "target_duration": 60, "enable_concurrency": False, "language": "zh-CN",
                    "models": {k: cfg["models"].get(k, "") for k in MODEL_FIELDS}}
        models = catalog()
        selected = set(defaults["models"].values())
        for provider in PROVIDERS:
            for kind, types in KINDS.items():
                options = [m["id"] for m in models if m["provider"] == provider and types.intersection(m["type"])]
                configured = any(getattr(Config, provider_attr(provider, f), "") for f in secret_fields(provider))
                if not options or not (configured or selected.intersection(options)):
                    continue
                cid = f"legacy-{provider}-{kind}"
                connections.append({"id": cid, "name": f"{PROVIDERS[provider]} · {dict(text='文本', image='图片', video='视频')[kind]} · 现有配置", "kind": kind, "provider": provider,
                                    "base_url": cfg["api_providers"][provider]["base_url"], "models": [m for m in options if m in selected] or options,
                                    "enabled": True, "enable_proxy": bool(cfg["api_providers"][provider].get("enable_proxy")),
                                    "secrets": {f: f"legacy:{provider}:{f}" for f in secret_fields(provider)},
                                    "test": {"status": "untested", "message": "尚未进行连接测试", "tested_at": None}})
                for field, mid in defaults["models"].items():
                    if mid in options:
                        defaults["models"][field] = model_alias(cid, mid)
        return {"revision": 1, "connections": connections, "defaults": defaults}

    def read(self):
        with self._lock, self._db() as db:
            row = db.execute("SELECT document FROM versions ORDER BY revision DESC LIMIT 1").fetchone()
            if row:
                return json.loads(row[0])
            value = self._initial()
            db.execute("INSERT OR IGNORE INTO versions VALUES (1, ?, ?)", (time.time(), json.dumps(value, ensure_ascii=False)))
            return json.loads(db.execute("SELECT document FROM versions ORDER BY revision DESC LIMIT 1").fetchone()[0])

    def _secret(self, ref):
        if ref.startswith("legacy:"):
            _, provider, field = ref.split(":")
            return getattr(Config, provider_attr(provider, field), "") or ""
        # Read this store's .env, not arbitrary environment variable names supplied by users.
        if not re.fullmatch(r"YC_SERVICE_[A-F0-9]{32}", ref or ""):
            return ""
        if self.env_path.exists():
            for line in self.env_path.read_text().splitlines():
                key, _, value = line.partition("=")
                if key.strip() == ref:
                    return value.strip().strip('"').strip("'")
        return ""

    def _write_secret(self, value):
        if len(value) > 4096 or any(c in value for c in "\r\n\x00") or value != value.strip():
            raise SettingsError("INVALID_CREDENTIAL", "凭据格式无效，请检查空格或换行")
        ref = "YC_SERVICE_" + uuid.uuid4().hex.upper()
        text = self.env_path.read_text() if self.env_path.exists() else ""
        self.env_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.env_path.parent, prefix=".env.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(text.rstrip("\n") + "\n" + ref + "=" + value + "\n")
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.env_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return ref

    def public(self):
        value = self.read()
        for conn in value["connections"]:
            conn["credentials"] = {field: bool(self._secret(ref)) for field, ref in conn.pop("secrets").items()}
        return value

    def runtime(self):
        value = self.read()
        for conn in value["connections"]:
            conn["credentials"] = {field: self._secret(ref) for field, ref in conn.pop("secrets").items()}
        return value

    def mutate(self, expected_revision, change):
        with self._lock, self._db() as db:
            self.read()
            db.execute("BEGIN IMMEDIATE")
            current = json.loads(db.execute("SELECT document FROM versions ORDER BY revision DESC LIMIT 1").fetchone()[0])
            if expected_revision != current["revision"]:
                raise SettingsError("SETTINGS_CHANGED", "设置已在其他页面更新，请重新加载后再保存", 409)
            change(current)
            current["revision"] += 1
            db.execute("INSERT INTO versions VALUES (?, ?, ?)", (current["revision"], time.time(), json.dumps(current, ensure_ascii=False)))
        return self.public()

    def save_connection(self, revision, values, connection_id=None):
        provider, kind = values["provider"], values["kind"]
        if provider not in PROVIDERS or kind not in KINDS:
            raise SettingsError("INVALID_SERVICE", "请选择有效的服务类型和供应商")
        options = {m["id"] for m in catalog() if m["provider"] == provider and KINDS[kind].intersection(m["type"])}
        models = list(dict.fromkeys(values["models"]))
        if not models or any(m not in options for m in models):
            raise SettingsError("MODEL_NOT_SUPPORTED", "请选择该供应商已接入的模型；新模型需先适配协议")
        base_url = _url(values["base_url"])
        cid = connection_id or uuid.uuid4().hex

        def change(doc):
            previous = next((c for c in doc["connections"] if c["id"] == cid), None)
            if connection_id and not previous:
                raise SettingsError("SERVICE_NOT_FOUND", "服务不存在", 404)
            refs = copy.deepcopy(previous.get("secrets", {})) if previous and previous["provider"] == provider else {}
            if previous and (previous["provider"] != provider or previous["kind"] != kind):
                raise SettingsError("SERVICE_TYPE_LOCKED", "已有服务的供应商和类型不可切换，请新增另一条连接")
            invalid = set(values.get("credentials", {})) | set(values.get("clear_credentials", []))
            if not invalid.issubset(secret_fields(provider)):
                raise SettingsError("INVALID_CREDENTIAL", "凭据字段与供应商不匹配")
            for field in secret_fields(provider):
                value = values.get("credentials", {}).get(field, "")
                if value and field in values.get("clear_credentials", []):
                    raise SettingsError("INVALID_CREDENTIAL", "同一凭据不能同时替换和清空")
                if value:
                    refs[field] = self._write_secret(value)
                elif field in values.get("clear_credentials", []):
                    refs[field] = ""
                else:
                    refs.setdefault(field, "")
            updated = {"id": cid, "name": values["name"].strip(), "provider": provider, "kind": kind, "base_url": base_url,
                       "models": models, "enabled": values.get("enabled", True), "enable_proxy": values.get("enable_proxy", False),
                       "secrets": refs, "test": {"status": "untested", "message": "配置已保存，尚未测试", "tested_at": None}}
            if previous:
                stable = ("provider", "base_url", "models", "secrets", "enable_proxy")
                if all(updated[k] == previous[k] for k in stable):
                    updated["test"] = previous["test"]
                doc["connections"][doc["connections"].index(previous)] = updated
            else:
                doc["connections"].append(updated)
        return self.mutate(revision, change)

    def save_defaults(self, revision, defaults):
        def change(doc):
            validate_defaults(defaults, doc)
            doc["defaults"] = copy.deepcopy(defaults)
        return self.mutate(revision, change)

    def set_default(self, revision, connection_id, model):
        def change(doc):
            conn = next((c for c in doc["connections"] if c["id"] == connection_id), None)
            if not conn or model not in conn["models"] or not conn["enabled"]:
                raise SettingsError("SERVICE_UNAVAILABLE", "服务已停用或模型不可用")
            from models.config_model import MODEL_CONFIG
            metadata = MODEL_CONFIG["models"][model]
            alias = model_alias(connection_id, model)
            field_types = {"llm": "llm", "vlm": "vlm", "image_t2i": "t2i", "image_it2i": "i2i"}
            for field, typ in field_types.items():
                if typ in KINDS[conn["kind"]] and typ in metadata["type"]:
                    doc["defaults"]["models"][field] = alias
            if conn["kind"] == "video":
                caps = metadata.get("capabilities", {})
                for mode, ability in MODE_ABILITIES.items():
                    if ability in abilities(caps):
                        field = {"first_frame": "video_first_frame", "start_end_frame": "video_start_end", "reference": "video_reference", "text": "video_text"}[mode]
                        doc["defaults"]["models"][field] = alias
                # A model switch and its format change must be one valid transaction.
                active = {"text": "video_text", "first_frame": "video_first_frame", "start_end_frame": "video_start_end", "reference": "video_reference"}[doc["defaults"]["video_generation_mode"]]
                if doc["defaults"]["models"].get(active) == alias:
                    for field, allowed in (("video_resolution", caps.get("resolutions")), ("video_ratio", caps.get("ratios"))):
                        if allowed and doc["defaults"][field] not in allowed:
                            doc["defaults"][field] = allowed[0]
            validate_defaults(doc["defaults"], doc)
        return self.mutate(revision, change)

    def probe(self, revision, connection_id):
        doc = self.runtime()
        if doc["revision"] != revision:
            raise SettingsError("SETTINGS_CHANGED", "设置已更新，请重新加载后测试", 409)
        conn = next((c for c in doc["connections"] if c["id"] == connection_id), None)
        if not conn:
            raise SettingsError("SERVICE_NOT_FOUND", "服务不存在", 404)
        result = probe_connection(conn)
        def change(current):
            next(c for c in current["connections"] if c["id"] == connection_id)["test"] = {**result, "tested_at": time.time()}
        return self.mutate(revision, change)


def resolve_service(model, doc):
    if not model or not model.startswith("svc/"):
        return None
    parts = model.split("/", 2)
    if len(parts) != 3:
        raise SettingsError("SERVICE_UNAVAILABLE", "模型连接格式无效")
    conn = next((c for c in doc["connections"] if c["id"] == parts[1]), None)
    if not conn or not conn["enabled"] or parts[2] not in conn["models"]:
        raise SettingsError("SERVICE_UNAVAILABLE", "所选服务已停用或模型已移除，请在生成配置中重新选择")
    return conn


def validate_defaults(defaults, doc):
    from models.config_model import MODEL_CONFIG
    types = {"llm": "llm", "vlm": "vlm", "image_t2i": "t2i", "image_it2i": "i2i"}
    for field, model in defaults["models"].items():
        if not model:
            continue
        resolve_service(model, doc)
        meta = MODEL_CONFIG["models"].get(raw_model(model))
        if not meta:
            raise SettingsError("MODEL_UNAVAILABLE", "默认模型不在可选清单中，请重新选择")
        if field in types and types[field] not in meta["type"]:
            raise SettingsError("MODEL_CAPABILITY_MISMATCH", "默认模型不支持该用途")
        if field.startswith("video_"):
            mode = {"video_first_frame": "first_frame", "video_start_end": "start_end_frame", "video_reference": "reference", "video_text": "text"}[field]
            if MODE_ABILITIES[mode] not in abilities(meta.get("capabilities", {})):
                raise SettingsError("MODEL_CAPABILITY_MISMATCH", "视频模型不支持所选参考方式")
    active_field = {"first_frame": "video_first_frame", "start_end_frame": "video_start_end", "reference": "video_reference", "text": "video_text"}[defaults["video_generation_mode"]]
    model = defaults["models"].get(active_field)
    if model:
        validate_video_options(model, ratio=defaults["video_ratio"], resolution=defaults["video_resolution"])


def validate_video_options(model, *, ratio=None, resolution=None, duration=None, mode=None):
    from models.config_model import MODEL_CONFIG
    caps = MODEL_CONFIG["models"].get(raw_model(model), {}).get("capabilities", {})
    for field, value in (("ratios", ratio), ("resolutions", resolution)):
        if value and caps.get(field) and value.upper() not in [v.upper() for v in caps[field]]:
            raise SettingsError("VIDEO_CAPABILITY_MISMATCH", "所选视频模型不支持该画幅或分辨率，请调整生成配置")
    spec = caps.get("duration", {})
    if duration is not None and spec.get("verified"):
        if ((spec.get("integer") and int(duration) != duration) or duration < spec.get("min", 0)
                or duration > spec.get("max", float("inf")) or ((spec.get("values") or spec.get("allowed")) and duration not in (spec.get("values") or spec["allowed"]))):
            raise SettingsError("VIDEO_DURATION_INVALID", "片段时长不在所选模型支持范围内，请调整片段后再生成")
    if mode and caps.get("api_contract_verified") and MODE_ABILITIES[mode] not in abilities(caps):
        raise SettingsError("VIDEO_MODE_UNSUPPORTED", "所选模型不支持这种参考方式")


def probe_connection(conn):
    if not all(conn["credentials"].values()) or not conn["credentials"]:
        return {"status": "missing", "message": "请先配置完整凭据"}
    if conn["provider"] in {"kling", "dashscope", "ark"}:
        return {"status": "unsupported", "message": "此适配器没有已接入的只读检测端点；能力待验，未发起生成"}
    import requests
    base = _url(conn["base_url"])
    headers = {"Authorization": f"Bearer {conn['credentials'].get('api_key', '')}"}
    if conn["provider"] == "gemini":
        url = base + "/models"
        headers = {"x-goog-api-key": conn["credentials"].get("api_key", "")}
    else:
        url = base + ("/models" if base.endswith("/v1") else "/v1/models")
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.get(url, headers=headers, timeout=(5, 10), allow_redirects=False,
                                   proxies={"http": Config.PROXY, "https": Config.PROXY} if conn.get("enable_proxy") and Config.PROXY else None)
        if response.status_code in (401, 403):
            return {"status": "error", "message": "认证失败或无模型清单权限，请检查凭据和账号权限"}
        if response.status_code != 200:
            return {"status": "error", "message": f"模型清单返回 HTTP {response.status_code}；请核对服务地址与协议"}
        data = response.json()
        items = data.get("data", data.get("models")) if isinstance(data, dict) else None
        if not isinstance(items, list):
            return {"status": "error", "message": "响应不是模型清单，请检查服务地址"}
        if conn["provider"] == "toapis":
            visible = {item.get("id") for item in items if isinstance(item, dict)}
            if any(model.removeprefix("toapis/") not in visible for model in conn["models"]):
                return {"status": "error", "message": "Key 可连接，但部分所选型号未出现在账号模型清单；请检查权限"}
        return {"status": "available", "message": "模型清单连接可用；生成能力待验，未调用付费模型"}
    except (requests.RequestException, ValueError):
        return {"status": "error", "message": "连接失败或超时，请检查网络、代理和服务地址后重试"}


service_settings = ServiceSettings()


def project_defaults():
    doc = service_settings.read()
    defaults = copy.deepcopy(doc["defaults"])
    models = defaults.pop("models")
    values = {**defaults, **{MODEL_FIELDS[k]: v for k, v in models.items()}, "settings_revision": doc["revision"]}
    active = {"first_frame": "video_first_frame_model", "start_end_frame": "video_start_end_model", "reference": "video_reference_model", "text": "video_text_model"}
    values["video_model"] = values.get(active[values["video_generation_mode"]], "")
    return values
