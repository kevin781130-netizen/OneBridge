# Third-party notices

This initial OneBridge commit does not vendor third-party source code.

Runtime dependencies are installed from their normal package distributions and retain their own licenses, including FastAPI, Pydantic, SQLAlchemy, Uvicorn, boto3, and their transitive dependencies.

Upstream AI tools such as OpenClaw, Flowise, Open Design, Hermes, LangGraph, MCP SDKs, and ContextForge are not embedded in the core by this commit. When added, their exact versions, licenses, and source provenance must be recorded here or in a generated release compliance bundle.
