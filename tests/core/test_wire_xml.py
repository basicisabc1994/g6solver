# SPDX-License-Identifier: Apache-2.0
import pytest

from g6.core.xml import parse_xml


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_xml_rejects_dtd_in_any_encoding(encoding):
    data = ('<?xml version="1.0" encoding="' + encoding + '"?>'
            '<!DOCTYPE testsuite [<!ENTITY x "forged">]><testsuite>&x;</testsuite>').encode(encoding)
    with pytest.raises(ValueError, match="DOCTYPE"):
        parse_xml(data)


def test_xml_byte_limit():
    with pytest.raises(ValueError, match="byte limit"):
        parse_xml(b"<testsuite/>", limit=3)


def test_unknown_xml_encoding_is_unreadable_evidence():
    with pytest.raises(ValueError):
        parse_xml(b'<?xml version="1.0" encoding="not-a-codec"?><testsuite/>')
