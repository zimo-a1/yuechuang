"""Last-mile secret redaction, applied before log records enter the queue."""

import logging
import re


class PrivacyFilter(logging.Filter):
    def __init__(self, secrets=None):
        super().__init__()
        self._secrets = secrets

    def filter(self, record):
        if self._secrets is None:
            from config import Config
            secrets = [getattr(Config, key) for key in dir(Config)
                       if any(token in key.upper() for token in ("API_KEY", "ACCESS_KEY", "SECRET", "PASSWORD", "TOKEN"))]
        else:
            secrets = self._secrets
        message = record.getMessage()
        for value in sorted((v for v in secrets if isinstance(v, str) and len(v) >= 6), key=len, reverse=True):
            message = message.replace(value, "[REDACTED]")
        message = re.sub(r"(?i)\bBearer\s+[^\s\"',;]+", "Bearer [REDACTED]", message)
        message = re.sub(r"(?i)((?:api[_-]?key|access[_-]?key|password|secret|access[_-]?token)\s*[\"']?\s*[:=]\s*[\"']?)[^\s\"',;}]+",
                         r"\1[REDACTED]", message)
        # Signed URLs / userinfo can carry credentials and user-provided text.
        message = re.sub(r"https?://[^\s<>\"']+", "[URL]", message)
        message = re.sub(r"(/[^\s\"?]*)\?[^\s\"]*", r"\1?[REDACTED]", message)
        if record.exc_info and record.exc_info[0] is not None:
            message += f" ({record.exc_info[0].__name__})"
        record.msg, record.args = message, ()
        record.exc_info = record.exc_text = record.stack_info = None
        return True
