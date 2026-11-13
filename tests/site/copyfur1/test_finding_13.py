def test_select_labels_are_reader_facing_copy(honesty):
    pages = {
        'option': '<select><option label="G6 verifies every patch.">Checked.</option></select>',
        'optgroup': '<select><optgroup label="G6 verifies every patch."><option>Checked.</option></optgroup></select>',
    }
    missed = [tag for tag, page in pages.items() if not honesty.claims(honesty._page_copy(page))]
    assert missed == [], f'Visible select labels skipped: {missed}'
