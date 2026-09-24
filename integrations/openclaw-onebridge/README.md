# OneBridge OpenClaw Tool Plugin

Native OpenClaw tool plugin for the OneBridge control plane.

Requires OpenClaw 2026.5.17 or later and Node 24.16+ or 26.1+.

The plugin registers:

- onebridge_submit
- onebridge_status
- onebridge_artifacts
- onebridge_approve
- onebridge_retry
- onebridge_cancel

Build and validate with an OpenClaw installation:

```bash
npm install
npm run plugin:build
npm run plugin:validate
```

Configure `baseUrl` and optionally `apiKey` in the OpenClaw plugin entry.
The plugin keeps workflow state in OneBridge and makes bounded same-origin HTTP
calls only. It does not read the OneBridge database or invoke workers directly.
