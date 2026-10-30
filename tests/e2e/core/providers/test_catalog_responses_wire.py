"""The catalog's Responses dialect must satisfy the client SDK's wire schema."""

import pytest
from openai.types.responses import ResponseStreamEvent
from pydantic import TypeAdapter

from tests.fakes.providers.catalog_fake import CatalogFake, _responses_events


@pytest.mark.parametrize("call_id", [None, "call_catalog"])
def test_catalog_responses_events_satisfy_sdk_schema(call_id):
    fake = CatalogFake(tool_name="read_file", tool_args={"path": "/fixture"})
    adapter = TypeAdapter(ResponseStreamEvent)
    events = _responses_events(fake, "CATALOG-TURN-COMPLETE", call_id)

    for event in events:
        parsed = adapter.validate_python(event)
        assert parsed.type == event["type"]

    completed = adapter.validate_python(events[-1])
    assert completed.response.status == "completed"
    output = completed.response.output[0]
    if call_id:
        assert output.call_id == call_id
        assert output.name == fake.tool_name
    else:
        assert output.content[0].text == "CATALOG-TURN-COMPLETE"
