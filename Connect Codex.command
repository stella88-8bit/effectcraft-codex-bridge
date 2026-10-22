#!/bin/zsh
set -eu
cd -- "${0:A:h}"
if [[ ! -x .runtime/bin/effectcraft-cli && -z "${EFFECTCRAFT_CLI:-}" ]]; then
  python3 scripts/fetch_cli.py
fi
python3 bridge.py install-codex
print '\nТеперь перезапустите MCP-сервер effectcraft-codex в настройках Codex или откройте новый чат.'
read '?Нажмите Enter, чтобы закрыть окно.'
