"""The few Flock API calls the `flock` command makes, over httpx."""

from typing import Any

import click
import httpx

TRANSPORT: httpx.BaseTransport | None = None
"""Replaces httpx's network transport when set, for tests."""


class Api:
    """
    A Flock API, as one user (or anonymously).

    Args:
        host (`str`):
            The API's URL.
        token (`str`, *optional*):
            A CLI token (or a wandb key, for `/graphql`), sent as `Authorization: Bearer <token>`.
    """

    def __init__(self, host: str, token: str | None = None) -> None:
        self.host = host
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.client = httpx.Client(
            base_url=host, headers=headers, timeout=30.0, transport=TRANSPORT
        )

    def request(self, method: str, path: str, body: Any = None) -> httpx.Response:
        """Sends a request. A server that can't be reached raises `click.ClickException`."""
        try:
            return self.client.request(method, f"/api/v1/{path}", json=body)
        except httpx.HTTPError as error:
            raise click.ClickException(f"Couldn't reach {self.host}: {error}") from None

    def get(self, path: str) -> httpx.Response:
        return self.request("GET", path)

    def post(self, path: str, body: Any = None) -> httpx.Response:
        return self.request("POST", path, body)


def error_of(response: httpx.Response) -> str:
    """Flock's reason for refusing a request: its `error`, else the status."""
    try:
        body = response.json()
    except ValueError:
        body = None
    message = body.get("error") if isinstance(body, dict) else None
    return message or f"{response.status_code} {response.reason_phrase}"
