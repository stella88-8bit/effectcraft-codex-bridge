# Engineering instructions

- Target Python 3.10+ and the standard library. Keep the runtime free of third-party dependencies. Preserve Python 3.10–3.13 CI compatibility.
- EffectCraft is pinned to v0.13.0 / `de3fec28ed7a3d7a700a9d8ea307779dcda99a8e`. Treat the linked official docs and native command schemas as authoritative. Do not invent engine commands or duplicate the native MCP catalog.
- MCP delegates to the official CLI over stdio. Keep stdout protocol-clean. Live and headless are different sessions; never silently fall back between them.
- Preserve existing user work. Do not automatically quit/restart an attached app, clear/open/replace a project, or overwrite output files. Use new compositions and explicit output paths for real integration checks.
- HTTP must remain loopback-only, authenticated on every route, with strict Host/Origin/JSON validation and bounded input. Do not log tokens, authorization headers or command params. Preserve native domain results.
- Never automatically retry API/control requests after transmission failure. Preserve `outcome_unknown`; inspect state before an explicit retry. Keep the difference between HTTP, native control and JSON-RPC MCP clear.
- A valid token grants editor/file/script authority: do not describe the API as a file sandbox. Native MCP confinement has separate upstream limitations.
- Run `python3 -m unittest discover -s tests -v` after substantive changes. Add focused regression tests for transport, input validation and security boundaries. Run `python3 -m compileall -q effectcraft_codex bridge.py examples` before handoff.
- Live smoke checks are opt-in and require an installed app; `scripts/live_smoke.py` mutates the attached project by adding a composition. Report real GUI/API/MCP verification separately from mock/unit checks.
- Keep user-facing documentation in Russian. Document actual CLI options and version-pinned upstream links. Registration does not prove tools are loaded in the current chat: mention MCP reload/new chat when relevant.
- Do not commit `.state`, `.runtime`, tokens, personal absolute paths, user projects, logs, temporary QA renders or bundled upstream binaries. Curated documentation illustrations under `docs/assets` are allowed. Review staged content before publishing.
