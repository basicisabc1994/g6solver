"""Offline synthetic illustration. Does not purport to validate a market claim."""
from g6.cognitive_bridge import (
    CognitiveAgent, Hypothesis, LLMProposer, Observation, ToolSpec,
)

# A real deployment injects an approved G6 LLMCaller and a real, audited research tool.
# This fake model only makes the example deterministic, offline and cost-free.
class ScriptedModel:
    def complete(self, system, user, *, max_tokens):
        return '''{"probes": [{"id":"contrast-pricing", "question":"In independently collected interviews, is switching cost the dominant barrier?", "tool":"interview_index", "arguments":{"question_id":"Q1"}, "positive_if":{"switching":true,"discoverability":false},"novelty":0.6}]}'''


def approved_interview_reader(question, args):
    # REPLACE with an authenticated, source-citing research database, not made-up data.
    return Observation(positive=True, source_ref="synthetic-demo:interviews/Q1",
                       notes="Synthetic observation for demonstrating control flow only")


tool = ToolSpec("interview_index", approved_interview_reader, assumed_match_probability=.8, cost=1)
model = ScriptedModel()
proposer = LLMProposer(model, {"interview_index": tool})
agent = CognitiveAgent(proposer, {"interview_index": tool})
answer = agent.run("Find a defensible unmet need in workflow software", [
    Hypothesis("switching", "Customer switching cost is the chief obstacle", 0.5),
    Hypothesis("discoverability", "The problem is primarily product discoverability", 0.5),
], budget=1, max_steps=1)
print(answer)
