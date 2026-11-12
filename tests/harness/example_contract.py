"""Independent fixed-text identity for the fresh-interpreter example compiler."""
import hashlib
import json

EXAMPLE_TEXT_SHA256 = 'e49e0b1ba995723a63af93b2dcf76a39a2a7122e2fa306092cca31f56e6b4c8e'


def reference_instance_digest(base, one):
    """Keep the historical instance format, with the deliberately replaced example text pinned exactly."""
    if one.template != 'example.v1':
        return base.instance_digest(one)
    body = {'template': one.template, 'template_text_sha256': EXAMPLE_TEXT_SHA256, 'id': one.id,
            'requirement_id': one.requirement_id, 'intent': one.intent, 'bindings': one.bindings,
            'provenance': one.provenance}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=True).encode('utf-8')).hexdigest()
