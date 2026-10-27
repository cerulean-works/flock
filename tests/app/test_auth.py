import time

import jwt

ME = "/api/v1/me"


def login(server, email: str, password: str):
    return server.anonymous().post("/api/v1/auth/login", {"email": email, "password": password})


def test_the_first_signup_becomes_the_super_admin(make_flock):
    server = make_flock(bootstrap=False)
    anon = server.anonymous()
    config = anon.get("/api/v1/auth/config").json()
    assert config == {"signup_open": True, "google": False, "s3": None}
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
        "default_org": None,
        "orgs": [],
        "created_at": 0,
        "avatar_url": None,  # Gravatar is off in tests
        "avatar_source": None,
    }
    root = server.client(token=body["access_token"])
    assert root.get(ME).json()["email"] == "root@x.io"
    assert root.get("/api/v1/projects").json() == {"projects": []}

    again = anon.post(signup, {"email": "b@x.io", "name": "B", "password": "password"})
    assert again.status == 403  # only through invites from now on
    assert anon.get("/api/v1/auth/config").json()["signup_open"] is False
    assert login(server, "ROOT@x.io", "rootroot").status == 200


def test_setting_up_before_any_account_exists(make_flock):
    """The dashboard's setup: signing in first is refused like any wrong password (nobody to
    sign in as), then the super admin signs up without an org and creates its first one."""
    server = make_flock(bootstrap=False)
    anon = server.anonymous()
    refused = login(server, "root@x.io", "rootroot")
    assert refused.status == 401, refused.body
    assert refused.json()["error"] == "invalid email or password"
    assert anon.get("/api/v1/auth/config").json()["signup_open"] is True

    created = anon.post(
        "/api/v1/auth/signup",
        {"email": "root@x.io", "name": "Root", "password": "rootroot"},
    )
    assert created.status == 201, created.body
    user = created.json()["user"]
    assert (user["is_superadmin"], user["orgs"], user["default_org"]) == (True, [], None)
    root = server.client(token=created.json()["access_token"])
    assert root.get("/api/v1/orgs").json() == {"orgs": []}  # setup's second step comes next

    # Leaving setup after the account step must keep the first organization unnamed.
    server.stop()
    server = make_flock(reuse=server, bootstrap=False)
    signed_in = login(server, "root@x.io", "rootroot")
    assert signed_in.status == 200
    root = server.client(token=signed_in.json()["access_token"])
    assert root.get("/api/v1/orgs").json() == {"orgs": []}
    assert root.post("/api/v1/orgs", {"name": "bad/name"}).status == 422
    assert root.get("/api/v1/orgs").json() == {"orgs": []}

    made = root.post("/api/v1/orgs", {"name": "cerulean"})
    assert made.status == 201, made.body
    me = root.patch(ME, {"default_org": "cerulean"})
    assert me.status == 200, me.body
    assert me.json()["default_org"] == "cerulean"
    assert me.json()["orgs"] == [{"name": "cerulean", "display_name": "cerulean", "role": "admin"}]
    assert list(server.store.orgs) == ["cerulean"]
    assert login(server, "root@x.io", "rootroot").status == 200


def test_login_refresh_and_logout(flock):
    alice = flock.add_user("alice@lab.test")
    assert login(flock, alice.email, "wrong password").status == 401
    assert login(flock, "nobody@lab.test", alice.password).status == 401

    tokens = login(flock, alice.email, alice.password).json()
    assert tokens["token_type"] == "bearer"
    assert tokens["user"]["orgs"] == [{"name": "flock", "display_name": "flock", "role": "member"}]
    api = flock.client(token=tokens["access_token"])
    assert api.get(ME).json()["id"] == alice.id

    anon = flock.anonymous()
    fresh = anon.post("/api/v1/auth/refresh", {"refresh_token": tokens["refresh_token"]}).json()
    assert fresh["refresh_token"] != tokens["refresh_token"]
    reused = anon.post("/api/v1/auth/refresh", {"refresh_token": tokens["refresh_token"]})
    assert reused.status == 401  # each refresh token works once
    renewed = flock.client(token=fresh["access_token"])
    assert renewed.get(ME).status == 200

    assert renewed.post("/api/v1/auth/logout", {}).status == 204
    assert renewed.get(ME).status == 401  # the session ended, not just when the token expires
    assert api.get(ME).status == 401  # same session
    gone = anon.post("/api/v1/auth/refresh", {"refresh_token": fresh["refresh_token"]})
    assert gone.status == 401


def test_access_tokens_expire_and_must_be_ours(make_flock):
    server = make_flock(access_token_ttl=1.0)
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


def test_changing_the_password_ends_other_sessions(flock):
    alice = flock.add_user("alice@lab.test")
    here = flock.client(token=login(flock, alice.email, alice.password).json()["access_token"])
    there = flock.client(token=login(flock, alice.email, alice.password).json()["access_token"])
    change = "/api/v1/me/password"
    assert here.post(change, {"current": "wrong", "new": "new password"}).status == 403
    assert here.post(change, {"current": alice.password, "new": "x"}).status == 422
    assert here.post(change, {"current": alice.password, "new": "new password"}).status == 204
    assert here.get(ME).status == 200
    assert there.get(ME).status == 401
    assert alice.api.get(ME).status == 401  # CLI sessions end too
    assert alice.api.graphql("query Viewer { viewer { id } }").status == 200  # wandb keys stay
    assert login(flock, alice.email, alice.password).status == 401
    assert login(flock, alice.email, "new password").status == 200


def test_me(flock):
    alice = flock.add_user("alice@lab.test")
    assert flock.client().post("/api/v1/orgs", {"name": "lab"}).status == 201
    assert alice.api.patch(ME, {"default_org": "lab"}).status == 404  # not a member
    updated = alice.api.patch(ME, {"name": "Alice", "default_org": "flock"}).json()
    assert (updated["name"], updated["default_org"]) == ("Alice", "flock")
    assert alice.api.patch(ME, {"email": "x@y.z"}).status == 422


def test_accounts_survive_a_restart(make_flock):
    server = make_flock()
    alice = server.add_user("alice@lab.test")
    tokens = login(server, alice.email, alice.password).json()
    server.stop()
    restarted = make_flock(reuse=server)
    assert restarted.client(token=alice.token).get(ME).json()["email"] == alice.email
    assert restarted.client(token=tokens["access_token"]).get(ME).status == 200
    refreshed = restarted.anonymous().post(
        "/api/v1/auth/refresh", {"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status == 200
    assert login(restarted, alice.email, alice.password).status == 200


def test_configured_jwt_secret_signs_tokens(make_flock):
    secret = "configured-secret-0123456789abcdef"
    server = make_flock(jwt_secret=secret)
    alice = server.add_user("alice@lab.test")
    claims = jwt.decode(alice.signup["access_token"], secret, ["HS256"], issuer="flock")
    assert claims["sub"] == str(alice.id)
