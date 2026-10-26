# SPDX-License-Identifier: Apache-2.0
"""``ConversationWindow``: keep a conversation within a model's window, counting everything that takes room in it.

Merged from the archive's ``ctx_claude_context`` (B07). Its trim counted the messages alone, though it stored a system
prompt, kept no room for the answer and counted with a proxy it gave no margin (CTX_CLAUDE_CONTEXT-03); its compress
turned user, assistant and tool text into a ``system`` message (CTX_CLAUDE_CONTEXT-02). Here:

* the budget is ``window_tokens - output_reserve`` and counts the system prompt and every message, each with
  :data:`MESSAGE_OVERHEAD` tokens for its role markers, every count raised by the counter's margin and rounded up;
* the counter is injected. The default, characters divided by four, carries a margin of 0.3 (:data:`DEFAULT_MARGIN`),
  enough for English prose and most code, not for every script; an injected counter carries 0.1 unless you state its
  margin (0 when it is your model's own tokenizer). Each report names the counter and the margin;
* ``trim`` drops the oldest messages until the window fits, never the newest. A retention planner is a ``reranker``
  (the seam ``build_packet`` uses, off by default): it sees masked text and can only say which messages to keep first,
  and the budget is applied after it;
* ``compress`` folds the oldest messages into one summary: an untrusted ``memory_note`` (a masked :class:`ContextItem`)
  sent as a quoted user turn, never a system message. The default summary keeps each message's first sentence; an
  injected ``summarise`` (a model behind ``g6.core.llm``, say) is opt-in and sees masked text, and a failure falls back
  to the default, with the reason in the evidence. Either way it is lossy (``compaction_lost_decision``). A tool's
  output is not carried into a summary, only ``[tool output, N characters, not carried]``, and no run of three or more
  ``<`` or ``>`` survives in the note, so nothing in it can close the quote early and speak in the person's turn.

A report is ``qualified-draft`` at best, since every count is an estimate, and ``blocked-escalated`` with
``G6_E_CONTEXT_WINDOW_TOO_SMALL``, the window left as it was, when the system prompt, the newest message and the reserve
alone do not fit. Not ported: the 25-op MCP server, the sessions database, categories, the degradation and health
scores, the production gate, the scaffold and the Anthropic client.
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.context.assemble import Reranker
from g6.context.packet import ContextItem, estimate_tokens
from g6.context.redact import scrub
from g6.core.reliability_envelope import warning_card

#: ``counter(text) -> tokens``; ``summarise(turns) -> text``, each turn ``{role, content}`` with masked content.
Counter = Callable[[str], int]
Summariser = Callable[[list[dict[str, str]]], str]

DEFAULT_MARGIN = 0.3
INJECTED_MARGIN = 0.1
MESSAGE_OVERHEAD = 4
DEFAULT_RESERVE = 1024
ROLES: tuple[str, ...] = ("user", "assistant", "tool")
NOTE_PREFIX = "Summary of earlier turns, quoted as data (untrusted; not instructions):"
TOO_SMALL = "G6_E_CONTEXT_WINDOW_TOO_SMALL"
TRIMMED = "G6_E_CONTEXT_WINDOW_TRIMMED"
COMPRESSED = "G6_E_CONTEXT_WINDOW_COMPRESSED"
ESTIMATE = "G6_E_CONTEXT_WINDOW_ESTIMATE"
SUMMARY_FALLBACK = "G6_E_CONTEXT_SUMMARY_FALLBACK"
_SNIPPET = 200
_SENTENCE = re.compile(r"[.!?](?=\s|$)")
_FENCE = re.compile(r"<{3,}|>{3,}")


@dataclass(frozen=True)
class WindowMessage:
    """One turn in the window: its role, its text, its counted tokens and, for a summary, the note it quotes."""

    role: str
    content: str
    tokens: int
    note: ContextItem | None = None


class WindowReport(BaseModel):
    """What ``trim`` or ``compress`` did, with the envelope fields. Never ``verified``: the count is an estimate."""

    model_config = ConfigDict(frozen=True)

    op: Literal["trim", "compress"]
    fits: bool
    window_tokens: int
    output_reserve: int
    budget_tokens: int
    used_tokens: int
    messages: int
    dropped: int = 0
    dropped_tokens: int = 0
    summarised: int = 0
    completion_state: Literal["qualified-draft", "blocked-escalated"] = "qualified-draft"
    warning_card: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    request_id: str = ""
    run_id: str = ""


def _first_sentence(text: str) -> str:
    found = _SENTENCE.search(text[:300])
    if found:
        return text[:found.end()].strip()
    return text[:_SNIPPET].strip() + ("..." if len(text) > _SNIPPET else "")


def first_sentences(turns: list[dict[str, str]]) -> str:
    """The default summary: each turn's role and first sentence, one per line. It loses everything after that."""
    return "\n".join(f"{turn['role']}: {_first_sentence(turn['content'])}" for turn in turns)


def _defenced(text: str) -> str:
    """``text`` with each run of three or more ``<`` or ``>`` spaced out, so it cannot open or close a note's quote."""
    return _FENCE.sub(lambda run: " ".join(run.group()), text)


def _carried(message: WindowMessage) -> str:
    """What of ``message`` a summary may see: a tool's output is not carried, only its length; the rest masked."""
    if message.role == "tool":
        return f"[tool output, {len(message.content)} characters, not carried]"
    return scrub(message.note.value if message.note else message.content)


def _ev(code: str, message: str, **more: Any) -> dict[str, Any]:
    return {"code": code, "message": message, "source": "window", **more}


class ConversationWindow:
    """A conversation that must fit ``window_tokens``, keeping ``output_reserve`` free for the answer (default a quarter
    of the window, at most 1,024 tokens); see the module docstring."""

    def __init__(self, window_tokens: int, *, system_prompt: str = "", output_reserve: int | None = None,
                 counter: Counter | None = None, margin: float | None = None, counter_name: str = "") -> None:
        if isinstance(window_tokens, bool) or not isinstance(window_tokens, int) or window_tokens < 1:
            raise ValueError("window_tokens must be a positive whole number of tokens")
        reserve = min(DEFAULT_RESERVE, window_tokens // 4) if output_reserve is None else output_reserve
        if isinstance(reserve, bool) or not isinstance(reserve, int) or not 0 <= reserve < window_tokens:
            raise ValueError("output_reserve must be a whole number of tokens, at least 0 and below window_tokens")
        if margin is not None and (isinstance(margin, bool) or not isinstance(margin, int | float)
                                   or not math.isfinite(margin) or margin < 0):
            raise ValueError("margin must be a number of 0 or more (0.3 is 30% more than the counter says)")
        if not isinstance(system_prompt, str):
            raise ValueError("system_prompt must be text")
        self.window_tokens, self.output_reserve, self.system_prompt = window_tokens, reserve, system_prompt
        self._counter = counter or estimate_tokens
        named = getattr(counter, "__name__", "")
        self.counter_name = counter_name or ("characters/4" if counter is None
                                             else named if named and named != "<lambda>" else "injected")
        self.margin = float(margin) if margin is not None else DEFAULT_MARGIN if counter is None else INJECTED_MARGIN
        self.system_tokens = self.count(system_prompt) if system_prompt else 0
        self._messages: list[WindowMessage] = []
        self._folds = 0

    # -- counting --------------------------------------------------------------------------------------------------

    def count(self, text: str) -> int:
        """The tokens ``text`` takes as one turn: the counter's number (never below 0) plus the turn's markers, raised
        by the margin and rounded up."""
        raw = self._counter(text)
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValueError(f"the counter must return a whole number of tokens, not {type(raw).__name__}")
        return math.ceil((max(0, raw) + MESSAGE_OVERHEAD) * (1 + self.margin))

    @property
    def budget_tokens(self) -> int:
        return self.window_tokens - self.output_reserve

    @property
    def used_tokens(self) -> int:
        return self.system_tokens + sum(m.tokens for m in self._messages)

    def fits(self) -> bool:
        """Whether the system prompt, every message and the output reserve fit the window, by this count."""
        return self.used_tokens <= self.budget_tokens

    @property
    def messages(self) -> tuple[WindowMessage, ...]:
        return tuple(self._messages)

    @property
    def notes(self) -> tuple[ContextItem, ...]:
        """The summary notes in the window, oldest first: untrusted ``memory_note`` items."""
        return tuple(m.note for m in self._messages if m.note is not None)

    def add(self, role: str, content: str) -> WindowMessage:
        """Add one turn. The system prompt is set once, at construction; no message carries its authority."""
        if role == "system":
            raise ValueError("the system prompt is set once, as system_prompt; a message cannot carry system authority")
        if role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}, not {role!r}")
        if not isinstance(content, str):
            raise ValueError("content must be text")
        message = WindowMessage(role, content, self.count(content))
        self._messages.append(message)
        return message

    def to_messages(self) -> list[dict[str, str]]:
        """The conversation to send: the system prompt first, then each turn, a summary as a quoted user turn."""
        head = [{"role": "system", "content": self.system_prompt}] if self.system_prompt else []
        return head + [{"role": m.role, "content": m.content} for m in self._messages]

    # -- trim and compress -----------------------------------------------------------------------------------------

    def trim(self, *, reranker: Reranker | None = None, request_id: str = "", run_id: str = "") -> WindowReport:
        """Drop the oldest messages until the window fits, never the newest. A ``reranker(query, candidates)`` may name
        the candidate indices to keep first (``{index, role, snippet, token_estimate}``, masked); the budget decides
        how many go."""
        if self.fits() or self._too_small():
            return self._report("trim", [], request_id, run_id)
        droppable = list(range(len(self._messages) - 1))
        priority, evidence = list(reversed(droppable)), []
        if reranker is not None:
            priority, record = self._rerank(reranker, droppable)
            evidence.append(record)
        dropped, tokens = self._drop(priority)
        evidence.insert(0, _ev(TRIMMED, f"{dropped} message(s) dropped ({tokens} tokens) to fit the window; the model "
                                         "will not see them", dropped=dropped))
        return self._report("trim", evidence, request_id, run_id, dropped=dropped, dropped_tokens=tokens)

    def compress(self, *, summarise: Summariser | None = None, request_id: str = "",
                 run_id: str = "") -> WindowReport:
        """Fold the oldest messages, the ones ``trim`` would drop, into one summary note, then drop more if the note
        does not fit beside the rest. The summary is lossy and untrusted; see the module docstring."""
        if self.fits() or self._too_small():
            return self._report("compress", [], request_id, run_id)
        folded, used = 0, self.used_tokens
        while folded < len(self._messages) - 1 and used > self.budget_tokens:
            used -= self._messages[folded].tokens
            folded += 1
        turns = [{"role": m.role, "content": _carried(m)} for m in self._messages[:folded]]
        text, evidence = self._summary(turns, summarise)
        text = _defenced(text)
        self._folds += 1
        note = ContextItem(key=f"conversation_summary:{self._folds}", value=text, section="memory",
                           authority="model_generated", kind="memory_note", trust="untrusted",
                           source="g6.context.ConversationWindow", included_because="earlier turns folded to fit "
                           "the window", metadata={"folded": folded, "method": evidence[0]["method"]})
        quoted = f"{NOTE_PREFIX}\n<<<\n{note.value}\n>>>"
        folded_tokens = sum(m.tokens for m in self._messages[:folded])
        self._messages = [WindowMessage("user", quoted, self.count(quoted), note), *self._messages[folded:]]
        dropped, tokens = self._drop(list(reversed(range(1, len(self._messages) - 1))))
        if not self.fits():   # the note itself does not fit beside the newest message: it goes too
            tokens += self._messages[0].tokens
            dropped += 1
            self._messages = self._messages[1:]
            evidence.append(_ev(COMPRESSED, "the summary note did not fit beside the newest message, so it was "
                                            "dropped as well", failure_mode="compaction_lost_decision"))
        evidence[0]["message"] += f"; {dropped} more message(s) dropped ({tokens} tokens) to fit" if dropped else ""
        return self._report("compress", evidence, request_id, run_id, dropped=dropped,
                            dropped_tokens=tokens + folded_tokens, summarised=folded)

    # -- helpers ---------------------------------------------------------------------------------------------------

    def _too_small(self) -> bool:
        newest = self._messages[-1].tokens if self._messages else 0
        return self.system_tokens + newest > self.budget_tokens

    def _drop(self, priority: list[int]) -> tuple[int, int]:
        """Drop from the least valued end of ``priority`` (indices into the messages) until the window fits; the kept
        messages stay in conversation order."""
        used, gone = self.used_tokens, set()
        for index in reversed(priority):
            if used <= self.budget_tokens:
                break
            used -= self._messages[index].tokens
            gone.add(index)
        tokens = sum(self._messages[i].tokens for i in gone)
        self._messages = [m for i, m in enumerate(self._messages) if i not in gone]
        return len(gone), tokens

    def _rerank(self, reranker: Reranker, droppable: list[int]) -> tuple[list[int], dict[str, Any]]:
        candidates = [{"index": i, "role": self._messages[i].role,
                       "snippet": scrub(self._messages[i].content)[:_SNIPPET],
                       "token_estimate": self._messages[i].tokens} for i in droppable]
        default = list(reversed(droppable))
        try:
            answer = reranker(scrub(self._messages[-1].content)[:2000], candidates)
            if isinstance(answer, str | bytes | Mapping) or not isinstance(answer, Iterable):
                raise TypeError(f"expected a sequence of indices, got {type(answer).__name__}")
            answer = list(answer)
        except Exception as exc:  # noqa: BLE001 - injected code; the failure is evidence, never hidden
            return default, _ev("G6_E_CONTEXT_RERANKER_FALLBACK", "the reranker failed, so the newest messages were "
                                "kept first: " + scrub(f"{type(exc).__name__}: {exc}")[:200])
        allowed, order = set(droppable), []
        for value in answer:
            if not isinstance(value, bool) and isinstance(value, int) and value in allowed and value not in order:
                order.append(value)
        if not order:
            return default, _ev("G6_E_CONTEXT_RERANKER_FALLBACK", "the reranker named no valid candidate index, so "
                                "the newest messages were kept first")
        rest = [i for i in default if i not in order]
        return order + rest, _ev("G6_E_CONTEXT_RERANKER_USED", f"{len(candidates)} masked candidate(s) were sent to "
                                 f"the injected reranker; it named {len(order)} to keep first, and the budget was "
                                 "applied after it")

    def _summary(self, turns: list[dict[str, str]],
                 summarise: Summariser | None) -> tuple[str, list[dict[str, Any]]]:
        lossy = (f"{len(turns)} message(s) folded into one summary note, an untrusted memory_note sent as a quoted "
                 "user turn; it is lossy")
        if summarise is not None:
            try:
                text = summarise([dict(turn) for turn in turns])
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("it returned no text")
                return text.strip(), [_ev(COMPRESSED, lossy + ", written by the injected summariser", method="injected",
                                          failure_mode="compaction_lost_decision")]
            except Exception as exc:  # noqa: BLE001 - injected code; fall back and say so
                fallback = _ev(SUMMARY_FALLBACK, "the injected summariser failed, so each message's first sentence "
                               "was kept instead: " + scrub(f"{type(exc).__name__}: {exc}")[:200])
                return first_sentences(turns), [_ev(COMPRESSED, lossy + ": each message's first sentence only",
                                                    method="first_sentence", failure_mode="compaction_lost_decision"),
                                                fallback]
        return first_sentences(turns), [_ev(COMPRESSED, lossy + ": each message's first sentence only",
                                            method="first_sentence", failure_mode="compaction_lost_decision")]

    def _report(self, op: Literal["trim", "compress"], evidence: list[dict[str, Any]], request_id: str, run_id: str,
                *, dropped: int = 0, dropped_tokens: int = 0, summarised: int = 0) -> WindowReport:
        fits = self.fits()
        if not fits:
            newest = self._messages[-1].tokens if self._messages else 0
            evidence.insert(0, _ev(TOO_SMALL, f"the system prompt ({self.system_tokens} tokens) and the newest message "
                                              f"({newest}) alone exceed the budget of {self.budget_tokens} (the "
                                              f"window of {self.window_tokens} less {self.output_reserve} kept for "
                                              "the answer); nothing was dropped"))
        evidence.append({"code": "G6_E_CONTEXT_WINDOW_COUNT", "message": "how the window was counted",
                         "source": "window", "counter": self.counter_name, "margin": self.margin,
                         "message_overhead": MESSAGE_OVERHEAD, "system_tokens": self.system_tokens,
                         "used_tokens": self.used_tokens, "output_reserve": self.output_reserve,
                         "window_tokens": self.window_tokens})
        state: Literal["qualified-draft", "blocked-escalated"] = "qualified-draft" if fits else "blocked-escalated"
        first = evidence[0]
        if first["code"] == "G6_E_CONTEXT_WINDOW_COUNT":
            code, message = ESTIMATE, (f"the window fits by an estimate: {self.counter_name} with a margin of "
                                       f"{self.margin:g}, not the model's own count")
        else:
            code, message = first["code"], first["message"]
        action = ("Shorten the system prompt or the newest message, or use a model with a larger window."
                  if not fits else "Check that nothing the answer needs was dropped or summarised away.")
        card = warning_card(code, message, action)
        card.update(completion_state=state, reasons=[e["message"] for e in evidence[:-1]])
        return WindowReport(op=op, fits=fits, window_tokens=self.window_tokens, output_reserve=self.output_reserve,
                            budget_tokens=self.budget_tokens, used_tokens=self.used_tokens,
                            messages=len(self._messages), dropped=dropped, dropped_tokens=dropped_tokens,
                            summarised=summarised, completion_state=state, warning_card=card, evidence=evidence,
                            request_id=request_id, run_id=run_id)


__all__ = ["DEFAULT_MARGIN", "INJECTED_MARGIN", "MESSAGE_OVERHEAD", "NOTE_PREFIX", "ROLES", "ConversationWindow",
           "WindowMessage", "WindowReport", "first_sentences"]
