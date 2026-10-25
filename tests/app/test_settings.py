import dataclasses
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.api.src.settings import Settings


def test_settings_read_canonical_urls_and_explicit_overrides():
    settings = Settings.load(
        {"web_url": "https://dashboard.example.com"},
        environ={
            "FLOCK_WEB_URL": "https://other.example.com",
            "FLOCK_API_PUBLIC_URL": "https://api.example.com",
            "FLOCK_POOL_SIZE": "8",
            "FLOCK_GRAVATAR": "false",
            "FLOCK_CORS_ORIGINS": " https://one.example.com, ,https://two.example.com ",
        },
    )
    assert settings.web_url == "https://dashboard.example.com"
    assert settings.api_public_url == "https://api.example.com"
    assert settings.pool_size == 8
    assert settings.gravatar is False
    assert settings.cors_origins == ["https://one.example.com", "https://two.example.com"]


# The Settings fields the hub's S3 proxy uses.
PROXY_SETTINGS = {
    "database_url",
    "hub_s3_endpoint",
    "hub_s3_region",
    "hub_s3_access_key_id",
    "hub_s3_secret_access_key",
    "hub_bucket",
    "hub_mirror_org",
    "pool_size",
    "log_level",
}


@pytest.mark.parametrize("compose", ["app/compose.yaml", "app/api/compose.dev.yaml"])
@pytest.mark.parametrize("configured", [False, True])
def test_compose_configuration(compose, configured):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose is needed to validate deployment configuration")
    root = Path(__file__).resolve().parents[2]
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("FLOCK_", "POSTGRES_", "APP_", "UI_"))
    }
    if configured:
        env.update(
            FLOCK_WEB_URL="https://dashboard.example.com",
            FLOCK_API_PUBLIC_URL="https://api.example.com",
            FLOCK_API_URL="http://private-api:8080",
            FLOCK_VERSION="test-release",
            FLOCK_POOL_SIZE="8",
            FLOCK_GRAVATAR="false",
            POSTGRES_PASSWORD="test-database-password",
        )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            os.devnull,
            "-f",
            str(root / compose),
            "--profile",
            "hub",
            "config",
            "--format",
            "json",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    services = json.loads(result.stdout)["services"]
    api_env = services["api"]["environment"]
    settings = Settings.load(environ=api_env)
    # Every API option is configurable; DB and files are provided by the stack/image.
    assert set(api_env) == {
        f"FLOCK_{field.name.upper()}"
        for field in dataclasses.fields(Settings)
        if field.name != "files_dir"
    }
    assert settings.pool_size == (8 if configured else Settings.pool_size)
    # The hub's S3 proxy reads the same Settings: the database, the upstream S3 server and the
    # mirror org, set as for the API.
    proxy_env = services["hub-s3"]["environment"]
    assert set(proxy_env) == {f"FLOCK_{name.upper()}" for name in PROXY_SETTINGS}
    assert {k: v for k, v in api_env.items() if k in proxy_env} == proxy_env
    assert settings.gravatar is (not configured)
    password = "test-database-password" if configured else "flock"
    assert services["postgres"]["environment"]["POSTGRES_PASSWORD"] == password
    assert settings.database_url == f"postgresql://flock:{password}@postgres:5432/flock"
    version = "test-release" if configured else "latest"
    assert services["api"]["image"] == f"flock-api:{version}"
    web_url = (
        "https://dashboard.example.com"
        if configured
        else "http://localhost:5173"
        if "dev" in compose
        else "http://localhost:3000"
    )
    assert settings.web_url == web_url
    assert settings.api_public_url == (
        "https://api.example.com" if configured else "http://localhost:8080"
    )
    if "ui" in services:
        ui = services["ui"]
        assert ui["image"] == f"flock-ui:{version}"
        assert ui["build"]["args"] == {"FLOCK_WEB_URL": web_url}
        assert ui["environment"] == {
            "FLOCK_API_URL": "http://private-api:8080" if configured else "http://api:8080",
            "FLOCK_API_PUBLIC_URL": settings.api_public_url,
        }
        assert all("ports" not in service for service in services.values())
    else:
        assert services["api"]["ports"][0]["published"] == "8080"
