# Paperclip + Hermes + OneBridge bridge

This integration deliberately uses Paperclip's built-in Hermes runtime and OneBridge's existing REST control plane. It does not add a custom Paperclip Agent Runtime or modify either upstream project.

## Responsibilities

- Paperclip: company, agents, goals, budgets, issue assignment, and heartbeat scheduling.
- Hermes Agent: reasoning, web search, browser research, and tool selection. Use Paperclip's built-in hermes_local adapter for this integration. hermes_gateway requires separate environment-transport qualification before using this skill.
- OneBridge: durable production tasks, worker dispatch, artifact manifests, approval state, audit, and release-gate evaluation.

Hermes calls OneBridge through the onebridge-control Hermes skill. This avoids a second Paperclip-to-OneBridge task scheduler and keeps Paperclip's native Hermes heartbeat contract intact.

## Install the Hermes skill

Copy the complete skills/onebridge-control directory into the Hermes skills directory:

- Windows: %USERPROFILE%\.hermes\skills\onebridge-control\
- macOS/Linux: ~/.hermes/skills/onebridge-control/

The directory must contain SKILL.md and scripts/onebridge.py. Paperclip's built-in Hermes adapter scans Hermes-installed skills under ~/.hermes/skills/ (or the configured HERMES_HOME).

## Configure the Paperclip-managed Hermes agent

Provide only the following OneBridge values through the agent's protected environment configuration. Do not put API keys in the prompt template.

    ONEBRIDGE_BASE_URL=http://127.0.0.1:8080
    ONEBRIDGE_API_KEY=ob_<workspace-scoped-key>
    ONEBRIDGE_TENANT_ID=ws_<workspace-id>

Paperclip injects PAPERCLIP_API_URL, PAPERCLIP_API_KEY, PAPERCLIP_AGENT_ID, PAPERCLIP_COMPANY_ID, and PAPERCLIP_RUN_ID for local Hermes runs; PAPERCLIP_TASK_ID is present when a task triggered the wake. Do not manually configure or override PAPERCLIP_API_KEY. Preflight uses that short-lived run token only for the read-only GET /api/agents/me identity check.

For a remote OneBridge service, use HTTPS and a private network. Plain HTTP is accepted only for loopback addresses.

On OneBridge, enable:

    ONEBRIDGE_REQUIRE_API_KEY=true
    ONEBRIDGE_TASK_QUEUE_URL=postgresql://...

Run the OneBridge API and durable worker. The API key's workspace must match ONEBRIDGE_TENANT_ID.

## Preflight and current output limits

Run:

    python onebridge.py preflight --paperclip-context --outputs content

The read-only preflight verifies:
- Paperclip's current run token via GET /api/agents/me, matching injected agent ID and company ID;
- OneBridge API-key authentication and exact workspace/tenant match via GET /api/v1/auth/me;
- durable task queue configuration;
- healthy, non-mock adapters that declare the requested output capability.

It never creates a task, dispatches a worker, approves an artifact, or changes state.

Preflight verifies queue configuration, not that a worker process is currently alive. A wait timeout leaves the durable task in OneBridge and does not cancel it.

Output routing follows OneBridge's current routes:

| Output | Required adapter |
|---|---|
| content | flowise |
| design | open_design |
| code, test_report | hermes |

The current OneBridge Hermes command adapter requires a strong sandbox. Its present sandbox implementation does not support native Windows workers and isolates network access on Linux/macOS. Therefore code/test-report jobs are blocked by preflight by default. Only an operator may set ONEBRIDGE_HERMES_WORKER_QUALIFIED=1 after real end-to-end qualification of the worker and its model connectivity. This integration does not weaken that sandbox.

## Operator approval

The normal agent key can submit and inspect tasks, but cannot approve artifacts through this helper. The optional approve command requires a separate operator-only ONEBRIDGE_APPROVER_API_KEY and ONEBRIDGE_APPROVER_ID. Do not expose those values to autonomous Hermes agents.

The helper intentionally exposes no release, retry, or cancel command. In particular, the current OneBridge cancel endpoint updates task state but does not yet prove that an already-running worker process has stopped.

## Tests

The bridge uses only the Python standard library. Its contract tests live in the repository's normal tests suite and run with:

    python -m pytest tests/test_paperclip_onebridge_bridge.py

No live Paperclip, Hermes, OneBridge server, payment provider, or autonomous agent is required for these tests.