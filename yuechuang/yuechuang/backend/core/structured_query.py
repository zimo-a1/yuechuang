"""One bounded retry loop for output decoding, shape and domain validation."""

import logging
import os

from core.output_validation import ModelOutputError, decode_json
from prompts.loader import load_prompt

logger = logging.getLogger(__name__)


def max_output_attempts():
    try:
        value = int(os.environ.get('YC_STRUCTURED_MAX_ATTEMPTS', '3'))
    except ValueError:
        value = 3
    return max(1, min(value, 3))


async def query_structured(query, prompt, validate, *, check_cancel, label, on_retry=None, attempts=None):
    limit = min(max_output_attempts(), max(1, attempts)) if attempts is not None else max_output_attempts()
    current_prompt = prompt
    for attempt in range(1, limit + 1):
        check_cancel()
        # Provider/network/initialization errors propagate; no multiplicative outer transport retry.
        raw = await query(current_prompt)
        check_cancel()
        try:
            result = validate(decode_json(raw))
        except ModelOutputError as exc:
            diagnostic = exc.diagnostic
        except ValueError:
            diagnostic = 'domain_constraints'
        else:
            return result
        logger.warning('Structured output rejected label=%s attempt=%d/%d reason=%s',
                       label, attempt, limit, diagnostic)
        if attempt == limit:
            raise ModelOutputError(diagnostic)
        check_cancel()
        if on_retry:
            on_retry(attempt + 1, limit)
        current_prompt = prompt + '\n\n' + load_prompt('common', 'repair_structure', 'zh').format(
            diagnostic=diagnostic,
        )
    raise AssertionError('unreachable')
