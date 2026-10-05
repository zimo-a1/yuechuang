"""Isolate job/provider configuration across async workers and nested executors."""
import copy
import functools
import hashlib
import inspect
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar, copy_context

from config import Config, CONFIG_CONTEXT
from core.service_settings import service_settings, resolve_service, provider_attr, raw_model, SettingsError, validate_video_options

SERVICE_CONTEXT = ContextVar("yuechuang_services", default=None)


def capture_runtime():
    attributes = copy.deepcopy(CONFIG_CONTEXT.get() or {k: v for k, v in vars(Config).items() if k.isupper()})
    services = copy.deepcopy(SERVICE_CONTEXT.get() or service_settings.runtime())
    fingerprint = hashlib.sha256(json.dumps(Config.as_public_dict(), sort_keys=True).encode()).hexdigest()[:12]
    return {"attributes": attributes, "services": services, "version": f"settings-{services['revision']}/{fingerprint}"}


@contextmanager
def runtime_scope(snapshot):
    config_token = CONFIG_CONTEXT.set(snapshot["attributes"])
    service_token = SERVICE_CONTEXT.set(snapshot["services"])
    try:
        yield
    finally:
        SERVICE_CONTEXT.reset(service_token)
        CONFIG_CONTEXT.reset(config_token)


class ContextThreadPoolExecutor(ThreadPoolExecutor):
    def submit(self, fn, /, *args, **kwargs):
        context = copy_context()
        return super().submit(context.run, fn, *args, **kwargs)


def configured_client(method_name):
    """Bind a client to its creation-time configuration, including worker threads.

    Each named service gets a fresh client under scoped provider settings. Cached
    provider SDKs from another connection can therefore never be reused.
    """
    def decorate(cls):
        original_init = cls.__init__
        original_method = getattr(cls, method_name)
        signature = inspect.signature(original_method)

        @functools.wraps(original_init)
        def init(self, *args, **kwargs):
            self._configuration_snapshot = capture_runtime()
            original_init(self, *args, **kwargs)

        @functools.wraps(original_method)
        def invoke(self, *args, **kwargs):
            bound = signature.bind(self, *args, **kwargs)
            bound.apply_defaults()
            values = dict(bound.arguments)
            values.pop("self")
            model = values.get("model", "")
            snapshot = self._configuration_snapshot
            conn = resolve_service(model, snapshot["services"])
            if method_name == "generate_video":
                mode = "start_end_frame" if values.get("last_image_path") else "reference" if values.get("reference_image_paths") else "first_frame" if values.get("image_path") else "text"
                validate_video_options(model, ratio=values.get("video_ratio"), resolution=values.get("resolution"), duration=values.get("duration"), mode=mode)
            if not conn:
                with runtime_scope(snapshot):
                    return original_method(self, **values)
            if not conn["credentials"] or not all(conn["credentials"].values()):
                raise SettingsError("SERVICE_NOT_CONFIGURED", "所选服务尚未配置完整凭据，请先到设置中填写")
            scoped = copy.deepcopy(snapshot)
            attrs = scoped["attributes"]
            provider = conn["provider"]
            attrs[provider_attr(provider, "base_url")] = conn["base_url"]
            for field, value in conn["credentials"].items():
                attrs[provider_attr(provider, field)] = value
            attrs["CONFIG"]["api_providers"][provider]["enable_proxy"] = conn["enable_proxy"]
            values["model"] = raw_model(model)
            with runtime_scope(scoped):
                return original_method(cls(), **values)

        cls.__init__ = init
        setattr(cls, method_name, invoke)
        return cls
    return decorate
