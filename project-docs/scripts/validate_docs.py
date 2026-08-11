#!/usr/bin/env python3
"""Детерминированный валидатор документов, сгенерированных скиллом project-docs.

Проверяет ARCHITECTURE.md, TESTING.md и AGENTS.md в корне проекта на
типовые дефекты генерации:

* ссылки на файлы кода (`src/...`, `file.py:123`) существуют в проекте;
* grep-паттерны из карты логов TESTING.md реально находятся в коде;
* mermaid-блоки не содержат запрещённых конструкций (style, classDef,
  click, ID узлов с пробелами);
* в документах нет секретов, токенов и IP-адресов;
* не осталось плейсхолдеров {{...}} и HTML-комментариев-инструкций;
* AGENTS.md не превышает 150 строк.

Использует только стандартную библиотеку Python 3 (3.8+), работает в
закрытом контуре. Код завершения: 0 — всё чисто, 1 — есть замечания,
2 — ошибка запуска (например, каталог не найден).

Пример запуска:
    python3 validate_docs.py /path/to/project
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Имена проверяемых документов в корне проекта.
DOC_NAMES = ("ARCHITECTURE.md", "TESTING.md", "AGENTS.md")

# Каталоги, исключаемые при поиске фактов в коде.
EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
    "build", "target", ".idea", ".vscode", ".next", "coverage",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "vendor",
}

# Расширения файлов, в которых ищем grep-паттерны карты логов.
CODE_EXTENSIONS = {
    ".py", ".java", ".kt", ".go", ".js", ".ts", ".jsx", ".tsx",
    ".mjs", ".cs", ".rs", ".php", ".rb", ".scala",
}

# Паттерны секретов и инфраструктурных данных, запрещённых в документах.
SECRET_PATTERNS = [
    ("пароль/токен в тексте", re.compile(
        r"(?i)(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*\S+")),
    ("Bearer-токен", re.compile(r"Bearer\s+[A-Za-z0-9._\-]{10,}")),
    ("приватный ключ", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("IPv4-адрес", re.compile(r"\b(?!127\.0\.0\.1\b|0\.0\.0\.0\b)\d{1,3}(?:\.\d{1,3}){3}\b")),
]

# Запрещённые конструкции внутри mermaid-блоков.
MERMAID_FORBIDDEN = ("style ", "classDef", "click ", ":::")

# Максимальная длина AGENTS.md по best practices.
AGENTS_MAX_LINES = 150

# Ограничение на размер читаемого файла кода.
MAX_FILE_BYTES = 1_000_000


def find_docs(root: Path) -> dict:
    """Находит существующие документы скилла в корне проекта.

    Args:
        root: Корень проекта.

    Returns:
        Словарь ``{имя_документа: Path}`` для найденных файлов.
    """
    return {name: root / name for name in DOC_NAMES if (root / name).is_file()}


def collect_code_corpus(root: Path) -> list:
    """Собирает тексты всех кодовых файлов проекта для поиска паттернов.

    Args:
        root: Корень проекта.

    Returns:
        Список кортежей ``(относительный_путь, содержимое)``.
    """
    corpus = []
    for dirpath, dirnames, filenames in __import__("os").walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for name in filenames:
            path = Path(dirpath) / name
            if path.suffix.lower() not in CODE_EXTENSIONS:
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                corpus.append((path.relative_to(root).as_posix(),
                               path.read_text(encoding="utf-8", errors="ignore")))
            except OSError:
                continue
    return corpus


def check_placeholders(doc_texts: dict) -> list:
    """Ищет незаполненные плейсхолдеры и остатки инструкций шаблона.

    Args:
        doc_texts: Словарь ``{имя_документа: текст}``.

    Returns:
        Список строк-замечаний (пустой список — всё чисто).
    """
    issues = []
    for name, text in doc_texts.items():
        for match in sorted(set(re.findall(r"\{\{[A-Z_]+\}\}", text)))[:10]:
            issues.append(f"{name}: незаполненный плейсхолдер {match}")
        if "<!-- ИНСТРУКЦИЯ" in text:
            issues.append(f"{name}: остался HTML-комментарий-инструкция шаблона")
    return issues


def check_paths(root: Path, doc_texts: dict) -> list:
    """Проверяет, что упомянутые в документах пути к коду существуют.

    Распознаются backtick-токены с расширением файла, а также записи
    вида ``path/file.py:123`` (номер строки отсекается).

    Args:
        root: Корень проекта.
        doc_texts: Словарь ``{имя_документа: текст}``.

    Returns:
        Список строк-замечаний.
    """
    issues = []
    token_re = re.compile(r"`([^`\s]+\.[a-zA-Z0-9]{1,5}(?::\d+)?)`")
    for name, text in doc_texts.items():
        for token in sorted(set(token_re.findall(text))):
            file_part = token.split(":")[0]
            if "<" in file_part or file_part.startswith(("http", "{")):
                continue
            if not (root / file_part).exists():
                issues.append(f"{name}: путь не найден в проекте: `{token}`")
    return issues


def check_log_patterns(doc_texts: dict, corpus: list) -> list:
    """Проверяет, что паттерны карты логов TESTING.md находятся в коде.

    Паттерны извлекаются из колонки «Паттерн для поиска» таблиц
    разделов «Карта успешных логов» и «Логи ошибок». Каждый паттерн
    ищется сначала как regex, при ошибке компиляции — как подстрока.

    Args:
        doc_texts: Словарь ``{имя_документа: текст}``.
        corpus: Результат :func:`collect_code_corpus`.

    Returns:
        Список строк-замечаний.
    """
    text = doc_texts.get("TESTING.md")
    if text is None:
        return []
    issues = []
    pattern_idx = None
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if any("Паттерн" in c for c in cells):
            pattern_idx = next(i for i, c in enumerate(cells) if "Паттерн" in c)
            continue
        if pattern_idx is None or len(cells) <= pattern_idx:
            continue
        if set(cells[0]) <= {"-", " ", ":"}:  # строка-разделитель заголовка
            continue
        raw = cells[pattern_idx].strip("`")
        if not raw or "{{" in raw or len(raw) < 3:
            continue
        try:
            found = any(re.search(raw, content) for _, content in corpus)
        except re.error:
            found = any(raw in content for _, content in corpus)
        if not found:
            issues.append(f"TESTING.md: паттерн не найден в коде: `{raw}`")
    return issues


def check_mermaid(doc_texts: dict) -> list:
    """Проверяет mermaid-блоки на запрещённые конструкции и ID с пробелами.

    Args:
        doc_texts: Словарь ``{имя_документа: текст}``.

    Returns:
        Список строк-замечаний.
    """
    issues = []
    block_re = re.compile(r"```mermaid\n(.*?)```", re.DOTALL)
    for name, text in doc_texts.items():
        for num, block in enumerate(block_re.findall(text), start=1):
            for forbidden in MERMAID_FORBIDDEN:
                if forbidden in block:
                    issues.append(
                        f"{name}: mermaid-блок #{num}: запрещённая конструкция `{forbidden.strip()}`")
            for line in block.splitlines():
                arrow = re.match(r"^\s*([A-Za-zА-Яа-я0-9_]+(?: [A-Za-zА-Яа-я0-9_]+)+)\s*-->", line)
                if arrow:
                    issues.append(
                        f"{name}: mermaid-блок #{num}: ID узла с пробелом: `{arrow.group(1)}`")
    return issues


def check_secrets(doc_texts: dict) -> list:
    """Ищет секреты, токены и IP-адреса в документах.

    Args:
        doc_texts: Словарь ``{имя_документа: текст}``.

    Returns:
        Список строк-замечаний.
    """
    issues = []
    for name, text in doc_texts.items():
        for label, pattern in SECRET_PATTERNS:
            for match in pattern.finditer(text):
                snippet = match.group(0)[:40]
                issues.append(f"{name}: {label}: `{snippet}…`")
    return issues


def check_agents_length(doc_texts: dict) -> list:
    """Проверяет, что AGENTS.md укладывается в лимит строк.

    Args:
        doc_texts: Словарь ``{имя_документа: текст}``.

    Returns:
        Список строк-замечаний.
    """
    text = doc_texts.get("AGENTS.md")
    if text is None:
        return []
    lines = text.count("\n") + 1
    if lines > AGENTS_MAX_LINES:
        return [f"AGENTS.md: {lines} строк при лимите {AGENTS_MAX_LINES}"]
    return []


def main() -> int:
    """Точка входа: запускает все проверки и печатает отчёт.

    Returns:
        0 — замечаний нет, 1 — есть замечания, 2 — ошибка запуска.
    """
    parser = argparse.ArgumentParser(
        description="Проверяет ARCHITECTURE.md / TESTING.md / AGENTS.md на типовые дефекты.")
    parser.add_argument("project_root", nargs="?", default=".",
                        help="Корень проекта (по умолчанию — текущий каталог).")
    parser.add_argument("--json", action="store_true",
                        help="Вывести отчёт в JSON вместо текста.")
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    if not root.is_dir():
        print(f"Ошибка: каталог не найден: {root}", file=sys.stderr)
        return 2

    docs = find_docs(root)
    if not docs:
        print(f"В {root} не найдено ни одного из: {', '.join(DOC_NAMES)}")
        return 0

    doc_texts = {name: path.read_text(encoding="utf-8", errors="ignore")
                 for name, path in docs.items()}
    corpus = collect_code_corpus(root)

    checks = {
        "placeholders": check_placeholders(doc_texts),
        "paths": check_paths(root, doc_texts),
        "log_patterns": check_log_patterns(doc_texts, corpus),
        "mermaid": check_mermaid(doc_texts),
        "secrets": check_secrets(doc_texts),
        "agents_length": check_agents_length(doc_texts),
    }
    total_issues = sum(len(v) for v in checks.values())

    if args.json:
        print(json.dumps({"docs": sorted(docs), "checks": checks,
                          "total_issues": total_issues}, ensure_ascii=False, indent=2))
    else:
        print(f"Проверяемые документы: {', '.join(sorted(docs))}")
        for check, issues in checks.items():
            status = "OK" if not issues else f"FAIL ({len(issues)})"
            print(f"[{status}] {check}")
            for issue in issues:
                print(f"    - {issue}")
        print(f"\nИтого замечаний: {total_issues}")
    return 1 if total_issues else 0


if __name__ == "__main__":
    sys.exit(main())
