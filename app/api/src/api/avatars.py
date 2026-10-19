from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.api.src.api.deps import AdminOrgDep, APIError, JSONResponse, StoreDep, UserDep
from app.api.src.api.orgs import org_info
from app.api.src.core.avatars import Avatars, ImageError

router = APIRouter(prefix="/api/v1", tags=["avatars"])

IMAGE_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            f"image/{kind}": {"schema": {"type": "string", "format": "binary"}}
            for kind in ("png", "jpeg", "gif", "webp")
        },
    }
}


async def read_image(request: Request) -> bytes:
    """The request body, refused (413) as soon as it grows past an avatar's size limit."""
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > Avatars.MAX_BYTES:
        raise APIError(413, f"images are at most {Avatars.MAX_BYTES} bytes")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > Avatars.MAX_BYTES:
            raise APIError(413, f"images are at most {Avatars.MAX_BYTES} bytes")
    return bytes(data)


async def changed(change) -> None:
    try:
        await change
    except ImageError as e:
        raise APIError(e.status, str(e)) from None


@router.get("/avatars/{digest}", response_class=Response)
async def get_avatar(digest: str, request: Request, store: StoreDep) -> Response:
    """An avatar image. Public: its URL (the SHA-256 of the image) is the secret, and the
    image behind a URL never changes, so it may be cached for good."""
    try:
        key = bytes.fromhex(digest)
    except ValueError:
        key = b""
    found = await store.avatars.image(key) if len(key) == 32 else None
    if found is None:
        raise APIError(404, "avatar not found")
    content_type, data = found
    headers = {
        "cache-control": "public, max-age=31536000, immutable",
        "etag": f'"{digest}"',
        "x-content-type-options": "nosniff",
        "content-security-policy": "default-src 'none'",
    }
    if request.headers.get("if-none-match") == headers["etag"]:
        return Response(status_code=304, headers=headers)
    return Response(data, media_type=content_type, headers=headers)


@router.put("/me/avatar", openapi_extra=IMAGE_BODY)
async def set_my_avatar(request: Request, user: UserDep, store: StoreDep) -> Response:
    """Upload your avatar: the body is a PNG, JPEG, GIF or WebP image of at most 1 MiB."""
    await changed(store.avatars.set_user(user, await read_image(request)))
    return JSONResponse(store.accounts.info(user))


@router.delete("/me/avatar")
async def delete_my_avatar(user: UserDep, store: StoreDep) -> Response:
    await store.avatars.set_user(user, None)
    return JSONResponse(store.accounts.info(user))


@router.post("/me/avatar/gravatar")
async def fetch_my_gravatar(user: UserDep, store: StoreDep) -> Response:
    """Replace your avatar with your email's Gravatar (404 if you have none there)."""
    if not store.settings.gravatar:
        raise APIError(409, "Gravatar is turned off on this server")
    try:
        found = await store.avatars.gravatar(user)
    except ImageError as e:
        raise APIError(502, f"Gravatar sent an unusable image: {e}") from None
    except OSError as e:
        raise APIError(502, f"could not reach Gravatar: {e}") from None
    if not found:
        raise APIError(404, f"{user.email} has no Gravatar")
    return JSONResponse(store.accounts.info(user))


@router.put("/orgs/{org}/avatar", openapi_extra=IMAGE_BODY)
async def set_org_avatar(
    org: AdminOrgDep, request: Request, user: UserDep, store: StoreDep
) -> Response:
    """Upload the organization's avatar (admins), like ``PUT /me/avatar``."""
    await changed(store.avatars.set_org(org, await read_image(request)))
    return JSONResponse(org_info(store, org, user))


@router.delete("/orgs/{org}/avatar")
async def delete_org_avatar(org: AdminOrgDep, user: UserDep, store: StoreDep) -> Response:
    await store.avatars.set_org(org, None)
    return JSONResponse(org_info(store, org, user))
