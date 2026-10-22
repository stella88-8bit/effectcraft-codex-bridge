# Практические примеры

Команды ниже выполняются из корня этого репозитория. Используйте Python 3.10 или новее и установленный официальный EffectCraft 0.13.0; upstream CLI на macOS поставляется отдельным release asset, не внутри `.app`. Указывайте свой путь через `--cli` или `EFFECTCRAFT_CLI`, если исполняемый файл не найден автоматически.

## 1. Подключить Codex

```sh
python3 bridge.py doctor
python3 bridge.py install-codex
```

`install-codex` регистрирует сервер через `codex mcp add` под именем `effectcraft-codex`. Для другого имени добавьте `--name effectcraft-work`. Перезапустите MCP-подключение/чат или Codex и проверьте, что инструменты появились. Регистрация сама по себе не означает горячего подключения инструментов к уже открытому чату.

При первом обращении live-режим подключается к control port `9877`; если там нет EffectCraft, обёртка запускает отдельную управляемую сессию с изолированными настройками. Она не завершает ранее открытый пользовательский экземпляр. Чтобы потребовать уже работающий control channel и запретить новый запуск, используйте `mcp --no-launch` в конфигурации. Настройка существующего проекта и выбор нужного окна описаны в [архитектуре](architecture.md).

Отдельный headless-сервер нужен, если окно не требуется:

```sh
python3 bridge.py mcp --headless
```

Это stdio-сервер: его обычно запускает MCP-клиент, а не человек в интерактивном терминале. Примеры обеих конфигураций: [mcp-config.example.toml](../examples/mcp-config.example.toml). Headless не является автоматической заменой live.

## 2. Что написать Codex после подключения MCP

> Прочитай состояние EffectCraft и список композиций. Добавь в текущий проект отдельную композицию «Neon Intro», 1920×1080, 30 fps, 5 секунд. Сделай тёмный фон и крупный титр «ПРИВЕТ». Анимируй появление с Easy Ease. Покажи кадры на 0,5, 1,5 и 4 секундах, исправь обрезку текста, затем сохрани результат в новый файл в выбранной мной папке. Текущий документ не открывай и не очищай.

Модель должна сначала получить состояние и схемы команд, затем выполнить изменения, посмотреть рендер и сохранить результат. Текст запроса не отправляется в HTTP `/prompt`: такого маршрута нет.

Минимальный эквивалент через нативные MCP-инструменты:

```json
{"name":"get_state","arguments":{}}
{"name":"describe_command","arguments":{"command":"comp.new"}}
{"name":"execute_command","arguments":{"command":"comp.new","params":{"name":"Neon Intro","width":1920,"height":1080,"frameRate":30,"duration":5}}}
{"name":"execute_command","arguments":{"command":"layer.newText","params":{"text":"ПРИВЕТ","size":140,"fill":"#e0ff38"}}}
```

Это тела `tools/call`, а не четыре JSON-сообщения для HTTP API. Из результата создания слоя возьмите настоящий id. Следующие вызовы показаны с условным id `2`; замените его на полученный:

```json
{"name":"add_keyframe","arguments":{"layer":2,"path":"transform/position","keys":[{"time":0,"value":[960,700]},{"time":1.2,"value":[960,540]}],"timeBase":"comp","interpolation":"easyEase"}}
{"name":"render_frame","arguments":{"time":1.5,"max_side":1280}}
```

В API команд движка времена keyframes по умолчанию относятся к слою; явно задавайте `timeBase:"comp"` для секунд композиции. Scripting `setValueAtTime` использует секунды композиции. Идентификаторы и пути свойств проверяйте через `get_comp` / `get_layer`.

## 3. Запустить HTTP API

В отдельном терминале:

```sh
python3 bridge.py api --api-port 8767 --port 9877
```

Сервер создаёт `.state/api-token` с правами `0600`. Клиент читает файл напрямую; не нужно выводить токен в терминал или копировать в историю команд.

```sh
python3 examples/api_client.py --token-file .state/api-token health
python3 examples/api_client.py --token-file .state/api-token state
python3 examples/api_client.py --token-file .state/api-token commands --filter layer.new
python3 examples/api_client.py --token-file .state/api-token ui
python3 examples/api_client.py --token-file .state/api-token openapi
```

Если сервер запущен с `--state-dir` или другим API-портом, передайте соответствующие `--token-file` и `--api-port` клиенту. Все опции самого клиента стоят **перед** подкомандой. `--timeout` — строго положительное конечное число секунд; ноль не означает бесконечное ожидание. `render --time` принимает конечное время композиции, включая отрицательное, без изменения его значения клиентом.

### Маршруты

Все маршруты требуют `Authorization: Bearer <token>`. Тела POST имеют `Content-Type: application/json`. Неизвестные поля отвергаются. Точная актуальная схема находится в `GET /v1/openapi.json`.

| Метод и путь | Запрос | Назначение |
| --- | --- | --- |
| `GET /health` | без тела | Проверка API с чтением состояния редактора |
| `GET /v1/state` | без тела | `editor.state` |
| `GET /v1/commands?filter=comp` | строковый `filter` необязателен | `command.list` со схемами |
| `GET /v1/ui` | без тела | Нативный `ui.inspect` |
| `GET /v1/openapi.json` | без тела | OpenAPI 3.1 |
| `POST /v1/command` | `{"command":"comp.new","params":{}}` | Команда движка |
| `POST /v1/rpc` | `{"method":"ui.inspect","params":{}}` | Метод control channel |
| `POST /v1/render-frame` | `{"comp":1,"time":1.5,"max_side":1280}` | PNG base64, без записи файла сервером |

`params` можно опустить; тогда передаётся `{}`. `comp`, `time` и `max_side` в запросе кадра необязательны. `comp` — имя или id, `time` — секунды композиции, `max_side` — максимальная сторона PNG; ноль означает полный размер. Состав `result` сохраняется нативным, а не приводится к заранее придуманной модели.

### Создать композицию и титр

```sh
python3 examples/api_client.py --token-file .state/api-token command comp.new --params '{"name":"API Title","width":1280,"height":720,"frameRate":30,"duration":4}'
python3 examples/api_client.py --token-file .state/api-token command layer.newText --params '{"text":"CODEX + EFFECTCRAFT","size":68,"fill":"#e0ff38"}'
python3 examples/api_client.py --token-file .state/api-token render --comp "API Title" --time 1.5 --max-side 1280 --out title.png
```

Первые два вызова изменяют проект. Имена не обязаны быть уникальны; в автоматизации надёжнее передавать возвращённые id. Клиент намеренно не перезаписывает существующий PNG. Для сохранения `.ecproj` сначала прочитайте схему подходящей файловой команды и задайте новый явный абсолютный путь; изменение в окне ещё не означает сохранения на диск.

### Выполнить демонстрационный сценарий через API

Этот пример выполняется Python из корня репозитория. Сценарий добавляет композицию и не заменяет текущий документ. Важно проверить вложенный `ok`: ошибки scripting приходят внутри нативного результата, даже если запрос к движку технически доставлен.

```python
import base64
import json
from pathlib import Path
from examples.api_client import Client

client = Client(Path(".state/api-token"))
code = Path("examples/demo.jsx").read_text(encoding="utf-8")
script = client.request("POST", "/v1/command", {
    "command": "script.run",
    "params": {"code": code, "name": "Codex bridge demo"},
})
if not script.get("ok"):
    raise RuntimeError(script.get("error"))
created = json.loads(script["result"])
frame = client.request("POST", "/v1/render-frame", {
    "comp": created["comp"], "time": 1.5, "max_side": 1280,
})
png = base64.b64decode(frame["png"], validate=True)
with Path("demo-frame.png").open("xb") as output:
    output.write(png)
print(created)
```

Полный исходник объекта, титров и анимации: [demo.jsx](../examples/demo.jsx). Вызов сценария требует полномочий scripting; ограниченный нативный MCP с automation roots его отвергнет.

## 4. Прямой control-вызов и готовое демо

Для диагностики без HTTP можно обращаться к официальному control protocol:

```sh
python3 bridge.py call --method engine.execute --params '{"command":"editor.state","params":{}}'
python3 bridge.py call --method ui.inspect
python3 bridge.py demo --out demo-output
```

`call` не является MCP-вызовом и не проходит HTTP-аутентификацию. `demo` создаёт отдельную композицию в подключённом проекте и сохраняет демонстрационные результаты в указанную папку. Не используйте пример как оправдание для очистки или открытия чужого проекта.

## 5. Обработка сбоя

Не отправляйте команду снова вслепую после тайм-аута. Если `outcome_unknown:true`, проверьте состояние и историю: создание слоя или сохранение могли уже завершиться. Если движок вернул понятную ошибку параметров, запросите `command.describe` и исправьте поля. Если TCP-порт недоступен, сначала установите, какая сессия должна быть целью; запуск headless не восстановит связь с существующим окном.

Официальные первоисточники версии 0.13.0: [agents.md](https://github.com/storytold/effectcraft/blob/de3fec28ed7a3d7a700a9d8ea307779dcda99a8e/docs/agents.md), [mcp.md](https://github.com/storytold/effectcraft/blob/de3fec28ed7a3d7a700a9d8ea307779dcda99a8e/docs/mcp.md), [control-protocol.md](https://github.com/storytold/effectcraft/blob/de3fec28ed7a3d7a700a9d8ea307779dcda99a8e/docs/control-protocol.md).
