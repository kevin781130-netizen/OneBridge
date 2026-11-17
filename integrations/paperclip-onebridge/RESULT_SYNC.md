# OneBridge -> Paperclip result-card synchronization (P1)

**Operator-triggered / read-only by default. No scheduler, Agent, install, or additional Python dependencies.** This script lives on a separate feature branch and does not alter Paperclip upstream or OneBridge Main.

## Behavior

\`sync_result.py\` reads the *authoritative* Paperclip issue using the existing HTTP Bridge client and derives the deterministic OneBridge task/project IDs. It then reads authenticated OneBridge task status and up to 50 artifact manifests, validates project and artifact scope, and builds a small Paperclip work-product card with a link to the **OneBridge Review Portal** (\`/review/{task_id}\`).

- The card is a \`preview_url\` type, \`provider=onebridge\`, stable \`externalId=task_id\`.
- The visible summary includes task status, artifact count and a digest of sorted artifact manifests. It **does not** attach artifact bytes, leak API keys, or copy generated content.
- On explicit apply, reads existing issue work products, creates once or patches only if changed. Duplicate cards fail closed; re-run with unchanged status/artifacts produces no write.
- Paperclip issue status, ownership, approvals and releases are **never changed**.
- OneBridge approvals/releases are **never changed**; any \`waiting_approval\` state must be handled in OneBridge.
- Uses independently provisioned Paperclip read and write keys. Operator must supply \`PAPERCLIP_RESULT_SYNC_LIVE=1\` **and** the command's \`--apply\` flag to enable any Paperclip write.
- Doesn't automatically poll or schedule. Run again to refresh the status or artifact digest. (Automation is a separate future, explicitly authorized feature.)

## Zero-install test

From OneBridge repo root:

    python3 -m unittest discover -s tests -p test_paperclip_result_sync.py -v

The test suite uses the Python standard library only and temporary localhost fake
Paperclip and OneBridge HTTP endpoints. It covers preview, create/unchanged/update,
manifest digest, rejected tenant/agent/issue/artifact scope, missing write key and
duplicate protection. **Passing Mock tests is not equivalent to production live success.**

## Operator usage (ONLY when both real services are running)

Supply the original HTTP Bridge connection variables from
\`integrations/paperclip-onebridge/HTTP_BRIDGE.md\`. Keep tokens in a protected
secrets environment; do not write them into Git.

Read-only preview:

    python3 integrations/paperclip-onebridge/scripts/sync_result.py \
      --issue-id '<actual Paperclip issue UUID>' \
      --agent-id '<actual assigned Paperclip agent UUID>'

Enable actual Paperclip work-product writes only after the correct agent and company
are confirmed and the write key is scoped to the intended issue:

    # Set PAPERCLIP_WRITE_API_KEY and PAPERCLIP_RESULT_SYNC_LIVE=1 securely.
    python3 integrations/paperclip-onebridge/scripts/sync_result.py \
      --issue-id '<actual Paperclip issue UUID>' \
      --agent-id '<actual assigned Paperclip agent UUID>' --apply

Read access to Paperclip and OneBridge is required even for preview. The live write
API may reject calls due to actor/run permission gates; an authorization error is a
hard stop, not a reason to widen permissions. Do **not** use an administrator credential
to bypass agent-bound work-product restrictions. A Paperclip issue title/description
change can change the derived task ID; there is no persisted mapping for that case.

## Safety and limitations

- No report is written to Paperclip unless the operator explicitly runs \`--apply\`
  with \`PAPERCLIP_RESULT_SYNC_LIVE=1\` and a distinct authorized write key.
- This is **result visibility**, not Paperclip issue completion. Paperclip's HTTP
  adapter completes on successful webhook acknowledgement, not actual task completion.
- Cards link to a loopback OneBridge URL and are suitable for **local-machine UAT**.
  A remote browser cannot use that localhost link; authenticated remote URL routing
  would require separate design and security review.
- One sync process is a single writer. Concurrent independent processes can race
  during the read-before-create window; no uniqueness constraint is provided by
  Paperclip's work-product API. Do not run overlapping sync instances.
- Actual end-to-end Paperclip and OneBridge services, authorization and link opening
  are **NOT_RUN** as of this branch; only mock HTTP and CI checks qualify the code.
- No OneBridge agent, payment, customer data, account, model or internet calls are
  required by the offline tests.
