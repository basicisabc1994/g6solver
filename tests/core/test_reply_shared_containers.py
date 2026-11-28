# SPDX-License-Identifier: Apache-2.0
"""Reply conversion retains sharing without mixing serialized model values."""
from pydantic import BaseModel

from g6.core.external_data import component_value


def test_shared_containers_remain_shared_before_the_door_counts_them():
    rows = [1, 2, 3]
    reply = component_value({"rows": [rows, rows]}, in_result=True)
    assert reply["rows"] == [[1, 2, 3], [1, 2, 3]]
    assert reply["rows"][0] is reply["rows"][1]


def test_temporary_model_dictionaries_cannot_reuse_an_earlier_identity():
    class Payload(BaseModel):
        data: dict[str, int]

    values = [Payload(data={"number": number}) for number in range(100)]
    reply = component_value(values, in_result=True)
    assert reply == [{"data": {"number": number}} for number in range(100)]
