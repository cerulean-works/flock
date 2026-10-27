"""S3's XML bodies: errors, bucket listings, and mapping upstream bucket names back."""

import secrets
import xml.etree.ElementTree as ET
from collections.abc import Iterable

from starlette.responses import Response

NS = "http://s3.amazonaws.com/doc/2006-03-01/"
ET.register_namespace("", NS)


def local(tag: str) -> str:
    return tag.rpartition("}")[2]


def dump(root: ET.Element) -> bytes:
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root)


def xml_response(root: ET.Element, status: int = 200, headers=None) -> Response:
    return Response(dump(root), status, headers, media_type="application/xml")


def error(
    code: str,
    message: str,
    status: int,
    resource: str = "",
    head: bool = False,
    bucket: str | None = None,
) -> Response:
    """An S3 error; HEAD answers carry no body, as S3's don't."""
    if head:
        return Response(status_code=status, headers={"x-amz-error-code": code})
    root = ET.Element("Error")
    fields = [("Code", code), ("Message", message)]
    if bucket is not None:
        fields.append(("BucketName", bucket))
    fields += [("Resource", resource), ("RequestId", secrets.token_hex(8))]
    for name, text in fields:
        ET.SubElement(root, name).text = text
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


def list_buckets(buckets: Iterable[tuple[str, str]]) -> Response:
    """ListBuckets' answer for ``(name, creation date)`` pairs."""
    root = ET.Element(f"{{{NS}}}ListAllMyBucketsResult")
    owner = ET.SubElement(root, f"{{{NS}}}Owner")
    ET.SubElement(owner, f"{{{NS}}}ID").text = "flock"
    ET.SubElement(owner, f"{{{NS}}}DisplayName").text = "flock"
    listed = ET.SubElement(root, f"{{{NS}}}Buckets")
    for name, created in buckets:
        bucket = ET.SubElement(listed, f"{{{NS}}}Bucket")
        ET.SubElement(bucket, f"{{{NS}}}Name").text = name
        ET.SubElement(bucket, f"{{{NS}}}CreationDate").text = created
    return xml_response(root)


def location(region: str) -> Response:
    """GetBucketLocation's answer: empty for us-east-1, as S3 has it."""
    root = ET.Element(f"{{{NS}}}LocationConstraint")
    root.text = None if region == "us-east-1" else region
    return xml_response(root)


def rename(body: bytes, upstream: str, bucket: str, location: str | None = None) -> bytes:
    """An upstream answer with the bucket ``upstream`` called ``bucket`` again, where S3 names it:
    a listing's ``Name``, a multipart result's ``Bucket``, and ``Location`` (replaced by
    ``location`` when given). Owners and initiators (the global user) are dropped."""
    root = ET.fromstring(body)
    for parent in root.iter():
        for child in list(parent):
            name = local(child.tag)
            if name in ("Owner", "Initiator"):
                parent.remove(child)
            elif name in ("Name", "Bucket") and parent is root and child.text == upstream:
                child.text = bucket
            elif name == "Location" and parent is root and location is not None:
                child.text = location
    return dump(root)
