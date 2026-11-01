# Why Decision Forge

> *For three hundred years, every organization was built from people. The next ones will be built
> from people and agents — and the agents will outnumber the people.*

Decision Forge exists to answer one question: **how much of its work can an organization safely
entrust to AI agents?** This page explains why the answer comes down to data, why that data can only
come from inside each organization, and where Decision Forge stands in the answer.

## 1. Organizations are gaining a second workforce

The change is arriving on four fronts at once:

- **Capability.** Agents complete tasks on their own that take a human expert hours, not minutes.
- **Delegation.** People hand agents work they used to give a colleague or an outside contractor.
- **Collaboration.** Agents call other agents through open protocols such as A2A and MCP.
- **Scale.** The number of agents running inside an organization will soon exceed the number of people.

For the first time, the supply of labor is not limited by the number of people. Yet more intelligence
has not automatically produced more organizational capacity. Most agent projects stall between a
promising pilot and a production deployment.

## 2. The connection layer is ready; the trust layer is empty

Human organizations spent centuries building the machinery that lets them delegate to thousands of
people:

| What makes delegation safe | For people | For agents today |
| --- | --- | --- |
| **Foreseeing consequences** | A person knows what an action will do before taking it | Missing |
| **Staying within bounds** | Authorization, policy, and approval chains constrain each action | Missing |
| **Accountable results** | Work can be reviewed, credited, and corrected afterwards | Missing |

Agents can already reach each other's tools and data. What they lack is the layer that makes reaching
safe. Organizations fail in both directions: those that delegate too quickly see agents take actions
nobody authorized, and those that hold back never move past the pilot. Both are short of the same
thing, and it is not intelligence. It is **trust**, and trust rests on one capability: **foreseeing
the consequence of an action before it runs**.

## 3. Consequences are not in public data

Language models learned language by predicting the next word. Video and world models learn physics
by predicting the next frame. Both learn from public data, and public data is mostly
*observational*: it records what happened together, not what an action caused.

Inside an organization, every agent action is an *intervention*: reassigning a lead, sharing a client
file, approving a refund, revoking a permission. To foresee what such an action does, a model needs
three things that never appear on the public web:

| Ingredient | Meaning | Where it lives |
| --- | --- | --- |
| **Decision context** *x* | The state, roles, permissions, and options at the moment an agent acts | Workflows, tickets, CRM records, tool outputs |
| **Judgment standard** *ℓ* | What this organization considers allowed, out of bounds, or worth escalating | Handbooks, SOPs, approval rules, permission tables, human sign-offs |
| **Execution receipt** *s′* | What actually changed after the action | Approvals, outcomes, runtime logs |

The first and third are decision data. The second is *sovereign* data: two organizations can judge the
same action in opposite ways, and both can be right for themselves. There is no universal "correct
decision" that a general-purpose model could learn once and ship to everyone.

## 4. Every organization deserves its own intelligence

If the standard differs per organization, the intelligence that applies it has to be built per
organization, from that organization's own material, and it has to stay under that organization's
control. That is the role of Decision Forge:

| Ingredient | What Decision Forge does today |
| --- | --- |
| **Decision context** *x* | Reconstructs decision-time scenarios with explicit candidate actions, organizes related variants into families, and keeps each family in a single train, development, or test split |
| **Judgment standard** *ℓ* | Compiles rules from organization sources with verified quotations, requires content-bound business review, and replays the approved rules through a deterministic interpreter to label every example |
| **Execution receipt** *s′* | Ingests runtime events and reviewed outcomes, and turns reviewed development errors into requests for the next dataset version without touching held-out families |

The core runs locally and offline. Source documents reach a model endpoint only through an explicit
command, and the resulting datasets, review records, and caches stay in the organization's own
storage.

## 5. The stack

Decision Forge is the data layer of a three-layer stack:

```text
AI-native organizations   people and agents allocate, share, act, and accept together
        ▲      │ execution receipts
        │      ▼
Laplace                   causal-interventional model: predict the consequence, then allow,
        ▲                 block, or hand the action back to a person            (in development)
        │ verified decision data
Decision Forge            organization documents → decision training data, locally (open source)
```

- **Decision Forge** turns an organization's documents into verified decision training data.
- **Laplace**, Juneo Lab's causal-interventional model, learns to predict the consequence of an
  action before it runs and to make a calibrated call: allow it, block it, or return it to a person.
- **AI-native organizations** deploy it where people and agents work together. Their execution
  receipts return to the data, so each deployment teaches the model more about that organization.

The four decision surfaces in this repository are the decisions such an organization delegates:
**allocate work, share information, select actions, and evaluate outcomes.**

## 6. What exists today, and what does not yet

Decision Forge is an alpha. It already ships the offline data path, document compilation with a
review gate, distribution planning, verified synthesis, deduplication, family-level split isolation,
four training export formats, runtime event ingestion, and reviewed feedback. See
[project status](status.md) for the detailed list.

It does not yet show that training on its data improves a model's decisions. That fixed-budget
training study is the next evaluation milestone, and the [roadmap](https://github.com/Juneo-Lab/decision-forge/blob/main/ROADMAP.md)
tracks it alongside larger corpora, cross-document rule resolution, and multi-harness evaluation.
Laplace is under development and is not part of this repository.

## 7. The world we are building

Humans keep the right to decide. Agents earn the right to act. An organization can then entrust its
work to far more agents than it has people, because:

- **every action** is foreseen before it happens;
- **every delegation** narrows authority as work passes from person to agent to agent, and never
  widens it;
- **every contribution** is measured and credited once the work is done.

Payment networks authorize every transaction before it clears. We are building the same layer for
every agent action: a judgment made in real time, by the organization's own standard, before the
action happens. Decision Forge is where that standard becomes data.

**We are building the trust layer that lets human organizations keep pace with machine
intelligence.**
