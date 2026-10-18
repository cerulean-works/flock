import time

import jwt

ME = "/api/v1/me"


def login(server, email: str, password: str):
    return server.anonymous().post("/api/v1/auth/login", {"email": email, "password": password})


def test_the_first_signup_becomes_the_super_admin(make_tracker):
    server = make_tracker(bootstrap=False)
    anon = server.anonymous()
    assert anon.get("/api/v1/auth/config").json() == {"signup_open": True, "google": False}
    assert anon.get("/api/v1/projects").status == 401
    signup = "/api/v1/auth/signup"
    assert anon.post(signup, {"email": "a@x.io", "name": "A", "password": "short"}).status == 422
    assert (
        anon.post(signup, {"email": "nope", "name": "A", "password": "long enough"}).status == 422
    )

    created = anon.post(signup, {"email": " Root@X.io ", "name": "Root", "password": "rootroot"})
    assert created.status == 201, created.body
    body = created.json()
    assert body["user"] | {"created_at": 0, "id": 0} == {
        "id": 0,
        "email": "root@x.io",
        "name": "Root",
        "is_superadmin": True,
        "has_password": True,
        "google": False,
        "default_org": "flock",  # created for the first user, as there was no org
        "orgs": [{"name": "flock", "role": "admin"}],
        "created_at": 0,
    }
    root = server.client(token=body["access_token"])
    assert root.get(ME).json()["email"] == "root@x.io"
    assert root.get("/api/v1/projects").json() == {"projects": []}

    again = anon.post(signup, {"email": "b@x.io", "name": "B", "password": "password"})
    assert again.status == 403  # only through invites from now on
    assert anon.get("/api/v1/auth/config").json()["signup_open"] is False
    assert login(server, "ROOT@x.io", "rootroot").status == 200


def test_login_refresh_and_logout(tracker):
    alice = tracker.add_user("alice@lab.test")
    assert login(tracker, alice.email, "wrong password").status == 401
    assert login(tracker, "nobody@lab.test", alice.password).status == 401

    tokens = login(tracker, alice.email, alice.password).json()
    assert tokens["token_type"] == "bearer"
    assert tokens["user"]["orgs"] == [{"name": "flock", "role": "member"}]
    api = tracker.client(token=tokens["access_token"])
    assert api.get(ME).json()["id"] == alice.id

    anon = tracker.anonymous()
    fresh = anon.post("/api/v1/auth/refresh", {"refresh_token": tokens["refresh_token"]}).json()
    assert fresh["refresh_token"] != tokens["refresh_token"]
    reused = anon.post("/api/v1/auth/refresh", {"refresh_token": tokens["refresh_token"]})
    assert reused.status == 401  # each refresh token works once
    renewed = tracker.client(token=fresh["access_token"])
    assert renewed.get(ME).status == 200

    assert alice.api.post("/api/v1/auth/logout", {}).status == 400  # API keys have no session
    assert renewed.post("/api/v1/auth/logout", {}).status == 204
    assert renewed.get(ME).status == 401  # the session ended, not just when the token expires
    assert api.get(ME).status == 401  # same session
    gone = anon.post("/api/v1/auth/refresh", {"refresh_token": fresh["refresh_token"]})
    assert gone.status == 401


def test_access_tokens_expire_and_must_be_ours(make_tracker):
    server = make_tracker(access_token_ttl=1.0)
    alice = server.add_user("alice@lab.test")
    tokens = alice.signup
    header, payload, signature = tokens["access_token"].split(".")
    tampered = f"{header}.{payload}.{signature[::-1]}"
    assert server.client(token=tampered).get(ME).status == 401
    claims = jwt.decode(tokens["access_token"], options={"verify_signature": False})
    forged = jwt.encode(
        claims | {"exp": time.time() + 3600}, "another secret that is long enough", "HS256"
    )
    assert server.client(token=forged).get(ME).status == 401

    api = server.client(token=tokens["access_token"])
    assert api.get(ME).status == 200
    time.sleep(2.1)
    assert api.get(ME).status == 401
    refreshed = server.anonymous().post(
        "/api/v1/auth/refresh", {"refresh_token": tokens["refresh_token"]}
    )
    assert server.client(token=refreshed.json()["access_token"]).get(ME).status == 200


def test_changing_the_password_ends_other_sessions(tracker):
    alice = tracker.add_user("alice@lab.test")
    here = tracker.client(token=login(tracker, alice.email, alice.password).json()["access_token"])
    there = tracker.client(token=login(tracker, alice.email, alice.password).json()["access_token"])
    change = "/api/v1/me/password"
    assert here.post(change, {"current": "wrong", "new": "new password"}).status == 403
    assert here.post(change, {"current": alice.password, "new": "x"}).status == 422
    assert here.post(change, {"current": alice.password, "new": "new password"}).status == 204
    assert here.get(ME).status == 200
    assert there.get(ME).status == 401
    assert alice.api.get(ME).status == 200  # API keys stay valid
    assert login(tracker, alice.email, alice.password).status == 401
    assert login(tracker, alice.email, "new password").status == 200


def test_api_keys(tracker):
    alice = tracker.add_user("alice@lab.test")
    keys = "/api/v1/me/api-keys"
    created = alice.api.post(keys, {"name": "laptop"})
    assert created.status == 201
    key = created.json()["key"]
    assert key.startswith("flock-") and len(key) == 54  # the shape the wandb SDK accepts
    listed = alice.api.get(keys).json()["keys"]
    assert [(k["name"], k["prefix"]) for k in listed] == [
        ("tests", alice.api_key[:12]),
        ("laptop", key[:12]),
    ]
    assert all("key" not in k for k in listed)

    for headers in ({"x-api-key": key}, {"Authorization": f"Bearer {key}"}):
        assert tracker.anonymous().get(ME, headers=headers).json()["id"] == alice.id
    key_id = created.json()["id"]
    assert tracker.client().delete(f"{keys}/{key_id}").status == 404  # not the admin's key
    assert alice.api.delete(f"{keys}/{key_id}").status == 204
    assert tracker.client(api_key=key).get(ME).status == 401
    assert alice.api.delete(f"{keys}/{key_id}").status == 404


def test_me(tracker):
    alice = tracker.add_user("alice@lab.test")
    assert tracker.client().post("/api/v1/orgs", {"name": "lab"}).status == 201
    assert alice.api.patch(ME, {"default_org": "lab"}).status == 404  # not a member
    updated = alice.api.patch(ME, {"name": "Alice", "default_org": "flock"}).json()
    assert (updated["name"], updated["default_org"]) == ("Alice", "flock")
    assert alice.api.patch(ME, {"email": "x@y.z"}).status == 422


def test_accounts_survive_a_restart(make_tracker):
    server = make_tracker()
    alice = server.add_user("alice@lab.test")
    tokens = login(server, alice.email, alice.password).json()
    server.stop()
    restarted = make_tracker(reuse=server)
    assert restarted.client(api_key=alice.api_key).get(ME).json()["email"] == alice.email
    assert restarted.client(token=tokens["access_token"]).get(ME).status == 200
    refreshed = restarted.anonymous().post(
        "/api/v1/auth/refresh", {"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status == 200
    assert login(restarted, alice.email, alice.password).status == 200


def test_configured_jwt_secret_signs_tokens(make_tracker):
    secret = "configured-secret-0123456789abcdef"
    server = make_tracker(jwt_secret=secret)
    alice = server.add_user("alice@lab.test")
    claims = jwt.decode(alice.signup["access_token"], secret, ["HS256"], issuer="flock-tracker")
    assert claims["sub"] == str(alice.id)
