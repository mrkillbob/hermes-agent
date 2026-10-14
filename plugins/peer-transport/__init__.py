"""peer-transport plugin: durable peer mailbox plus an opt-in Claude launcher adapter.

``transport`` is the SQLite mailbox (identity, dedup, delivery facts). ``adapters`` holds the stream
and queue endpoints and the Claude launcher. Nothing here is wired into a turn, hook, tool or command;
other code imports the modules it needs, so ``register`` has nothing to register.
"""


def register(ctx) -> None:
    return None
