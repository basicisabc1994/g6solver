"""Source census plus executable one-over admission challenges.

The census deliberately includes semantic comparisons and retention slices.
Their disposition is explicit; an added/changed expression cannot inherit a
previous owner's classification. The completeness check keeps offered bounds
visible instead of treating an inventory row as runtime evidence.
"""
import ast
import json
from pathlib import Path

import pytest

from g6.cognitive_bridge import snapshot
from tests.cognitive_bridge import test_followup4_repair1_limits as limits
from tests.cognitive_bridge import test_followup4_repair1_late as late
from tests.cognitive_bridge import test_followup4_repair1_edges as edges
from tests.cognitive_bridge import test_followup4_repair1_admission as admission

MANIFEST = Path(__file__).with_name('followup4_repair1_bounds.json')


def discover(sources=None):
    if sources is None:
        sources = {p.name: p.read_text(encoding='utf-8') for p in Path(snapshot.__file__).parent.glob('*.py')}
    rows = []
    for module, source in sorted(sources.items()):
        def visit(node, owner):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                owner = '.'.join(filter(None, [owner, node.name]))
            kind = None
            if isinstance(node, ast.Compare):
                kind = 'comparison'
            elif isinstance(node, ast.Slice):
                kind = 'slice'
            elif isinstance(node, ast.keyword) and node.arg in {
                'min_length', 'max_length', 'ge', 'gt', 'le', 'lt', 'max_tokens', 'cap', 'minimum'}:
                kind = 'declared-bound'
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and any(
                word in ast.unparse(node).split('=', 1)[0].upper()
                for word in ('MAX_', 'MIN_', 'DEPTH', 'BUDGET', 'CAP', 'LIMIT')):
                kind = 'budget-definition'
            elif isinstance(node, ast.Call) and ast.unparse(node.func) in {
                '_admit_length', '_admit_json', '_admit_bytes', '_admit_array', '_encoded_size'}:
                kind = 'admission'
            elif isinstance(node, ast.Dict) and any(isinstance(k, ast.Constant) and k.value in
                ('max_bytes', 'max_length') for k in node.keys):
                kind = 'record-bound'
            if kind:
                rows.append([module, owner, kind, ast.unparse(node)])
            for child in ast.iter_child_nodes(node):
                visit(child, owner)
        visit(ast.parse(source), '')
    return rows


def test_census_exactly_matches_current_code_and_rejects_a_new_bound():
    expected = json.loads(MANIFEST.read_text(encoding='utf-8'))
    assert discover() == [row['source'] for row in expected], 'new/changed bound requires its own disposition and probe'
    changed = {p.name: p.read_text(encoding='utf-8') for p in Path(snapshot.__file__).parent.glob('*.py')}
    changed['snapshot.py'] += '\ndef added_bound(value):\n if len(value) > 17: raise ValueError("cap")\n'
    assert discover(changed) != [row['source'] for row in expected]


def test_every_bound_has_an_instrumented_one_over_case():
    rows = json.loads(MANIFEST.read_text(encoding='utf-8'))
    pending = [row['source'] for row in rows if row['disposition'] == 'offered']
    assert not pending, f'admission not established for these individual bounds: {pending}'
    assert all(row['reason'] for row in rows)
    assert all(row['probes'] for row in rows if row['disposition'] in ('resource', 'scalar'))


def runtime_cases():
    cases = {}
    for cls, name, annotation, bound, limit in limits.LIMITS:
        for typed in (False, True):
            cases[f'schema:{cls.__name__}.{name}.{bound}:{typed}'] = lambda mp, cls=cls, name=name, annotation=annotation, bound=bound, limit=limit, typed=typed: limits.test_every_model_bound_precedes_boundary_conversion(cls, name, annotation, bound, limit, typed)
    for cls, name, cap in edges.model_list_caps():
        for api in ('model_validate', 'constructor'):
            cases[f'direct-schema:{cls.__name__}.{name}:{api}'] = lambda mp, cls=cls, name=name, cap=cap, api=api: edges.test_direct_model_list_cap_precedes_item_validation(cls, name, cap, api, mp)
    for name in limits.scalar_limits():
        cases[f'scalar:{name}'] = lambda mp, name=name: limits.test_every_scalar_budget_refuses_before_external_work(name, mp)
    for kind in ('list', 'mapping', 'text'):
        cases['walk:' + kind] = lambda mp, kind=kind: limits.test_known_walk_budget_one_over_precedes_children(kind, mp)
    for kind in ('visits', 'bytes', 'depth'):
        cases['array:' + kind] = lambda mp, kind=kind: edges.test_native_array_each_budget_before_tolist(kind, mp)
    for hook in ('tolist', 'attribute', 'buffer'):
        cases['array-hook:' + hook] = lambda mp, hook=hook: edges.test_python_array_hooks_cannot_bypass_native_size_admission(hook)
    for outer in ('dict-subclass', 'protocol', 'frozen'):
        cases['outer-field:' + outer] = lambda mp, outer=outer: edges.test_outer_mapping_field_cap_precedes_detachment(outer)
    cases['model-root:keys'] = lambda mp: edges.test_model_input_does_not_compare_foreign_keys()
    cases['model-root:attributes'] = lambda mp: edges.test_model_input_cannot_extract_unmeasured_attributes()
    cases['model-root:visits'] = lambda mp: edges.test_model_global_visit_budget_precedes_core_dictionary_copy(mp)
    cases['array:unmeasured'] = lambda mp: admission.test_an_unmeasured_array_cannot_run_its_converter()
    cases['array:unavailable'] = lambda mp: edges.test_unavailable_native_buffer_is_a_named_refusal(mp)
    cases['generated:output-size'] = generated_output_size
    for field in ('oracle_digest', 'variant_digest'):
        cases['digest:' + field] = lambda mp, field=field: edges.test_digest_width_one_over_precedes_conversion(field)
    for char in ('\0', '😀', '\\', '"', '\n', 'a', '\ud800'):
        cases['encoded:' + ascii(char)] = lambda mp, char=char: admission.test_encoded_admission_preserves_exact_boundary(mp, char)
    for path in ('construct', 'replace', 'fresh'):
        for view in (False, True):
            cases[f'buffer:{path}:{view}'] = lambda mp, path=path, view=view: edges.test_buffer_one_over_precedes_copy_on_each_record_path(path, view)
    for field in ('failure_mode', 'assumption_at_risk', 'counterexample_or_refutation', 'suggested_method',
                  'evidence_required', 'potential_domain_standard_gap'):
        cases['critique-field:' + field] = lambda mp, field=field: limits.test_record_local_length_before_conversion('critique', field, 1200)
        cases['reply-field:critique.' + field] = lambda mp, field=field: edges.test_model_field_one_over_precedes_parsed_field_copy('critique', field, 1200)
    for kind, field, cap in [('probes', 'id', 100), ('probes', 'question', 1000),
                           *[('ideas', f, 1000) for f in ('id', 'statement', 'cross_domain_analogy', 'falsifying_question')]]:
        cases[f'reply-field:{kind}.{field}'] = lambda mp, kind=kind, field=field, cap=cap: edges.test_model_field_one_over_precedes_parsed_field_copy(kind, field, cap)
    cases['encoded:all-bmp'] = lambda mp: edges.test_encoder_all_bmp_escape_widths_before_output(mp)
    cases['generated:count'] = generated_count
    cases['walk:depth'] = walk_depth
    for train, holdout in [(9, 5), (10, 4)]:
        cases[f'rows:{train}:{holdout}'] = lambda mp, train=train, holdout=holdout: edges.test_distillation_row_minimum_precedes_numeric_conversion(train, holdout)
    cases['candidates'] = lambda mp: late.test_candidate_cap_precedes_detachment()
    cases['obligations'] = lambda mp: limits.test_portfolio_obligation_cap_precedes_scoring()
    for kind in ('information', 'ml', 'method'):
        cases['affordability:' + kind] = lambda mp, kind=kind: limits.test_unaffordable_candidate_never_scored(kind)
    cases['reply'] = lambda mp: late.test_json_reply_cap_precedes_text_conversion(True)
    cases['arguments'] = lambda mp: late.test_tool_argument_one_over_precedes_encoding(mp)
    cases['model'] = lambda mp: late.test_declarative_model_one_over_precedes_encoding(mp)
    for kind, cap, error in [('critique', 25000, 'critique prompt'), ('claims', 15000, 'prompt cap')]:
        cases['prompt:' + kind] = lambda mp, kind=kind, cap=cap, error=error: late.test_prompt_one_over_precedes_encoding(mp, kind, cap, error)
    for name, value, error in [('claims', [None] * 21, '20'), ('hypotheses', [None] * 33, '32')]:
        cases['count:' + name] = lambda mp, name=name, value=value, error=error: limits.test_collection_bound_precedes_detachment(name, value, error)
    for kind, field, cap in [('claim', 'id', 100), ('claim', 'statement', 3000), ('claim', 'evidence_needs', 5), ('critique', 'failure_mode', 1200)]:
        cases[f'field:{kind}.{field}'] = lambda mp, kind=kind, field=field, cap=cap: limits.test_record_local_length_before_conversion(kind, field, cap)
    for kind, field, cap in [('critic', 'objective', 3000), ('critic', 'model_summary', 12000), ('compiler', 'question', 3000), ('features', 'feature_names', 100)]:
        cases[f'length:{kind}.{field}'] = lambda mp, kind=kind, field=field, cap=cap: limits.test_function_length_before_conversion(kind, field, cap)
    for kind, collection, error in [('probes', 'probes', 'too many probes'), ('ideas', 'ideas', 'idea schema or count'), ('claims', 'claims', 'claim count'), ('critique', 'candidates', 'too many critique')]:
        cases['reply-count:' + kind] = lambda mp, kind=kind, collection=collection, error=error: limits.test_model_count_one_over_before_parsed_collection_detachment(kind, collection, error)
    # Repair 2: the copier reads each declared bound itself, at every public entry (listed from the code there).
    from tests.cognitive_bridge import test_followup4_repair2_inventory as copier
    for entry, parameter, kind, name in copier._cases():
        cases[f'copier:{entry}:{parameter}:{name}'] = lambda mp, entry=entry, parameter=parameter, kind=kind, name=name: copier.test_one_over_is_refused_before_any_detachment_round_trip_or_encoding(entry, parameter, kind, name, mp)
    cases['text-keys:budget'] = copier.test_the_key_reader_spends_the_visit_budget_before_it_reads_a_key
    # Repair 3: one tally per detachment, one native scalar per array item.
    from tests.cognitive_bridge import test_followup4_repair3_inventory as roots
    cases['tally:detachment'] = lambda mp: [roots.test_one_tally_counts_a_declared_type_across_the_whole_detachment(make) for make in (
        lambda: {f'h{i}': roots.Hypothesis(f'h{i}', 's', .5) for i in range(33)},)]
    cases['array:format'] = lambda mp: roots.test_a_buffer_format_that_is_not_one_native_scalar_is_refused('T{<i:x:<d:y:}', mp)
    return cases


def generated_output_size(monkeypatch):
    """One outstanding bound: an opaque maker supplies no pre-call output size."""
    monkeypatch.setattr(snapshot, 'MAX_SIZE', 4)
    calls = []
    def make(index):
        calls.append(index)
        return 'long'  # root cost 1 + 4 characters = one over the size budget
    with pytest.raises(snapshot.NotPlainData, match='size'):
        snapshot.generated(1, make, where='generated size')
    assert calls == [], 'unmeasured maker ran before output-size admission'


def generated_count(monkeypatch):
    calls = []
    with pytest.raises(snapshot.NotPlainData, match='size'):
        snapshot.generated(snapshot.MAX_ITEMS, lambda i: calls.append(i), where='visit count')
    assert calls == []


def walk_depth(monkeypatch):
    value = 0
    for _ in range(snapshot.DEPTH + 1):
        value = [value]
    with late.calls_to(snapshot.json.dumps) as encoded:
        with pytest.raises(snapshot.NotPlainData, match='nested deeper'):
            snapshot.canonical_json(value)
    assert encoded == []


CASES = runtime_cases()


@pytest.mark.parametrize('name', list(CASES))
def test_inventory_runtime_one_over(name, monkeypatch):
    CASES[name](monkeypatch)


def test_manifest_references_executable_cases():
    rows = json.loads(MANIFEST.read_text(encoding='utf-8'))
    assert all(set(row['probes']) <= CASES.keys() for row in rows)
