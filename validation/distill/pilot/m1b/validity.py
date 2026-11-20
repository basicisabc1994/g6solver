"""Pure construction-validity counts from headline-labelled rows."""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping

HEADLINE_LABELS = frozenset(("positive", "negative"))


def construction_validity(rows: Iterable[Mapping[str, str | None]]) -> dict:
    """Compute CV1, CV2 and the N=3 CV3 count, without fitting or labelling.

    CV1 weights rows, including repeated constructions. CV2 requires both
    headline labels in each of at least three repositories within one kind.
    CV3 counts distinct repositories with both labels in an informative kind;
    labels from different kinds cannot be combined to qualify a repository.
    Missing identifiers and non-headline labels contribute only to ignored_count.
    """
    counts = defaultdict(Counter)
    ignored = 0
    for row in rows:
        repository, kind, label = (row.get(key) for key in ("repository", "kind", "label"))
        if (not all(isinstance(value, str) and value.strip() for value in (repository, kind, label))
                or label not in HEADLINE_LABELS):
            ignored += 1
            continue
        counts[kind][label, repository] += 1

    table, mixed = {}, {}
    labelled, correct = 0, 0
    for kind in sorted(counts):
        entries = counts[kind]
        labels = sorted({label for label, _ in entries})
        table[kind] = {label: {repo: entries[label, repo]
                               for repo in sorted({repo for lab, repo in entries if lab == label})}
                       for label in labels}
        totals = [sum(table[kind][label].values()) for label in labels]
        labelled += sum(totals)
        correct += max(totals)
        mixed[kind] = sorted(set(table[kind].get("positive", {})) & set(table[kind].get("negative", {})))
    informative = [kind for kind in mixed if len(mixed[kind]) >= 3]
    stop_repositories = {repo for kind in informative for repo in mixed[kind]}
    return {
        "code": "ok" if labelled else "no_headline_rows",
        "ignored_count": ignored,
        "cv1": {"table": table, "labelled_count": labelled, "best_rule_correct": correct,
                "best_rule_accuracy": correct / labelled if labelled else None},
        "cv2": {"repositories_by_kind": mixed, "informative_kinds": informative},
        "cv3": {"repository_count": len(stop_repositories), "stop_count": 3,
                "stop_reached": len(stop_repositories) >= 3},
    }
