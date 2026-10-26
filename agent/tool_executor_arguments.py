"""Parse model tool arguments without implicit repair or coercion."""
import json
from typing import Any, Optional


def _parse_tool_arguments(raw_arguments: Any) -> tuple[dict, Optional[str]]:
    """Parse model-emitted arguments without repairing or coercing them."""
    try:
        arguments = json.loads(raw_arguments)
    except (json.JSONDecodeError, TypeError):
        arguments = None
    if isinstance(arguments, dict):
        return arguments, None
    return {}, json.dumps(
        {"error": "Invalid tool arguments", "message": "Tool arguments must be a valid JSON object; tool was not executed."},
        ensure_ascii=False,
    )
