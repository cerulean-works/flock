import pytest

from app.api.src.hub_credentials import (
    KEY_PREFIX,
    TEMP_PREFIX,
    Session,
    make_session_token,
    new_access_key_id,
    read_session_token,
    secret_for,
)

SECRET = "server-secret"


def test_access_key_ids_are_prefixed_and_random():
    first, second = new_access_key_id(KEY_PREFIX), new_access_key_id(KEY_PREFIX)
    assert first.startswith(KEY_PREFIX) and len(first) == 20
    assert first != second
    assert new_access_key_id(TEMP_PREFIX).startswith(TEMP_PREFIX)


def test_secrets_are_derived_per_key_and_kind():
    key = new_access_key_id(KEY_PREFIX)
    assert secret_for(SECRET, key) == secret_for(SECRET, key)
    assert secret_for(SECRET, key) != secret_for("other", key)
    assert secret_for(SECRET, key) != secret_for(SECRET, new_access_key_id(KEY_PREFIX))
    # The same random part as a temporary key gets a different secret.
    assert secret_for(SECRET, key) != secret_for(SECRET, TEMP_PREFIX + key[4:])


def test_session_tokens_round_trip():
    session = Session(7, 2000.0, True, new_access_key_id(TEMP_PREFIX), mirror_writer=True)
    token = make_session_token(SECRET, session)
    assert read_session_token(SECRET, token, now=1000.0) == session


@pytest.mark.parametrize(
    "mangle",
    [
        lambda token: token,  # read with the wrong server secret below
        lambda token: token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1],
        lambda token: "x" + token,
        lambda token: token.partition(".")[0],
        lambda token: "",
        lambda token: "not.a-token",
    ],
)
def test_forged_or_malformed_tokens_are_refused(mangle):
    token = make_session_token(SECRET, Session(7, 2000.0, False, "FLKTX"))
    server_secret = "other" if mangle(token) == token else SECRET
    assert read_session_token(server_secret, mangle(token), now=1000.0) is None


def test_expired_tokens_are_refused():
    token = make_session_token(SECRET, Session(7, 2000.0, False, "FLKTX"))
    assert read_session_token(SECRET, token, now=2000.0) is None
