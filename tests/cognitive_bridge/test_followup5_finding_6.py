"""The array format rule refuses a buffer whose every item is one text item (a regression of repair 3).

Repair 3 admits "a buffer whose items are one native scalar each (or one text or bytes item)". Its pattern names the
text formats ``s``, ``p`` and ``w`` and leaves out ``u``, the format of ``array.array('u')``: one Unicode character
an item. On b683e40 it converted to a list of text.
"""
import array

from g6.cognitive_bridge import xai
from g6.cognitive_bridge.codes import NotPlainData


def test_an_array_of_one_character_items_is_read_as_text():
    try:
        taken = xai._native(array.array('u', 'ab'), where="the estimator's feature_names_in_")
    except NotPlainData as exc:
        taken = str(exc)
    assert taken == ['a', 'b']
