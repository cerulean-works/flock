"""S3's XML bodies: errors, and rewriting keys between the virtual bucket and the upstream."""

import secrets
import xml.etree.ElementTree as ET
from collections.abc import Callable
from urllib.parse import quote_plus, unquote_plus

from starlette.responses import Response

NS = "http://s3.amazonaws.com/doc/2006-03-01/"
ET.register_namespace("", NS)

# Elements holding object keys or key prefixes, in the responses the proxy rewrites.
KEY_ELEMENTS = frozenset({"Key", "Prefix", "StartAfter"})


def local(tag: str) -> str:
    return tag.rpartition("}")[2]


def dump(root: ET.Element) -> bytes:
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root)


def xml_response(root: ET.Element, status: int = 200, headers=None) -> Response:
    return Response(dump(root), status, headers, media_type="application/xml")


def error(code: str, message: str, status: int, resource: str = "", head: bool = False):
    """An S3 error; HEAD answers carry no body, as S3's don't."""
    root = ET.Element("Error")
    for name, text in (
        ("Code", code),
        ("Message", message),
        ("Resource", resource),
        ("RequestId", secrets.token_hex(8)),
    ):
        ET.SubElement(root, name).text = text
    if head:
        return Response(status_code=status, headers={"x-amz-error-code": code})
    return xml_response(root, status)


def parse_error(body: bytes) -> tuple[str, str] | None:
    """(code, message) of an upstream error body, if it is one."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None
    if local(root.tag) != "Error":
        return None
    code = root.findtext("Code") or root.findtext(f"{{{NS}}}Code") or "InternalError"
    message = root.findtext("Message") or root.findtext(f"{{{NS}}}Message") or ""
    return code, message


def list_buckets(name: str, created: str) -> Response:
    root = ET.Element(f"{{{NS}}}ListAllMyBucketsResult")
    owner = ET.SubElement(root, f"{{{NS}}}Owner")
    ET.SubElement(owner, f"{{{NS}}}ID").text = "flock"
    ET.SubElement(owner, f"{{{NS}}}DisplayName").text = "flock"
    bucket = ET.SubElement(ET.SubElement(root, f"{{{NS}}}Buckets"), f"{{{NS}}}Bucket")
    ET.SubElement(bucket, f"{{{NS}}}Name").text = name
    ET.SubElement(bucket, f"{{{NS}}}CreationDate").text = created
    return xml_response(root)


def rewrite(
    body: bytes,
    key: Callable[[str], str | None],
    bucket: str,
    url_encoded: bool = False,
    token: Callable[[str], str] | None = None,
) -> tuple[ET.Element, bytes]:
    """An upstream response with its keys mapped by ``key`` and its bucket renamed.

    ``key`` returns None for keys that aren't the caller's to see; their entries are dropped.
    ``url_encoded`` keys (``encoding-type=url``) are decoded before mapping and re-encoded after.
    ``token`` maps continuation tokens. Owners (the global user) are dropped.
    """
    root = ET.fromstring(body)

    def walk(parent: ET.Element) -> None:
        for child in list(parent):
            name = local(child.tag)
            if name == "Owner" or name == "Initiator":
                parent.remove(child)
            elif name in KEY_ELEMENTS and child.text is not None:
                text = unquote_plus(child.text) if url_encoded else child.text
                mapped = key(text)
                if mapped is None:
                    if parent is root:
                        child.text = ""
                    else:
                        root_remove(root, parent)
                    continue
                child.text = quote_plus(mapped, safe="/") if url_encoded else mapped
            elif name in ("Bucket", "Name") and parent is root:
                child.text = bucket
            elif name in ("ContinuationToken", "NextContinuationToken") and token is not None:
                child.text = token(child.text or "")
            elif name == "Location":
                parent.remove(child)
            else:
                walk(child)

    walk(root)
    return root, dump(root)


def root_remove(root: ET.Element, element: ET.Element) -> None:
    """Remove ``element`` (an entry such as ``Contents``) from wherever it is in ``root``."""
    for parent in root.iter():
        if element in list(parent):
            parent.remove(element)
            return
