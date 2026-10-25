"""The few REST calls `flock.hub` makes to the Flock API."""

from typing import Any

import httpx


class HubError(Exception):
    """A request to the Flock API failed. `status` is the HTTP status, or `None` without an answer; `body` is the JSON error body, if any."""

    def __init__(
        self, message: str, status: int | None = None, body: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.body = body or {}


class DatasetNotFound(HubError, LookupError):
    """The dataset doesn't exist, or the caller can't see it."""


class HubClient:
    """
    A small httpx client for the hub's endpoints under `/api/v1`.

    Args:
        api_url (`str`):
            The Flock API's base URL.
        api_key (`str`):
            An API key, sent as `Authorization: Bearer <key>`.
        transport (`httpx.BaseTransport`, *optional*):
            Replaces the network transport, for tests.
        timeout (`float`, *optional*, defaults to 30):
            Seconds to wait for each request.
    """

    def __init__(
        self,
        api_url: str,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._http = httpx.Client(
            base_url=api_url.rstrip("/") + "/api/v1",
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
            timeout=timeout,
        )

    def credentials(self, read_only: bool = False) -> dict[str, Any]:
        """Temporary S3 credentials: `endpoint`, `region`, the keys, `session_token` and `expires_at`."""
        return self._request("POST", "/hub/credentials", json={"read_only": read_only})

    def create_dataset(
        self, org: str, name: str, description: str = "", visibility: str = "private"
    ) -> dict[str, Any]:
        """Creates `org/name` and returns its `DatasetInfo`."""
        body = {"name": name, "description": description, "visibility": visibility}
        return self._request("POST", f"/datasets/{org}", json=body)

    def get_dataset(self, org: str, name: str) -> dict[str, Any]:
        """
        Returns the `DatasetInfo` of `org/name`.

        Raises:
            [`DatasetNotFound`]: if the API answers 404.
        """
        return self._request("GET", f"/datasets/{org}/{name}")

    def set_version_message(
        self, org: str, name: str, version: int, message: str
    ) -> dict[str, Any]:
        """Sets the message of a version whose commit couldn't carry one, and returns the version."""
        return self._request(
            "PATCH", f"/datasets/{org}/{name}/versions/{version}", json={"message": message}
        )

    def mirrors(self, repo: str, config: str | None = None) -> dict[str, Any]:
        """The mirror dataset of `repo` (and `config`) and its imports so far: `{"dataset", "mirrors": [...]}`."""
        params = {"repo": repo, **({"config": config} if config else {})}
        return self._request("GET", "/hub/mirrors", params=params)

    def request_mirror(
        self, repo: str, config: str | None = None, revision: str | None = None
    ) -> dict[str, Any]:
        """
        Asks the server to mirror `repo` at `revision`, or finds the mirror it already has.

        Returns:
            `dict`: the mirror, `{id, state, repo, config, sha, dataset, version, error, progress}`.

        Raises:
            [`HubError`]: with status 422 and the `flock-hub import` command in `body["command"]` if the dataset is too large for the server to import.
        """
        body = {"repo": repo, "config": config, "revision": revision}
        return self._request("POST", "/hub/mirrors", json=body)

    def get_mirror(self, mirror_id: int) -> dict[str, Any]:
        """A mirror's state, as [`request_mirror`] returns it."""
        return self._request("GET", f"/hub/mirrors/{mirror_id}")

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise HubError(f"{method} {path} failed: {e}") from e
        if response.is_success:
            return response.json()
        try:
            body = response.json()
            message = body.get("error") or response.text
        except ValueError, AttributeError:
            body, message = None, response.text
        not_found = response.status_code == 404 and path.startswith("/datasets/")
        error = DatasetNotFound if not_found else HubError
        raise error(
            f"{method} {path}: {response.status_code} {message}",
            response.status_code,
            body if isinstance(body, dict) else None,
        )
