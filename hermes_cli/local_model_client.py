"""Local Models over the existing profile-bound native HTTP gateway transport."""
from __future__ import annotations

import json
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class LocalModelClientError(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LocalModelClient:
    def __init__(self, home, endpoint):
        self.home, self.endpoint = home, endpoint
        # Neither environment proxies nor redirects may receive a local bootstrap ticket.
        self._http = build_opener(ProxyHandler({}), _NoRedirect())

    def get(self, path):
        return self._request('GET', path)

    def post(self, path, body):
        return self._request('POST', path, body)

    def _request(self, method, path, body=None):
        from hermes_cli.gateway_client import _session_ticket
        from hermes_cli.dashboard_auth.native_http import HEADER

        try:
            # Tickets are single-use and remain bound to the selected profile and instance.
            ticket = _session_ticket(self.home, self.endpoint, purpose='native-http')
            request = Request(self.endpoint.api_origin + '/api/local-models/' + path,
                              data=None if body is None else json.dumps(body).encode(),
                              headers={HEADER: ticket, 'Content-Type': 'application/json'}, method=method)
            with self._http.open(request, timeout=30) as response:
                payload = response.read(1048577)
                if len(payload) > 1048576:
                    raise LocalModelClientError('Gateway response exceeded the setup limit')
                result = json.loads(payload)
                if not isinstance(result, dict):
                    raise LocalModelClientError('Invalid gateway setup response')
                return result
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read(8192)).get('detail')
            except (ValueError, AttributeError, OSError, HTTPException):
                detail = None
            raise LocalModelClientError(str(detail or f'Gateway refused setup (HTTP {exc.code})')) from exc
        except (URLError, OSError, ValueError, HTTPException) as exc:
            # A lost POST response is ambiguous. Never retry or resubmit a setup job here.
            raise LocalModelClientError('Gateway connection failed; setup outcome is unknown. '
                                        'Check Local Models jobs before starting another setup.') from exc
