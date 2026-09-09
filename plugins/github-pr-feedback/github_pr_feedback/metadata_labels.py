"""Configured advisory metadata derived from canonical PR titles and file paths."""
from dataclasses import dataclass
import re


def _title_term_matches(term: str, title: str) -> bool:
    """Match selector edges without requiring a word boundary around punctuation."""
    left = r"\b" if term[:1].isalnum() or term[:1] == "_" else r"(?<!\w)"
    right = r"\b" if term[-1:].isalnum() or term[-1:] == "_" else r"(?!\w)"
    return re.search(left + re.escape(term) + right, title.casefold()) is not None


def _pathname_glob_matches(path: str, pattern: str) -> bool:
    """Shell-style pathname glob where ``*`` does not cross ``/`` and ``**`` does."""
    expression = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            expression.append("(?:[^/]+/)*")
            index += 3
        elif pattern.startswith("**", index):
            expression.append(".*")
            index += 2
        elif pattern[index] == "*":
            expression.append("[^/]*")
            index += 1
        else:
            expression.append(re.escape(pattern[index]))
            index += 1
    return re.fullmatch("".join(expression), path.casefold()) is not None


@dataclass(frozen=True)
class MetadataLabelRule:
    label: str
    repositories: tuple[str, ...]
    title_terms: tuple[str, ...]
    path_patterns: tuple[str, ...]
    color: str
    description: str

    def matches(self, repository, title, paths):
        return repository in self.repositories and (
            any(_title_term_matches(term, title)
                for term in self.title_terms)
            or any(_pathname_glob_matches(path, pattern)
                   for path in paths for pattern in self.path_patterns)
        )


def parse_metadata_rules(raw):
    if not isinstance(raw, list) or len(raw) > 128:
        raise ValueError("metadata_rules must be a list of at most 128 rules")
    result = []
    for rule in raw:
        if not isinstance(rule, dict) or set(rule) != {
            "label", "repositories", "title_terms", "path_patterns", "color", "description"
        }:
            raise ValueError("invalid metadata label rule fields")
        for key in ("repositories", "title_terms", "path_patterns"):
            values = rule[key]
            if not isinstance(values, list) or len(values) > 50 or any(
                not isinstance(v, str) or not v.strip() or len(v) > 200 for v in values
            ):
                raise ValueError("invalid metadata label selectors")
        label = rule["label"]
        if not isinstance(label, str) or not label.strip() or len(label) > 50 or "," in label:
            raise ValueError("invalid metadata label")
        normalized_label = label.casefold()
        if normalized_label.startswith(("status/", "priority/")) or normalized_label == "ci-reviewed":
            raise ValueError("evidence and authority labels cannot be inferred from text or paths")
        if not rule["repositories"] or not (rule["title_terms"] or rule["path_patterns"]):
            raise ValueError("metadata rules require explicit repositories and selectors")
        if not isinstance(rule["color"], str) or not re.fullmatch('[0-9a-fA-F]{6}',rule['color']):
            raise ValueError("invalid metadata label color")
        if (not isinstance(rule['description'], str)
                or not rule['description'].strip()
                or len(rule['description']) > 100):
            raise ValueError("invalid metadata label description")
        result.append(MetadataLabelRule(label, tuple(rule['repositories']),
            tuple(v.casefold() for v in rule['title_terms']),
            tuple(v.casefold() for v in rule['path_patterns']),rule['color'],rule['description']))
    return tuple(result)
