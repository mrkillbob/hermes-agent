"""Equivalent source-token context using positions instead of growing line copies."""
from bisect import bisect_left
import re


class SourceLexicalContext:
    def __init__(self, text):
        self.text = text
        self.newlines = [-1, *(index for index, char in enumerate(text) if char == '\n'), len(text)]

    def line_bounds(self, start, end):
        return (self.newlines[bisect_left(self.newlines, start)-1]+1,
                self.newlines[bisect_left(self.newlines, end)])

    def identifier_context(self, match):
        text = self.text
        start, end = match.span()
        line_start, line_end = self.line_bounds(start, end)
        trimmed = start
        while trimmed > line_start and text[trimmed-1].isspace():
            trimmed -= 1
        previous = text[trimmed-1] if trimmed > line_start else ''
        if previous in ("'", '"'):
            return False
        if trimmed < start:
            for keyword in ('def', 'class'):
                beginning = trimmed-len(keyword)
                if (beginning >= line_start and text[beginning:trimmed] == keyword
                        and (beginning == line_start or re.match(r'\w', text[beginning-1]) is None)):
                    return True
        following = end
        while following < line_end and text[following].isspace():
            following += 1
        after = text[following] if following < line_end else ''
        at_line_assignment = start == line_start and after == '='
        return (previous not in ('.', "'", '"')
                and (at_line_assignment or (start > line_start and text[start-1] in ' \t([{,:;'))
                and after in ('=', ',', '.', ';', ':', ')', '(', ']', '}'))
