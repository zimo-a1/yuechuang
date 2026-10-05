"""Bounded text-only OpenAI-compatible transport using existing service settings.

Exactly one SDK request, no hidden retry; public usage never contains credentials.
"""
import os
import time
from urllib.parse import urlsplit
from core.ip_library import digest
from core.service_settings import resolve_service, raw_model, provider_attr, SettingsError


def bounded_env(name, default, low, high):
    try:
        return max(low, min(high, int(os.environ.get(name, default))))
    except ValueError:
        return default


def request_options(connection):
    """Explicit structured output; do not spend the novel budget on default thinking."""
    if connection['provider'] == 'deepseek':
        return {'response_format': {'type': 'json_object'},
                'extra_body': {'thinking': {'type': 'disabled'}}}
    return {}


def text_connection(model, runtime):
    from models.config_model import MODEL_CONFIG
    info = MODEL_CONFIG['models'].get(raw_model(model), {})
    if 'llm' not in info.get('type', []):
        raise SettingsError('TEXT_MODEL_REQUIRED', '请在改编设置中选择文本模型')
    conn = resolve_service(model, runtime['services'])
    attrs = runtime['attributes']
    if conn:
        provider, base, key = conn['provider'], conn['base_url'], conn['credentials'].get('api_key', '')
    else:
        provider = info.get('provider', '')
        base, key = attrs.get(provider_attr(provider, 'base_url'), ''), attrs.get(provider_attr(provider, 'api_key'), '')
    if provider not in {'deepseek', 'openai', 'agnes'}:
        raise SettingsError('NOVEL_PROVIDER_UNSUPPORTED', '长篇改编首版支持 DeepSeek、OpenAI 和 Agnes 的文本连接，请在项目配置中选择')
    if not key or not base:
        raise SettingsError('SERVICE_NOT_CONFIGURED', '文本服务地址或凭据未配置，请先到设置中填写')
    host = urlsplit(base).hostname
    if not host:
        raise SettingsError('SERVICE_NOT_CONFIGURED', '文本服务地址无效')
    timeout = bounded_env('YC_NOVEL_TIMEOUT', 180, 10, 600)
    max_tokens = bounded_env('YC_NOVEL_MAX_TOKENS', 12000, 1000, 20000)
    proxy = attrs.get('PROXY') if (conn['enable_proxy'] if conn else attrs.get('CONFIG', {}).get('api_providers', {}).get(provider, {}).get('enable_proxy')) else None
    # Include endpoint/config revision and the selected model, never the key itself.
    fingerprint = digest({'model':model, 'base':base, 'version':runtime['version'], 'timeout':timeout, 'max_tokens':max_tokens})
    return {'model':raw_model(model), 'base_url':base, 'api_key':key, 'provider':provider, 'host':host,
            'timeout':timeout, 'max_tokens':max_tokens, 'proxy':proxy, 'fingerprint':fingerprint}


def query_novel(prompt, connection):
    import httpx
    from openai import OpenAI
    started = time.monotonic()
    http = httpx.Client(proxy=connection['proxy'], timeout=connection['timeout'])
    try:
        with OpenAI(api_key=connection['api_key'], base_url=connection['base_url'],
                    timeout=connection['timeout'], max_retries=0, http_client=http) as client:
            response = client.chat.completions.create(model=connection['model'], stream=False,
                max_tokens=connection['max_tokens'], messages=[{'role':'user', 'content':prompt}],
                **request_options(connection))
            usage = response.usage
            choice = response.choices[0] if response.choices else None
            message = choice.message if choice else None
            finish = getattr(choice, 'finish_reason', None)
            # Only enum values and counts are persisted; never response/reasoning text.
            allowed_finishes = {'stop', 'length', 'content_filter', 'tool_calls',
                                'insufficient_system_resource', 'aborted'}
            details = getattr(usage, 'completion_tokens_details', None)
            reasoning_tokens = getattr(details, 'reasoning_tokens', None)
            text = getattr(message, 'content', None) or ''
            metrics = {'input_tokens':getattr(usage,'prompt_tokens',None), 'output_tokens':getattr(usage,'completion_tokens',None),
                       'elapsed_seconds':round(time.monotonic()-started, 3), 'cost':None,
                       'finish_reason':finish if finish in allowed_finishes else 'unknown',
                       'reasoning_tokens':reasoning_tokens if type(reasoning_tokens) is int and reasoning_tokens >= 0 else None,
                       'has_reasoning':bool(getattr(message, 'reasoning_content', None)),
                       'content_characters':len(text)}
            return text, metrics
    finally:
        http.close()
