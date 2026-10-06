---
description: Model switcher alias (locked — each agent carries its pinned model).
---

Model switching via this command is disabled in this project. Every agent carries its pinned model — switch AGENTS to change models.

If your system prompt contains a MODEL LOCK section naming a pinned model, you are on a locked Ops agent: output `[BLOCKED] Model locked to <pinned-model> while on <agent>. Switch agents to change models.` and stop. Do not run anything else.

Otherwise, state your current agent and its pinned model in one line and stop.
