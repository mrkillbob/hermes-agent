"""Authenticated handler extensions; caller authenticates before dispatch here."""

import json
from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

from hermes_cli.fleet_protocol import ClaimRequest, MAX_MESSAGE_BYTES

PREFIX = "/v2/fleet/claim-recovery/"


def get(handler):
    parsed = urlsplit(handler.path)
    if not parsed.path.startswith(PREFIX):
        return False
    try:
        if parsed.path == PREFIX + "capabilities" and not parsed.query:
            result = handler.fleet.store.claim_recovery_capabilities()
        elif parsed.path == PREFIX + "outcome":
            if len(parsed.query.encode()) > MAX_MESSAGE_BYTES * 3:
                raise ValueError("recovery binding too large")
            fields = parse_qs(parsed.query, strict_parsing=True, max_num_fields=1)
            if set(fields) != {"request"} or len(fields["request"]) != 1:
                raise ValueError("exact recovery binding required")
            request = ClaimRequest.from_dict(json.loads(fields["request"][0]))
            result = handler.fleet.store.lookup_claim_request(request).to_dict()
        else:
            handler._respond(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return True
        handler._respond(HTTPStatus.OK, result)
    except (ValueError, TypeError, KeyError):
        handler._respond(HTTPStatus.BAD_REQUEST, {"error": "invalid recovery binding"})
    return True


def post(handler, path):
    if not path.startswith(PREFIX):
        return False
    action = path[len(PREFIX) :]
    if action not in {"cutover", "claim", "resolve"}:
        handler._respond(HTTPStatus.NOT_FOUND, {"error": "not found"})
        return True
    body = handler._json_body()
    if action == "cutover":
        if set(body) != {"expected_store_id"}:
            raise ValueError("exact store identity required")
        result = handler.fleet.store.establish_claim_cutover(body["expected_store_id"])
    else:
        request = ClaimRequest.from_dict(body)
        method = (
            handler.fleet.store.claim_request
            if action == "claim"
            else handler.fleet.store.resolve_claim_request
        )
        result = method(request).to_dict()
    handler._respond(HTTPStatus.OK, result)
    return True
