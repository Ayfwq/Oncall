from __future__ import annotations

import re

_PATTERNS = [
    re.compile(r'(?i)(password|passwd|pwd|token|secret|api[_-]?key)\s*[:=]\s*([^\s,;]+)'),
    re.compile(r'(?i)authorization:\s*bearer\s+[^\s]+'),
    re.compile(r'(?i)(cookie|set-cookie):\s*[^\n]+'),
]


def redact_text(text: str) -> str:
    result = text
    for p in _PATTERNS:
        if p.groups >= 2:
            result = p.sub(lambda m: f'{m.group(1)}=[REDACTED]', result)
        else:
            result = p.sub('[REDACTED]', result)
    return result
