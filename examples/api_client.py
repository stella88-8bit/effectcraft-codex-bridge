#!/usr/bin/env python3
"""Локальный пример API-клиента без внешних зависимостей и без вывода токена.

Запуск из корня репозитория:
  python3 examples/api_client.py --token-file .state/api-token state
  python3 examples/api_client.py --token-file .state/api-token commands --filter comp
  python3 examples/api_client.py --token-file .state/api-token command comp.new \
      --params '{"name":"API Example","width":640,"height":360}'

Клиент намеренно не поддерживает удалённый host, redirect и автоматический retry.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import http.client
import json
import math
import os
from pathlib import Path
import stat
import sys
from urllib.parse import urlencode

MAX_RESPONSE = 64 * 1024 * 1024


class ApiError(RuntimeError):
    """Ошибка запроса; неопределённый результат требует ручной проверки."""


def json_object(value: str) -> dict:
    def invalid_constant(_value):
        raise ValueError("NaN/Infinity are not JSON values")

    def finite_float(number):
        parsed = float(number)
        if not math.isfinite(parsed):
            raise ValueError("JSON number must be finite")
        return parsed

    def unique_object(pairs):
        parsed = {}
        for key, item in pairs:
            if key in parsed:
                raise ValueError("Duplicate JSON object key")
            parsed[key] = item
        return parsed

    try:
        result = json.loads(value, parse_constant=invalid_constant,
                            parse_float=finite_float, object_pairs_hook=unique_object)
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError("--params должен содержать корректный JSON") from exc
    if not isinstance(result, dict):
        raise argparse.ArgumentTypeError("--params должен быть JSON-объектом")
    return result


def port_number(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError("порт должен быть от 1 до 65535")
    return number


def finite_time(value: str) -> float:
    try:
        number = float(value)
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError("время должно быть конечным числом") from exc
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError("время должно быть конечным числом")
    return number


def positive_timeout(value: str) -> float:
    number = finite_time(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("тайм-аут должен быть положительным конечным числом")
    return number


def read_token(path: Path) -> str:
    # Runtime также запрещает symlink и создаёт token с mode0600.
    if path.is_symlink():
        raise ApiError("Token-файл не должен быть символической ссылкой")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ApiError("Token-файл должен быть обычным файлом")
    if os.name != "nt" and info.st_mode & 0o077:
        raise ApiError("Token-файл доступен другим пользователям: задайте права 0600")
    if info.st_size > 4096:
        raise ApiError("Некорректный размер token-файла")
    token = path.read_text(encoding="ascii").strip()
    if len(token) < 32 or any(character.isspace() for character in token):
        raise ApiError("Некорректный token-файл")
    return token


class Client:
    def __init__(self, token_file: Path, api_port: int = 8767, timeout: float = 70):
        self._timeout = positive_timeout(timeout)
        self._token = read_token(token_file)
        self._port = api_port

    def request(self, method: str, path: str, body: dict | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self._port, timeout=self._timeout)
        headers = {"Authorization": "Bearer " + self._token, "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            content = response.read(MAX_RESPONSE + 1)
            if len(content) > MAX_RESPONSE:
                raise ApiError("Ответ слишком велик; состояние запроса нужно проверить отдельно")
            try:
                payload = json.loads(content)
            except (ValueError, UnicodeDecodeError) as exc:
                raise ApiError("Ответ не является JSON; не повторяйте изменение без проверки") from exc
            if not isinstance(payload, dict):
                raise ApiError("Некорректная оболочка ответа API")
            if not 200 <= response.status < 300 or payload.get("ok") is not True:
                error = payload.get("error")
                if not isinstance(error, dict):
                    raise ApiError("HTTP {}: некорректный ответ об ошибке".format(response.status))
                message = "HTTP {} / {}: {}".format(response.status, error.get("code", "error"), error.get("message", "API error"))
                if error.get("outcome_unknown"):
                    message += "\nРезультат неопределён: сначала проверьте проект, затем решайте о повторе."
                raise ApiError(message)
            if "result" not in payload:
                raise ApiError("В успешном ответе отсутствует result")
            return payload["result"]
        except (OSError, http.client.HTTPException) as exc:
            # Не выводим repr запроса/headers и не повторяем его.
            raise ApiError("Соединение прервалось. Команда могла исполниться; проверьте состояние до повтора.") from exc
        finally:
            connection.close()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument("--token-file", type=Path, required=True, help="runtime token-файл, по умолчанию сервера .state/api-token")
    result.add_argument("--api-port", type=port_number, default=8767)
    result.add_argument("--timeout", type=positive_timeout, default=70,
                        help="положительный конечный тайм-аут в секундах (по умолчанию 70)")
    subparsers = result.add_subparsers(dest="action", required=True)
    subparsers.add_parser("health", help="проверить API и подключение редактора")
    subparsers.add_parser("state", help="состояние редактора")
    subparsers.add_parser("ui", help="состояние окна")
    subparsers.add_parser("openapi", help="спецификация HTTP API")
    commands = subparsers.add_parser("commands", help="найти схемы команд")
    commands.add_argument("--filter", default="")
    command = subparsers.add_parser("command", help="выполнить команду движка")
    command.add_argument("command")
    command.add_argument("--params", type=json_object, default={})
    rpc = subparsers.add_parser("rpc", help="вызвать метод нативного control protocol")
    rpc.add_argument("method")
    rpc.add_argument("--params", type=json_object, default={})
    render = subparsers.add_parser("render", help="сохранить PNG; существующий файл не перезаписывается")
    render.add_argument("--comp", help="id или имя композиции; без него — активная")
    render.add_argument("--time", type=finite_time, default=1.5)
    render.add_argument("--max-side", type=int, default=1280)
    render.add_argument("--out", type=Path, required=True)
    return result


def main(argv=None) -> int:
    arguments = parser().parse_args(argv)
    try:
        client = Client(arguments.token_file, arguments.api_port, arguments.timeout)
        routes = {"health": "/health", "state": "/v1/state", "ui": "/v1/ui", "openapi": "/v1/openapi.json"}
        if arguments.action in routes:
            result = client.request("GET", routes[arguments.action])
        elif arguments.action == "commands":
            result = client.request("GET", "/v1/commands?" + urlencode({"filter": arguments.filter}))
        elif arguments.action == "command":
            result = client.request("POST", "/v1/command", {"command": arguments.command, "params": arguments.params})
        elif arguments.action == "rpc":
            result = client.request("POST", "/v1/rpc", {"method": arguments.method, "params": arguments.params})
        else:
            if arguments.out.exists():
                raise ApiError("Выходной файл уже существует; выберите другое имя")
            body = {"time": arguments.time, "max_side": arguments.max_side}
            if arguments.comp is not None:
                body["comp"] = arguments.comp
            result = client.request("POST", "/v1/render-frame", body)
            if not isinstance(result, dict) or not isinstance(result.get("png"), str):
                raise ApiError("В ответе рендера отсутствует PNG base64")
            png = base64.b64decode(result["png"], validate=True)
            if not png.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ApiError("Полученный кадр не является PNG")
            with arguments.out.open("xb") as stream:
                stream.write(png)
            result = {key: value for key, value in result.items() if key != "png"}
            result["saved_to"] = str(arguments.out.resolve())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ApiError, OSError, UnicodeError, ValueError, binascii.Error) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
