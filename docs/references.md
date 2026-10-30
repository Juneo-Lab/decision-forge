# Open-source design references

Decision Forge uses original implementations for its initial core. These projects inform the planned
integration boundaries. A listed reference does not imply an implemented adapter or measured result.

| Project | Mechanism informing the design | Integration status |
| --- | --- | --- |
| [NeMo Data Designer](https://github.com/NVIDIA-NeMo/DataDesigner) | Dependency-aware generation, conditional samplers, validation, resumable batches | Planned for M2 |
| [Data-Juicer Agents](https://github.com/datajuicer/data-juicer-agents) | Explicit tool contracts and recipe planning/execution | Architectural reference |
| [LangGraph](https://github.com/langchain-ai/langgraph) | Persistent stateful orchestration | Planned for M2 |
| [Distilabel](https://github.com/argilla-io/distilabel) | Reusable step caches and data-generation recipes | Alternative adapter candidate |
| [Harbor](https://github.com/harbor-framework/harbor) | Portable tasks, environments, and verifiers | Single-step task export in v0.1; workflow execution in M3 |
| [Verifiers](https://github.com/PrimeIntellect-ai/verifiers) | Separation of task data, harness, execution, and scoring | Adapter candidate for M3 |
| [Agent Lightning](https://github.com/microsoft/agent-lightning) | Runtime/model-call capture separated from training | Evaluation planned for M4 |
| [Data-Juicer Sandbox](https://github.com/datajuicer/data-juicer-sandbox) | Feedback-driven data/model iteration | Architectural reference |

The typed-choice exporter follows the `state`, `questions`, and `gold` convention documented in
[Unsloth's decision-model training guide](https://unsloth.ai/docs/basics/train-your-own-decision-model-with-unsloth).
Training compatibility must be tested against the selected model and trainer version.
