# SPDX-License-Identifier: Apache-2.0
"""What the Open Australian Legal Corpus is, pinned from its publisher's own records (retrieved 2026-10-21).

The dataset's Hugging Face API at the pinned revision lists ``corpus.jsonl`` with its size and SHA-256 (:data:`PINNED`);
its README at that revision gives the value sets of ``type``, ``jurisdiction`` and ``source`` (:data:`TYPES`,
:data:`JURISDICTIONS`, :data:`SOURCES`), the version (7.1.0), the date its sources were collected (10 March 2025) and
the counts by type; its ``LICENCE.md`` gives the terms (CC BY 4.0 for the corpus; each document also under its source's
terms, three of them custom). G6 hosts and publishes none of it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Pin:
    """One file of a dataset at one revision, with what its publisher states about it."""

    dataset: str
    revision: str
    filename: str
    size: int
    sha256: str
    version: str
    collected: str        # the date the corpus's sources were collected, as its README states it
    revised: str          # the revision's own date on Hugging Face

    @property
    def url(self) -> str:
        return f"https://huggingface.co/datasets/{self.dataset}/resolve/{self.revision}/{self.filename}"

    @property
    def page(self) -> str:
        return f"https://huggingface.co/datasets/{self.dataset}/tree/{self.revision}"

    def command(self, folder: str = "<folder>") -> str:
        """The command a person runs to download the file themselves (huggingface_hub's ``hf``)."""
        return (f"hf download {self.dataset} {self.filename} --repo-type dataset --revision {self.revision} "
                f"--local-dir {folder}")


PINNED = Pin(dataset="isaacus/open-australian-legal-corpus", revision="ef45e3fec41a960919a31149eee6dab9aa39f725",
             filename="corpus.jsonl", size=9_401_179_433,
             sha256="5abc64a3ad35a44eca65706babd63d1095c29ebebf3f5c588beb56612097b758", version="7.1.0",
             collected="10 March 2025", revised="2026-03-07")

DOI = "10.57967/hf/2784"
ATTRIBUTION = ("Open Australian Legal Corpus, version 7.1.0, by Umar Butler, published by Isaacus "
               f"(doi:{DOI}), under CC BY 4.0; each document also under its source's own terms (the dataset's "
               "LICENCE.md). G6 cut the documents into passages and changed nothing else.")

#: ``type``'s values, as the README lists them.
TYPES: tuple[str, ...] = ("primary_legislation", "secondary_legislation", "bill", "decision")
#: What ``build`` indexes unless asked for more: acts and subordinate legislation, not bills or decisions.
LEGISLATION: tuple[str, ...] = ("primary_legislation", "secondary_legislation")
#: ``jurisdiction``'s values, as the README lists them, with the names said in replies.
JURISDICTIONS: dict[str, str] = {"commonwealth": "the Commonwealth", "new_south_wales": "New South Wales",
                                 "queensland": "Queensland", "western_australia": "Western Australia",
                                 "south_australia": "South Australia", "tasmania": "Tasmania",
                                 "norfolk_island": "Norfolk Island"}
#: Australian jurisdictions the corpus has no document from.
NOT_COVERED: dict[str, str] = {"victoria": "Victoria", "australian_capital_territory": "the Australian Capital Territory",
                               "northern_territory": "the Northern Territory"}
#: Documents by type in version 7.1.0 (the README's statistics): decisions are most of the corpus.
COUNTS: dict[str, int] = {"primary_legislation": 9_059, "secondary_legislation": 31_696, "bill": 2_589,
                          "decision": 189_216}


@dataclass(frozen=True)
class Source:
    """One of the corpus's sources: its name, the official site to confirm a document against, and its terms."""

    name: str
    register: str
    terms: str


_CC = "CC BY 4.0, with the attribution wording the dataset's LICENCE.md gives for this source"
SOURCES: dict[str, Source] = {
    "federal_register_of_legislation": Source(
        "Federal Register of Legislation", "https://www.legislation.gov.au",
        "CC BY 4.0: \"Sourced from the Federal Register of Legislation at 10 March 2025. For the latest information "
        "on Australian Government law please go to https://www.legislation.gov.au.\""),
    "nsw_legislation": Source("NSW Legislation", "https://legislation.nsw.gov.au", _CC),
    "queensland_legislation": Source("Queensland Legislation", "https://www.legislation.qld.gov.au", _CC),
    "western_australian_legislation": Source("Western Australian Legislation", "https://www.legislation.wa.gov.au",
                                             _CC),
    "south_australian_legislation": Source("South Australian Legislation", "https://www.legislation.sa.gov.au", _CC),
    "tasmanian_legislation": Source("Tasmanian Legislation", "https://www.legislation.tas.gov.au", _CC),
    "federal_court_of_australia": Source(
        "Federal Court of Australia", "https://www.fedcourt.gov.au",
        "the Court's own terms: unaltered, for personal or non-commercial use or use within an organisation; a "
        "judgment may be republished unaltered, commercially too, acknowledged as a judgment of the Court with its "
        "source credited"),
    "high_court_of_australia": Source(
        "High Court of Australia", "https://www.hcourt.gov.au",
        "the Court's own terms: unaltered only, for personal or non-commercial use or use within an organisation, "
        "with its copyright notice kept"),
    "nsw_caselaw": Source(
        "NSW Caselaw", "https://www.caselaw.nsw.gov.au",
        "the State's own terms: reproduced accurately, in context, not as an official version, consistent with the "
        "current version and with any suppression order; copyright stays with the State, which may vary or revoke "
        "the permission"),
}

_ALIASES: dict[str, str] = {
    "cth": "commonwealth", "federal": "commonwealth", "australian_government": "commonwealth",
    "nsw": "new_south_wales", "qld": "queensland", "wa": "western_australia", "sa": "south_australia",
    "tas": "tasmania", "norfolk": "norfolk_island", "nfk": "norfolk_island",
    "vic": "victoria", "act": "australian_capital_territory", "nt": "northern_territory",
}
#: Words in a query that name a jurisdiction the corpus lacks; "ACT" and "NT" only in capitals, since "act" is a word.
_UNCOVERED_WORDS = re.compile(r"\b(?:vic|victoria|victorian|victorians)\b|\baustralian\s+capital\s+territory\b|"
                              r"\bnorthern\s+territory\b", re.IGNORECASE)
_UNCOVERED_CAPS = re.compile(r"\b(?:ACT|NT)\b")


def jurisdiction(name: str) -> str:
    """The corpus's code for ``name`` (``NSW``, ``New South Wales``, ``new_south_wales``...), or the code of a
    jurisdiction it lacks (``victoria``...), or ``""`` for a name that is neither."""
    key = re.sub(r"[^a-z]+", "_", (name or "").strip().lower()).strip("_").removeprefix("the_")
    code = _ALIASES.get(key, key)
    return code if code in JURISDICTIONS or code in NOT_COVERED else ""


def uncovered_named(query: str) -> str:
    """The jurisdiction the corpus lacks that ``query`` names, or ``""``."""
    if (found := _UNCOVERED_WORDS.search(query or "")) is not None:
        word = found.group(0).lower()
        return NOT_COVERED["victoria" if word.startswith("vic") else
                           "australian_capital_territory" if word.startswith("australian") else "northern_territory"]
    if (found := _UNCOVERED_CAPS.search(query or "")) is not None:
        return NOT_COVERED["australian_capital_territory" if found.group(0) == "ACT" else "northern_territory"]
    return ""


def covered() -> str:
    """The jurisdictions the corpus covers, in words."""
    names = list(JURISDICTIONS.values())
    return ", ".join(names[:-1]) + " and " + names[-1]


__all__ = ["ATTRIBUTION", "COUNTS", "DOI", "JURISDICTIONS", "LEGISLATION", "NOT_COVERED", "PINNED", "SOURCES", "TYPES",
           "Pin", "Source", "covered", "jurisdiction", "uncovered_named"]
