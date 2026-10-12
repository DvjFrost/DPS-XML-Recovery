"""Small built-in translation catalog; no runtime dependency."""

RU = {
    "title": "DPS XML Recovery",
    "iit": "Состояние комплекса ІІТ",
    "tax_id": "ЄДРПОУ / РНОКПП",
    "jks": "Файл ключа JKS",
    "alias": "Alias ключа",
    "period": "Период восстановления",
    "root": "Каталог сохранения XML",
    "mode": "Режим восстановления",
    "advanced": "Дополнительные настройки",
    "start": "Начать восстановление",
    "stop": "Остановить",
}

UK = {
    "title": "DPS XML Recovery",
    "iit": "Стан комплексу ІІТ",
    "tax_id": "ЄДРПОУ / РНОКПП",
    "jks": "Файл ключа JKS",
    "alias": "Alias ключа",
    "period": "Період відновлення",
    "root": "Каталог збереження XML",
    "mode": "Режим відновлення",
    "advanced": "Додаткові налаштування",
    "start": "Почати відновлення",
    "stop": "Зупинити",
}


def strings(language: str = "ru") -> dict[str, str]:
    return dict(UK if language.casefold().startswith("uk") else RU)
