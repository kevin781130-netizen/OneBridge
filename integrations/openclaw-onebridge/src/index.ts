import { Type } from "typebox";
import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";

const configSchema = Type.Object(
  {
    baseUrl: Type.String({ description: "OneBridge control-plane base URL." }),
    apiKey: Type.Optional(Type.String({ description: "OneBridge bearer API key." })),
  },
  { additionalProperties: false },
);

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

async function callOneBridge(
  baseUrl: string,
  apiKey: string | undefined,
  path: string,
  init: RequestInit,
  signal?: AbortSignal,
): Promise<Json> {
  const base = new URL(baseUrl);
  if (!["http:", "https:"].includes(base.protocol)) throw new Error("OneBridge baseUrl must be HTTP(S)");
  if (base.username || base.password || base.search || base.hash) throw new Error("OneBridge baseUrl must not include credentials, query, or fragment");

  const url = new URL(path, base.toString().replace(/\/$/, "") + "/");
  if (url.origin !== base.origin) throw new Error("OneBridge request origin mismatch");

  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body !== undefined) headers.set("Content-Type", "application/json");
  if (apiKey) headers.set("Authorization", "Bearer " + apiKey);

  const response = await fetch(url, {
    ...init,
    headers,
    signal: signal ?? null,
    redirect: "error",
  });
  const text = await response.text();
  let payload: Json = {};
  if (text) {
    try {
      payload = JSON.parse(text) as Json;
    } catch {
      throw new Error("OneBridge returned invalid JSON");
    }
  }
  if (!response.ok) throw new Error("OneBridge HTTP " + response.status);
  return payload;
}

export default defineToolPlugin({
  id: "onebridge",
  name: "OneBridge",
  description: "Use the OneBridge production control plane for durable AI artifact workflows.",
  configSchema,
  tools: (tool) => [
    tool({
      name: "onebridge_submit",
      label: "Submit OneBridge Task",
      description: "Submit a durable production task. OneBridge owns workflow state, artifacts, approvals, and audit history.",
      parameters: Type.Object(
        {
          goal: Type.String(),
          requiredOutputs: Type.Array(
            Type.Union([
              Type.Literal("content"),
              Type.Literal("design"),
              Type.Literal("code"),
              Type.Literal("test_report"),
            ]),
          ),
          tenantId: Type.String(),
          userId: Type.String(),
          conversationId: Type.Optional(Type.String()),
          approval: Type.Optional(Type.Union([Type.Literal("none"), Type.Literal("before_publish")])),
        },
        { additionalProperties: false },
      ),
      async execute(params, config, context) {
        context.signal?.throwIfAborted();
        return callOneBridge(
          config.baseUrl,
          config.apiKey,
          "/api/v1/tasks",
          {
            method: "POST",
            body: JSON.stringify({
              input: { goal: params.goal, required_outputs: params.requiredOutputs },
              context: {
                tenant_id: params.tenantId,
                user_id: params.userId,
                channel: "openclaw",
                conversation_id: params.conversationId ?? null,
              },
              policy: { approval: params.approval ?? "before_publish" },
            }),
          },
          context.signal,
        );
      },
    }),
    tool({
      name: "onebridge_status",
      label: "OneBridge Task Status",
      description: "Read the current durable OneBridge task state.",
      parameters: Type.Object({ taskId: Type.String() }, { additionalProperties: false }),
      execute: ({ taskId }, config, context) =>
        callOneBridge(config.baseUrl, config.apiKey, "/api/v1/tasks/" + encodeURIComponent(taskId), { method: "GET" }, context.signal),
    }),
    tool({
      name: "onebridge_artifacts",
      label: "OneBridge Artifacts",
      description: "List immutable artifacts for a OneBridge task.",
      parameters: Type.Object({ taskId: Type.String() }, { additionalProperties: false }),
      execute: ({ taskId }, config, context) =>
        callOneBridge(config.baseUrl, config.apiKey, "/api/v1/tasks/" + encodeURIComponent(taskId) + "/artifacts", { method: "GET" }, context.signal),
    }),
    tool({
      name: "onebridge_approve",
      label: "Review OneBridge Artifacts",
      description: "Approve or reject selected OneBridge artifacts.",
      parameters: Type.Object(
        {
          taskId: Type.String(),
          artifactIds: Type.Array(Type.String(), { minItems: 1 }),
          decision: Type.Union([Type.Literal("approve"), Type.Literal("reject")]),
          actor: Type.String(),
          reason: Type.Optional(Type.String()),
        },
        { additionalProperties: false },
      ),
      execute: (params, config, context) =>
        callOneBridge(
          config.baseUrl,
          config.apiKey,
          "/api/v1/tasks/" + encodeURIComponent(params.taskId) + "/approve",
          {
            method: "POST",
            body: JSON.stringify({
              artifact_ids: params.artifactIds,
              decision: params.decision,
              actor: params.actor,
              reason: params.reason ?? "",
            }),
          },
          context.signal,
        ),
    }),
    tool({
      name: "onebridge_retry",
      label: "Retry OneBridge Task",
      description: "Requeue a failed or blocked OneBridge task.",
      parameters: Type.Object({ taskId: Type.String() }, { additionalProperties: false }),
      execute: ({ taskId }, config, context) =>
        callOneBridge(config.baseUrl, config.apiKey, "/api/v1/tasks/" + encodeURIComponent(taskId) + "/retry", { method: "POST" }, context.signal),
    }),
    tool({
      name: "onebridge_cancel",
      label: "Cancel OneBridge Task",
      description: "Cancel a OneBridge task that has not completed.",
      parameters: Type.Object({ taskId: Type.String() }, { additionalProperties: false }),
      execute: ({ taskId }, config, context) =>
        callOneBridge(config.baseUrl, config.apiKey, "/api/v1/tasks/" + encodeURIComponent(taskId) + "/cancel", { method: "POST" }, context.signal),
    }),
  ],
});
