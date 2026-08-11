#!/usr/bin/env python3
"""Сборщик первичных фактов о проекте для скилла project-docs (GigaCode CLI).

Скрипт обходит дерево проекта и выводит в stdout JSON со сводкой:
структуру каталогов, детектированный стек, манифесты и зависимости,
точки входа, миграции БД, существующую документацию и статистику
log-вызовов по языкам. Использует только стандартную библиотеку
Python 3 (3.8+), поэтому работает в закрытом контуре без установки
внешних пакетов.

Пример запуска:
    python3 scan_project.py /path/to/project --max-depth 4
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover - зависит от версии интерпретатора
    tomllib = None

# Каталоги, которые никогда не несут смысла для архитектурного анализа.
EXCLUDE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "env", ".env", "dist", "build", "target", "out", "bin", "obj",
    ".idea", ".vscode", ".next", ".nuxt", "coverage", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".gradle", ".turbo", ".cache", "vendor",
}

# Файлы-манифесты: имя файла -> экосистема.
MANIFEST_NAMES = {
    "pyproject.toml": "python",
    "requirements.txt": "python",
    "setup.py": "python",
    "setup.cfg": "python",
    "Pipfile": "python",
    "pom.xml": "java",
    "build.gradle": "java",
    "build.gradle.kts": "kotlin",
    "go.mod": "go",
    "package.json": "node",
    "Cargo.toml": "rust",
    "composer.json": "php",
    "Gemfile": "ruby",
    "mix.exs": "elixir",
    "*.csproj": "dotnet",
}

# Типовые имена точек входа (поиск по имени файла в любом каталоге).
ENTRY_POINT_NAMES = {
    "main.py", "app.py", "api_server.py", "manage.py", "wsgi.py", "asgi.py",
    "main.go", "index.js", "server.js", "app.js", "main.ts", "index.ts",
    "server.ts", "Program.cs", "main.rs", "Application.java", "Main.java",
}

# Каталоги, где обычно живут миграции БД.
MIGRATION_DIR_NAMES = {"alembic", "migrations", "migration", "flyway", "liquibase", "migrate"}

# Существующая документация, которую агент обязан прочитать до анализа.
DOC_NAMES = {
    "README", "README.md", "README.txt", "ARCHITECTURE.md", "TESTING.md",
    "AGENTS.md", "CLAUDE.md", "CHANGELOG.md", "CONTRIBUTING.md",
}

# Расширения файлов -> язык (для подсчёта объёма кода и статистики логов).
LANG_BY_EXT = {
    ".py": "python",
    ".java": "java", ".kt": "kotlin",
    ".go": "go",
    ".js": "javascript", ".jsx": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".mjs": "javascript",
    ".cs": "csharp", ".rs": "rust", ".php": "php", ".rb": "ruby",
}

# Regex-паттерны log-вызовов по языкам: (уровень -> pattern).
LOG_PATTERNS = {
    "python": {
        "debug": re.compile(r"\b(?:log|logger)\.debug\("),
        "info": re.compile(r"\b(?:log|logger)\.info\("),
        "warning": re.compile(r"\b(?:log|logger)\.(?:warning|warn)\("),
        "error": re.compile(r"\b(?:log|logger)\.(?:error|exception|critical)\("),
    },
    "java": {
        "debug": re.compile(r"\blog(?:ger)?\.debug\("),
        "info": re.compile(r"\blog(?:ger)?\.info\("),
        "warning": re.compile(r"\blog(?:ger)?\.warn\("),
        "error": re.compile(r"\blog(?:ger)?\.error\("),
    },
    "kotlin": {
        "debug": re.compile(r"\blog(?:ger)?\.debug\("),
        "info": re.compile(r"\blog(?:ger)?\.info\("),
        "warning": re.compile(r"\blog(?:ger)?\.warn\("),
        "error": re.compile(r"\blog(?:ger)?\.error\("),
    },
    "go": {
        "debug": re.compile(r"\bslog\.Debug(?:Context)?\("),
        "info": re.compile(r"\b(?:slog\.Info(?:Context)?|log\.Print(?:f|ln)?)\("),
        "warning": re.compile(r"\bslog\.Warn(?:Context)?\("),
        "error": re.compile(r"\b(?:slog\.Error(?:Context)?|log\.Fatal(?:f|ln)?)\("),
    },
    "javascript": {
        "debug": re.compile(r"\b(?:console|logger)\.debug\("),
        "info": re.compile(r"\b(?:console\.(?:log|info)|logger\.info)\("),
        "warning": re.compile(r"\b(?:console|logger)\.warn\("),
        "error": re.compile(r"\b(?:console|logger)\.error\("),
    },
    "typescript": {
        "debug": re.compile(r"\b(?:console|logger)\.debug\("),
        "info": re.compile(r"\b(?:console\.(?:log|info)|logger\.info)\("),
        "warning": re.compile(r"\b(?:console|logger)\.warn\("),
        "error": re.compile(r"\b(?:console|logger)\.error\("),
    },
    "csharp": {
        "debug": re.compile(r"\bLog(?:ger)?\.LogDebug\("),
        "info": re.compile(r"\bLog(?:ger)?\.LogInformation\("),
        "warning": re.compile(r"\bLog(?:ger)?\.LogWarning\("),
        "error": re.compile(r"\bLog(?:ger)?\.LogError\("),
    },
}

# Максимальный размер файла, который скрипт читает для подсчёта логов.
MAX_FILE_BYTES = 1_000_000


def iter_project_files(root: Path):
    """Рекурсивно выдаёт все файлы проекта, пропуская служебные каталоги.

    Args:
        root: Абсолютный путь к корню проекта.

    Yields:
        Path очередного файла внутри проекта.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
        for name in sorted(filenames):
            yield Path(dirpath) / name


def build_tree(root: Path, max_depth: int, max_entries: int) -> list:
    """Строит компактное текстовое дерево проекта с ограничениями.

    Args:
        root: Корень проекта.
        max_depth: Максимальная глубина обхода относительно корня.
        max_entries: Максимальное число строк в дереве.

    Returns:
        Список строк вида ``"  ├── name/"``; при обрезке добавляет
        строку-маркер ``"… (дерево обрезано)"``.
    """
    lines = []
    truncated = False

    def walk(directory: Path, prefix: str, depth: int) -> None:
        """Рекурсивно дописывает в ``lines`` содержимое одного каталога.

        Args:
            directory: Текущий каталог.
            prefix: Отступ для вложенности.
            depth: Текущая глубина (0 — корень).
        """
        nonlocal truncated
        if depth > max_depth or len(lines) >= max_entries:
            truncated = True
            return
        try:
            entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name))
        except OSError:
            return
        entries = [e for e in entries if not (e.is_dir() and e.name in EXCLUDE_DIRS)]
        for entry in entries:
            if len(lines) >= max_entries:
                truncated = True
                return
            suffix = "/" if entry.is_dir() else ""
            lines.append(f"{prefix}{entry.name}{suffix}")
            if entry.is_dir():
                walk(entry, prefix + "  ", depth + 1)

    walk(root, "", 1)
    if truncated:
        lines.append("… (дерево обрезано: см. --max-depth/--max-entries)")
    return lines


def find_manifests(root: Path) -> dict:
    """Находит манифесты зависимостей в проекте.

    Args:
        root: Корень проекта.

    Returns:
        Словарь ``{относительный_путь: экосистема}``.
    """
    found = {}
    for path in iter_project_files(root):
        rel = path.relative_to(root).as_posix()
        name = path.name
        if name in MANIFEST_NAMES:
            found[rel] = MANIFEST_NAMES[name]
        elif name.endswith(".csproj"):
            found[rel] = "dotnet"
    return found


def read_dependencies(root: Path, manifests: dict) -> dict:
    """Извлекает списки зависимостей из найденных манифестов.

    Парсинг намеренно упрощённый (regex/строки, без полноценных
    XML/TOML-парсеров там, где tomllib недоступен): цель скрипта —
    дать агенту быстрый обзор, а не точный lock-граф.

    Args:
        root: Корень проекта.
        manifests: Результат :func:`find_manifests`.

    Returns:
        Словарь ``{относительный_путь_манифеста: [зависимости]}``.
    """
    deps = {}
    for rel, eco in manifests.items():
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if rel.endswith("pyproject.toml"):
            deps[rel] = _parse_pyproject(path, text)
        elif rel.endswith("requirements.txt"):
            deps[rel] = [
                line.strip() for line in text.splitlines()
                if line.strip() and not line.startswith(("#", "-", "git+"))
            ]
        elif rel.endswith("package.json"):
            deps[rel] = _parse_package_json(text)
        elif rel.endswith("go.mod"):
            deps[rel] = re.findall(r"^\s+([^\s(]+)\s+v[\d]", text, re.MULTILINE)
        elif rel.endswith("pom.xml"):
            deps[rel] = sorted(set(re.findall(r"<artifactId>([^<]+)</artifactId>", text)))
        else:
            deps[rel] = []
    return deps


def _parse_pyproject(path: Path, text: str) -> list:
    """Извлекает зависимости из pyproject.toml.

    Использует ``tomllib`` на Python 3.11+; на более старых версиях
    откатывается на regex по секции ``[project]`` / ``[tool.poetry.dependencies]``.

    Args:
        path: Путь к файлу (для tomllib нужен бинарный режим).
        text: Уже прочитанное текстовое содержимое файла.

    Returns:
        Список строк-зависимостей.
    """
    if tomllib is not None:
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
            result = list(data.get("project", {}).get("dependencies", []) or [])
            poetry = data.get("tool", {}).get("poetry", {}).get("dependencies", {}) or {}
            result.extend(k for k in poetry if k.lower() != "python")
            optional = data.get("project", {}).get("optional-dependencies", {}) or {}
            for group in optional.values():
                result.extend(group)
            return result
        except Exception:
            pass  # Битый TOML — уходим на regex-запасной вариант.
    deps = re.findall(r'^\s*"([a-zA-Z0-9_.\-]+[^"]*)"', text, re.MULTILINE)
    deps += re.findall(r"^([a-zA-Z0-9_.\-]+)\s*=", text, re.MULTILINE)
    return sorted(set(d for d in deps if not d.startswith(("name", "version", "description"))))


def _parse_package_json(text: str) -> list:
    """Извлекает имена зависимостей из package.json.

    Args:
        text: Содержимое package.json.

    Returns:
        Список имён пакетов из dependencies и devDependencies.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    deps = set((data.get("dependencies") or {}).keys())
    deps |= set((data.get("devDependencies") or {}).keys())
    return sorted(deps)


def find_entry_points(root: Path) -> list:
    """Ищет типовые точки входа приложения по именам файлов.

    Args:
        root: Корень проекта.

    Returns:
        Отсортированный список относительных путей-кандидатов.
    """
    found = set()
    for path in iter_project_files(root):
        if path.name in ENTRY_POINT_NAMES:
            found.add(path.relative_to(root).as_posix())
        if path.name == "main.go" and "cmd" in path.parts:
            found.add(path.relative_to(root).as_posix())
    return sorted(found)


def find_migrations(root: Path) -> dict:
    """Находит каталоги миграций БД и считает файлы миграций в них.

    Args:
        root: Корень проекта.

    Returns:
        Словарь ``{каталог: {"count": N, "samples": [...]}}``.
    """
    result = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        current = Path(dirpath)
        if current.name.lower() in MIGRATION_DIR_NAMES:
            migration_files = [
                f for f in filenames
                if f.endswith((".py", ".sql")) and not f.startswith("__")
            ]
            if migration_files:
                rel = current.relative_to(root).as_posix()
                result[rel] = {
                    "count": len(migration_files),
                    "samples": sorted(migration_files)[:5],
                }
    return result


def find_docs(root: Path) -> list:
    """Находит существующие документационные файлы верхнего уровня.

    Args:
        root: Корень проекта.

    Returns:
        Отсортированный список относительных путей (README, AGENTS.md,
        ARCHITECTURE.md, каталоги docs/ и т.п.).
    """
    found = set()
    for path in root.iterdir():
        if path.name in DOC_NAMES or (path.is_dir() and path.name.lower() in {"docs", "doc", "documentation"}):
            found.add(path.relative_to(root).as_posix() + ("/" if path.is_dir() else ""))
    return sorted(found)


def collect_stats(root: Path) -> dict:
    """Считает файлы по языкам и статистику log-вызовов.

    Для каждого языка подсчитывает число log-вызовов по уровням
    (debug/info/warning/error) и собирает топ-10 файлов по плотности
    логирования — это подсказывает, где сосредоточены наблюдаемые
    шаги пайплайна.

    Args:
        root: Корень проекта.

    Returns:
        Словарь с ключами ``files_by_lang``, ``total_code_files``,
        ``log_calls`` и ``top_logged_files``.
    """
    files_by_lang: dict = {}
    log_calls: dict = {}
    per_file_counts: list = []
    total_code = 0

    for path in iter_project_files(root):
        lang = LANG_BY_EXT.get(path.suffix.lower())
        if lang is None:
            continue
        total_code += 1
        files_by_lang[lang] = files_by_lang.get(lang, 0) + 1

        patterns = LOG_PATTERNS.get(lang)
        if not patterns:
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        file_total = 0
        levels = log_calls.setdefault(lang, {"debug": 0, "info": 0, "warning": 0, "error": 0})
        for level, pattern in patterns.items():
            hits = len(pattern.findall(text))
            levels[level] += hits
            file_total += hits
        if file_total:
            per_file_counts.append((file_total, path.relative_to(root).as_posix()))

    per_file_counts.sort(reverse=True)
    return {
        "files_by_lang": dict(sorted(files_by_lang.items(), key=lambda kv: -kv[1])),
        "total_code_files": total_code,
        "log_calls": {k: v for k, v in log_calls.items() if any(v.values())},
        "top_logged_files": [
            {"file": f, "log_calls": c} for c, f in per_file_counts[:10]
        ],
    }


def detect_stack(manifests: dict, files_by_lang: dict) -> dict:
    """Формирует вывод о стеке проекта по манифестам и языкам файлов.

    Args:
        manifests: Результат :func:`find_manifests`.
        files_by_lang: Число файлов по языкам из :func:`collect_stats`.

    Returns:
        Словарь ``{"ecosystems": [...], "primary_lang": str|None}``.
    """
    ecosystems = sorted(set(manifests.values()))
    primary_lang = next(iter(files_by_lang), None)
    return {"ecosystems": ecosystems, "primary_lang": primary_lang}


def main() -> int:
    """Точка входа: разбирает аргументы, собирает факты, печатает JSON.

    Returns:
        Код завершения: 0 при успехе, 2 если корень проекта не найден.
    """
    parser = argparse.ArgumentParser(
        description="Собирает первичные факты о проекте и выводит JSON в stdout.",
    )
    parser.add_argument("project_root", nargs="?", default=".",
                        help="Корень анализируемого проекта (по умолчанию — текущий каталог).")
    parser.add_argument("--max-depth", type=int, default=4,
                        help="Максимальная глубина дерева (по умолчанию 4).")
    parser.add_argument("--max-entries", type=int, default=400,
                        help="Максимум строк дерева (по умолчанию 400).")
    parser.add_argument("--compact", action="store_true",
                        help="Печатать JSON без отступов (экономия токенов).")
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    if not root.is_dir():
        print(f"Ошибка: каталог не найден: {root}", file=sys.stderr)
        return 2

    manifests = find_manifests(root)
    stats = collect_stats(root)
    report = {
        "project_root": str(root),
        "tree": build_tree(root, args.max_depth, args.max_entries),
        "docs_to_read_first": find_docs(root),
        "manifests": manifests,
        "stack": detect_stack(manifests, stats["files_by_lang"]),
        "dependencies": read_dependencies(root, manifests),
        "entry_points": find_entry_points(root),
        "migrations": find_migrations(root),
        "code_stats": stats,
    }
    indent = None if args.compact else 2
    print(json.dumps(report, ensure_ascii=False, indent=indent))
    return 0


if __name__ == "__main__":
    sys.exit(main())
