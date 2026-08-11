# Discovery — карта поисковых паттернов

Шпаргалка для фазы 2 (deep dive). Используй grep/glob-паттерны ниже, чтобы находить факты в коде. Применяй только те строки таблиц, которые соответствуют детектированному стеку. Результаты всегда перепроверяй чтением найденных файлов — grep даёт кандидатов, а не истину.

Все примеры паттернов даны для `rg` (ripgrep); при его отсутствии используй `grep -rnE`.

## 1. Точки входа и жизненный цикл

| Стек | Что искать | Паттерн |
|---|---|---|
| Python | `main`, `if __name__`, lifespan | `if __name__ == .__main__.`, `lifespan`, `@app\.on_event` |
| Java/Kotlin | `main`, Spring Boot | `public static void main`, `@SpringBootApplication` |
| Go | пакет main | `func main\(\)` в `cmd/**` и корне |
| Node | entry из package.json | поля `main`, `scripts.start` в `package.json` |
| .NET | Program/Startup | `Program.cs`, `WebApplication.CreateBuilder` |

Дополнительно: `Dockerfile` (`ENTRYPOINT`, `CMD`), `docker-compose*.yml` (`command`, `ports`, `depends_on`), `Makefile` (цели `run`, `up`).

## 2. HTTP API

| Стек | Паттерны |
|---|---|
| FastAPI | `@(app\|router)\.(get\|post\|put\|delete\|patch)`, `APIRouter\(` |
| Flask | `@app\.route\(` , `Blueprint\(` |
| Django | `urls.py`: `path\(`, `re_path\(` |
| Spring | `@(Rest)Controller`, `@(Get\|Post\|Put\|Delete\|Patch)Mapping` |
| Go | `http\.Handle(Func)?\(`, `\.GET\(\|\.POST\(` (gin/echo), `chi.NewRouter` |
| Express/Nest | `app\.(get\|post\|put\|delete)\(`, `@(Get\|Post\|Put\|Delete)\(` |

Собери каждый endpoint в таблицу: метод, путь, обработчик (`файл:строка`), назначение (из кода/docstring, не додумывать). Не забудь health-checks и метрики (`/health`, `/metrics`, `prometheus`).

## 3. Очереди сообщений

| Система | Признаки | Паттерны |
|---|---|---|
| Kafka | `kafka`, `aiokafka`, `confluent` | `AIOKafka(Producer\|Consumer)`, `@KafkaListener`, `sarama\.`, `produce\(`, `consume\(` |
| RabbitMQ | `pika`, `aio_pika`, `amqp` | `@RabbitListener`, `basic_publish`, `basic_consume`, `aio_pika\.connect` |
| Redis | `redis`, pub/sub, streams | `publish\(`, `subscribe\(`, `xadd\(`, `xread` |
| Celery / RQ | `celery`, `dramatiq` | `@app\.task`, `@shared_task`, `\.delay\(` |

Для каждого топика/очереди зафиксируй: имя (или env-переменную, из которой оно берётся), направление (produce/consume), формат сообщения (модель/схема), обработчик (`файл:строка`).

## 4. База данных

| Признак | Паттерны |
|---|---|
| SQLAlchemy | `__tablename__`, `declarative_base`, `Mapped\[`, `relationship\(` |
| Django ORM | `models\.Model`, поля `models\.(Char\|Integer\|Foreign)Field` |
| Hibernate/JPA | `@Entity`, `@Table`, `@(OneToMany\|ManyToOne)` |
| Go | `sqlx`, `gorm`, `database/sql` |
| Prisma | `schema.prisma` (glob) |
| Миграции | каталоги `alembic/`, `migrations/`, `flyway/`, `liquibase/`; файлы `V\d+__.*\.sql` |
| Enum-статусы | `(Enum\|StrEnum)\)`, `enum class`, `type .* string` рядом с константами статусов |

Схему БД собирай из ORM-моделей и сверяй с миграциями. Для каждой таблицы: назначение (по имени полей и использованию в репозиториях), PK, значимые FK, уникальные индексы.

## 5. Внешние интеграции (исходящие)

| Тип | Паттерны |
|---|---|
| HTTP-клиенты Python | `httpx\.(Async)?Client`, `requests\.(get\|post)`, `aiohttp\.ClientSession` |
| HTTP-клиенты Java/Kotlin | `RestTemplate`, `WebClient`, `@FeignClient`, `OkHttp` |
| HTTP-клиенты JS/TS | `axios\.`, `fetch\(`, `got\(` |
| gRPC | glob `**/*.proto`, `grpc\.(Server\|Channel)`, `@GrpcService` |
| Объектные хранилища | `boto3.*s3`, `minio`, `BlobServiceClient` |
| Почта | `smtplib`, `JavaMailSender`, `nodemailer` |

По каждой интеграции: система (по имени клиента/конфига), направление, что передаётся, где клиент (`файл:строка`), ретраи/SSL/таймауты (искать `retry`, `verify=`, `timeout`, `SSLContext`).

## 6. Фоновые задачи

| Стек | Паттерны |
|---|---|
| Python | `asyncio\.create_task`, `while True:`, `APScheduler`, `@scheduled_task`, `crontab\(` |
| Java/Kotlin | `@Scheduled`, `@Async`, `ScheduledExecutorService` |
| Go | `go func\(`, `time\.NewTicker` |
| Node | `setInterval`, `node-cron`, `bull` |

Для каждой фоновой задачи: что делает, периодичность/таймауты, защита от дублей (флаги, блокировки в БД).

## 7. Конфигурация

Источники: `BaseSettings` (pydantic), `application.yml/properties`, `config.yaml`, `.env.example`, `os.environ`, `@Value`, `viper`. Собери таблицу env-переменных: имя, назначение, значение по умолчанию. Значения хостов/паролей в документы не переносить.

## 8. Тесты и команды

| Что | Где |
|---|---|
| Тестовый фреймворк | `pytest`, `unittest`, `JUnit`, `go test`, `jest`, `vitest` — по манифестам и каталогам `tests/`, `test/`, `*_test.go` |
| Команды | `Makefile` (цели), `pyproject.toml` (`[tool.*]`, scripts), `package.json` (`scripts`), `justfile`, `Taskfile.yml` |

Команды для AGENTS.md перепроверять по фактическому наличию цели/скрипта.

## 9. Таблица «признак → тип интеграции» (быстрый детект по зависимостям)

| Зависимость в манифесте | Вероятная интеграция |
|---|---|
| `aiokafka`, `kafka-python`, `spring-kafka`, `sarama` | Kafka |
| `aio-pika`, `pika`, `spring-rabbit` | RabbitMQ |
| `psycopg`, `asyncpg`, `postgresql` драйверы | PostgreSQL |
| `pymongo`, `mongo-driver` | MongoDB |
| `redis`, `jedis`, `go-redis` | Redis |
| `boto3`, `minio` | S3-совместимое хранилище |
| `grpcio`, `protobuf` | gRPC |
| `elasticsearch`, `opensearch-py` | Поисковый движок |
| `openTelemetry`, `opentelemetry-*` | Трейсинг |

## 10. Примеры запросов и ответов (для TESTING.md, раздел 3)

Где искать готовые примеры тел запросов/ответов, в порядке приоритета:

| Источник | Что искать | Паттерны |
|---|---|---|
| OpenAPI/спеки | готовые examples | glob `**/*openapi*.{yml,yaml,json}`, `**/swagger*.{json,yaml}`; внутри — `example`, `examples` |
| Тесты | реальные payloads в вызовах | `tests/`: `client.post(`, `httpx.post(`, `mock`, `fixture`; Java: `MockMvc`, `@WebMvcTest` |
| Фикстуры | файлы с данными | glob `**/fixtures/**`, `**/testdata/**`, `**/*.http`, `**/*example*.json` |
| Postman/Bruno | коллекции запросов | glob `**/*.postman_collection.json`, `**/*.bru` |
| Модели | дефолтные значения полей | pydantic: `Field(`, `default=`; TS: `interface` + zod-схемы; Java: DTO-классы |

Правила:

- Пример из теста/фикстуры/спеки переносить почти как есть (маскируя хосты и секреты).
- Если готовых примеров нет — собрать минимальный валидный пример строго по обязательным полям модели и пометить: «пример составлен по модели, в тестах не встречается».
- Для ошибочных ответов искать обработчики: `HTTPException(`, `raise .*Error`, `@ExceptionHandler`, `exception_handler`, middleware ошибок — оттуда брать коды и тела.

## 11. Приёмы

- **Дерево проекта с аннотациями:** строй по выводу `scan_project.py`; аннотируй только значимые узлы (модули с бизнес-логикой, клиенты, модели), тривиальные (`__init__.py`, lock-файлы) — без аннотаций или одной общей строкой.
- **Интеграции через конфиг:** если клиент не находится по паттернам, ищи в конфиге переменные с `URL`, `HOST`, `ENDPOINT`, `BROKER` — они укажут на внешние системы, затем ищи использование этих переменных.
- **Сквозные идентификаторы:** ищи `trace_id`, `request_id`, `rq_uid`, `correlation` — они нужны и для ARCHITECTURE.md (пайплайны), и для TESTING.md (фильтрация логов).
- **Делегирование субагентам:** на больших проектах вызывай субагентов GigaCode параллельно, по одному на домен. Каждому давай узкую задачу и требуй структурированный ответ (таблица фактов с `файл:строка`). Результаты своди сам, конфликты перепроверяй чтением кода.
