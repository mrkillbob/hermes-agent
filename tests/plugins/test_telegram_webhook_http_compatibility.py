"""PTB webhook compatibility with the patched Tornado HTTP parser.

Only tiny ordinary requests reach a loopback fixture; Bot API calls are absent.
"""

import asyncio
import json
from unittest.mock import Mock

import pytest

pytest.importorskip("telegram")
pytest.importorskip("tornado")

from telegram import Bot
from telegram.ext._utils.webhookhandler import WebhookAppClass
from tornado.httpclient import HTTPRequest
from tornado.testing import AsyncHTTPTestCase, gen_test


class TestTelegramWebhookHTTPCompatibility(AsyncHTTPTestCase):
    def get_app(self):
        self.updates = asyncio.Queue()
        self.secret = "isolated-webhook-fixture"
        return WebhookAppClass("/telegram", Mock(spec=Bot), self.updates, self.secret)

    @staticmethod
    def update_body():
        return json.dumps({
            "update_id": 7,
            "message": {
                "message_id": 8, "date": 0,
                "chat": {"id": 42, "type": "private"},
                "text": "hello 世界",
            },
        }, ensure_ascii=False).encode()

    @gen_test
    async def test_authenticated_small_chunked_update_reaches_queue_unchanged(self):
        body = self.update_body()

        async def produce(write):
            await write(body[:32])
            await write(body[32:])

        response = await self.http_client.fetch(HTTPRequest(
            self.get_url("/telegram"), method="POST", body_producer=produce,
            headers={
                "Content-Type": "application/json",
                "X-Telegram-Bot-Api-Secret-Token": self.secret,
            },
        ))

        assert response.code == 200
        update = self.updates.get_nowait()
        assert update.update_id == 7
        assert update.message.chat.id == 42
        assert update.message.text == "hello 世界"
        assert self.updates.empty()

    def test_missing_or_wrong_secret_refuses_update(self):
        for secret in (None, "different-fixture-secret"):
            headers = {"Content-Type": "application/json"}
            if secret is not None:
                headers["X-Telegram-Bot-Api-Secret-Token"] = secret
            response = self.fetch(
                "/telegram", method="POST", body=self.update_body(),
                headers=headers, raise_error=False,
            )
            assert response.code == 403
            assert self.updates.empty()
