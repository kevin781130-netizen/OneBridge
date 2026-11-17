---
name: onebridge-control
description: Submit and track durable, review-gated production tasks in OneBridge from a Paperclip-managed Hermes agent. Use for artifact production and workflow tracking, not for general web research.
version: 0.1.0
---

# OneBridge Control

Use this skill when a Paperclip-managed Hermes agent needs OneBridge to create durable production artifacts, track execution, or inspect approval and release readiness.

Hermes remains responsible for web research, browsing, source verification, and deciding what work to request. OneBridge is the durable production workflow, artifact, approval, and audit service.

## Required environment

Configure only these OneBridge values in the Paperclip Hermes agent's protected environment settings. Never print or place credential values in prompts, comments, or logs.

- ONEBRIDGE_BASE_URL: OneBridge service root, for example http://127.0.0.1:8080. Remote endpoints must use HTTPS.
- ONEBRIDGE_API_KEY: workspace-scoped OneBridge API key.
- ONEBRIDGE_TENANT_ID: the workspace ID matching that key.

Paperclip injects PAPERCLIP_API_URL, PAPERCLIP_API_KEY, PAPERCLIP_AGENT_ID, PAPERCLIP_COMPANY_ID, and PAPERCLIP_RUN_ID for local Hermes runs. PAPERCLIP_TASK_ID is supplied when a task triggered the wake. Do not configure or override any PAPERCLIP_* identity variable or copy PAPERCLIP_API_KEY into ONEBRIDGE_API_KEY. The helper verifies the live Paperclip agent/company identity using the short-lived Paperclip token and separately verifies the OneBridge key's workspace.

## Workflow

1. Run preflight before submitting work.

   Windows PowerShell:

       python "$env:USERPROFILE\.hermes\skills\onebridge-control\scripts\onebridge.py" preflight --paperclip-context --outputs content

   macOS/Linux:

       python "$HOME/.hermes/skills/onebridge-control/scripts/onebridge.py" preflight --paperclip-context --outputs content

2. Research and prepare the task brief yourself. Include source URLs and factual constraints in the brief when relevant. Do not ask OneBridge to perform web scraping unless its selected output adapter is explicitly configured and qualified for that task.

3. Submit with the narrowest required output set. The default approval policy is before_publish, and network policy is always restricted:

   Windows PowerShell:

       python "$env:USERPROFILE\.hermes\skills\onebridge-control\scripts\onebridge.py" submit --goal-file task-brief.md --outputs content

   macOS/Linux:

       python "$HOME/.hermes/skills/onebridge-control/scripts/onebridge.py" submit --goal-file task-brief.md --outputs content

   Supported output types are content, design, code, and test_report. Preflight checks adapter health and capabilities. Code and test_report are deliberately blocked unless an operator has set ONEBRIDGE_HERMES_WORKER_QUALIFIED=1 after a real end-to-end worker qualification. Do not set that flag yourself. If an adapter is unavailable, stop and report the blocker; do not submit anyway.

4. Record the returned task_id. Wait for completion:

   Windows PowerShell:

       python "$env:USERPROFILE\.hermes\skills\onebridge-control\scripts\onebridge.py" wait --task-id TASK_ID --timeout-sec 300

   macOS/Linux:

       python "$HOME/.hermes/skills/onebridge-control/scripts/onebridge.py" wait --task-id TASK_ID --timeout-sec 300

   A timeout only stops waiting; it does not cancel the durable OneBridge task. Re-run status or wait later. Re-submitting the same issue brief is idempotent and returns the existing task.

5. Inspect artifacts:

   Windows PowerShell:

       python "$env:USERPROFILE\.hermes\skills\onebridge-control\scripts\onebridge.py" artifacts --task-id TASK_ID

   macOS/Linux:

       python "$HOME/.hermes/skills/onebridge-control/scripts/onebridge.py" artifacts --task-id TASK_ID

6. If the task reaches waiting_approval, stop and ask the authorized human approver to review it. Do not approve your own output. The approve command requires a separate ONEBRIDGE_APPROVER_API_KEY and ONEBRIDGE_APPROVER_ID; those credentials must not be installed in an autonomous agent's environment.

7. Before any operator-controlled release, inspect the read-only gate:

   Windows PowerShell:

       python "$env:USERPROFILE\.hermes\skills\onebridge-control\scripts\onebridge.py" release-gate --task-id TASK_ID

   macOS/Linux:

       python "$HOME/.hermes/skills/onebridge-control/scripts/onebridge.py" release-gate --task-id TASK_ID

   This skill intentionally provides no release, retry, or cancel command.

## Safety and idempotency

- Never print credentials or include them in task briefs.
- Do not bypass login, paywalls, rate limits, robots restrictions, or website access controls during research.
- Do not submit personal, sensitive, or access-controlled data unless the tenant has a documented lawful basis and authorization.
- Keep approval at before_publish; approval=none requires an explicit operator override outside the normal agent workflow.
- Task IDs are deterministic for the same tenant, Paperclip issue, goal, operation, output set, and approval policy. A repeated submit reuses the existing task rather than creating duplicate work.
- Use --dedupe-suffix only when a human explicitly requests a separate task with otherwise identical inputs.
- OneBridge owns task state and artifacts. Paperclip owns company organization and issue assignment. Do not create a second scheduler or poll OneBridge faster than the helper's bounded wait interval.