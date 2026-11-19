# SPDX-License-Identifier: Apache-2.0
"""Public task projection and bounded actor packets without filesystem retrieval."""
from __future__ import annotations

import json
from dataclasses import dataclass

from g6.context.assemble import assemble_packet, build_packet
from g6.context.packet import ContextItem, ContextPacket
from g6.context.retrieve import Retrieval
from g6.recursion.tasks import TaskSpec
from g6.rsi.records import CheckSpec, Provenance


@dataclass(frozen=True, slots=True)
class PublicTask:
    task_id: str
    family: str
    source: str
    licence_id: str
    prompt: str
    public_tests: CheckSpec
    provenance: Provenance


def public_task(task: TaskSpec) -> PublicTask:
    """Copy only actor-visible values; retain no task object or reference location."""
    return PublicTask(task.task_id, task.family, task.source, task.licence_id, task.prompt,
                      task.public_tests, task.provenance)


class _PublicOnly:
    def retrieve(self, query: str, *, budget: int) -> Retrieval:
        return Retrieval(query, (), 0)


def build_actor_packet(task: PublicTask, *, model_id: str, adapter_id: str | None, run_id: str,
                       budget: int = 1024, observations: tuple[tuple[dict, ...], ...] = ()) -> ContextPacket:
    provenance = {"task_id": task.task_id, "source": task.source, "licence_id": task.licence_id,
                  "content_digest": task.provenance.content_digest, "model_id": model_id, "adapter_id": adapter_id}
    visible = json.dumps({"prompt": task.prompt, "public_tests": task.public_tests.to_record()},
                         sort_keys=True, ensure_ascii=True)
    packet = build_packet(visible, _PublicOnly(), budget=budget, run_id=run_id, task_id=task.task_id,
                          role="actor", output_contract="Return an assistant answer or named function tool calls.")
    items = list(packet.all_items)
    for index in reversed(range(len(observations))):
        messages = observations[index]
        # An assistant call and all its replies travel as one pruning unit.
        items.append(ContextItem(key=f"turn:{index}", value=json.dumps(messages, sort_keys=True, ensure_ascii=True),
                                 section="tool", authority="model_generated", kind="model_output", trust="untrusted",
                                 source="episode", included_because="prior public turn and tool observations"))
    return assemble_packet(items, run_id=run_id, budget_tokens=budget, role="actor", task_id=task.task_id,
                           metadata={**packet.metadata, "provenance": provenance})


__all__ = ["PublicTask", "public_task", "build_actor_packet"]
