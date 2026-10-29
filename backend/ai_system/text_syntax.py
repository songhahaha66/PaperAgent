"""Bounded text scanners for untrusted paper fragments.

Index delimiters once so repeated unclosed openers cannot repeatedly scan the
remaining document. Math lookup is O(n log n); citations and Markdown are O(n).
"""
from bisect import bisect_left
import re

_ENV = r'(?:equation|align|gather|multline|displaymath)\*?'
_MATH_OPEN = re.compile(r'(?=(\$|\\\(|\\\[|\\begin\{' + _ENV + r'\}))')
_MATH_CLOSE = {
    '$$': re.compile(r'(?=\$\$)'),
    '$': re.compile(r'\$'),
    r'\(': re.compile(r'\\\)'),
    r'\[': re.compile(r'\\\]'),
    'environment': re.compile(r'\\end\{' + _ENV + r'\}'),
}


def math_spans(text):
    closes = {key: [(m.start(), m.end()) for m in pattern.finditer(text)]
              for key, pattern in _MATH_CLOSE.items()}
    newlines = [m.start() for m in re.finditer('\n', text)]
    cursor = 0
    for match in _MATH_OPEN.finditer(text):
        start = match.start()
        if start < cursor:
            continue
        opener = match[1]
        keys = ('$$', '$') if text.startswith('$$', start) else (opener,)
        for key in keys:
            key = 'environment' if key.startswith(r'\begin') else key
            after = start + (2 if key == '$$' else len(opener))
            endings = closes[key]
            index = bisect_left(endings, (after, -1))
            if index == len(endings):
                continue
            close, end = endings[index]
            if key == '$':
                newline = bisect_left(newlines, after)
                if close == after or (newline < len(newlines) and newlines[newline] < close):
                    continue
            cursor = close + 2 if key == '$$' else end
            yield start, cursor
            break


def math_blocks(text):
    return [text[start:end] for start, end in math_spans(text)]


def strip_math(text):
    parts, cursor = [], 0
    for start, end in math_spans(text):
        parts.append(text[cursor:start])
        cursor = end
    parts.append(text[cursor:])
    return ''.join(parts)


def native_citations(text):
    """Yield (start, end, keys) for cite/citep/etc, including optional arguments."""
    optional_end, closing = {}, None
    for index in range(len(text) - 1, -1, -1):
        if text[index] == ']':
            closing = index
        elif text[index] == '[' and closing is not None:
            optional_end[index] = optional_end.get(closing + 1, closing + 1)
    groups = {m.start(): m for m in re.finditer(r'\{([^{}]*)\}', text)}
    cursor = 0
    for command in re.finditer(r'\\cite\w*\*?', text):
        if command.start() < cursor:
            continue
        group = groups.get(optional_end.get(command.end(), command.end()))
        if group is not None and group[1]:
            cursor = group.end()
            yield command.start(), cursor, group[1]


def has_markdown(text):
    for line in text.splitlines():
        line = line.lstrip()
        if (line.startswith('```') or re.match(r'#{1,6}\s', line)
                or (line.startswith('|') and line.rstrip().endswith('|') and len(line.rstrip()) > 1)):
            return True
    # A label cannot cross a closing bracket, even when its opener is malformed.
    label = False
    for index, char in enumerate(text):
        if char == ']':
            if label and text.startswith('(', index + 1):
                return True
            label = False
        elif char == '!' and text.startswith('[', index + 1):
            label = True
    return False
