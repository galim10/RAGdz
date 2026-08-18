import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

import jsonschema
from json_repair import repair_json

logger = logging.getLogger(__name__)

EntityType = Literal["person", "place", "organization", "object", "concept"]
PositionLabel = Literal["начало", "первая треть", "середина", "вторая половина", "конец"]

JSON_RETRY_ATTEMPTS = 2


@dataclass
class EntityItem:
    name: str
    type: EntityType


FIRST_LAYER_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "block_id": {"type": "integer"},
        "key_entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["person", "place", "organization", "object", "concept"],
                    },
                },
                "required": ["name", "type"],
                "additionalProperties": False,
            },
        },
        "timeline_markers": {"type": "array", "items": {"type": "string"}},
        "positional_label": {
            "type": "string",
            "enum": ["начало", "первая треть", "середина", "вторая половина", "конец"],
        },
    },
    "required": ["block_id", "key_entities", "timeline_markers", "positional_label"],
    "additionalProperties": False,
}

SUMMARY_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "position_label": {
            "type": "string",
            "enum": ["начало", "первая треть", "середина", "вторая половина", "конец"],
        },
        "section_title": {"type": ["string", "null"]},
        "summary": {"type": "string"},
        "connection_to_previous": {"type": ["string", "null"]},
        "key_entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["person", "place", "organization", "object", "concept"],
                    },
                },
                "required": ["name", "type"],
                "additionalProperties": False,
            },
        },
        "timeline_markers": {"type": "array", "items": {"type": "string"}},
        "summary_for_next": {"type": "string"},
    },
    "required": [
        "position_label",
        "section_title",
        "summary",
        "connection_to_previous",
        "key_entities",
        "timeline_markers",
        "summary_for_next",
    ],
    "additionalProperties": False,
}


def _strip_markdown_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    return text


def parse_json_response(content: str, schema: dict[str, Any]) -> Any:
    text = _strip_markdown_fence(content)

    parsed = None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning(f"Невалидный JSON, пробую repair_json: {text}")
        logger.warning(e)
        try:
            repaired = repair_json(text)
            parsed = json.loads(repaired)
            logger.warning(f"JSON синтаксически восстановлен. Было: {text!r} -> Стало: {repaired!r}")
        except Exception as e2:
            logger.error(f"ОШИБКА JSON формат (не удалось починить) {text}")
            logger.error(e2)
            return None

    try:
        jsonschema.validate(parsed, schema)
    except jsonschema.ValidationError as e:
        logger.error(f"JSON распарсился, но не соответствует схеме (вероятно, repair_json исказил структуру): {parsed}")
        logger.error(e)
        return None

    return parsed


def parse_entities(raw_entities: list[dict] | None) -> list[EntityItem]:
    result: list[EntityItem] = []
    for item in raw_entities or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        etype = item.get("type")
        if isinstance(name, str) and name and isinstance(etype, str) and etype:
            result.append(EntityItem(name=name, type=etype))
    return result


class Chunk:
    def __init__(
        self,
        text: str,
        positional_label: str,
        positional_index: str,
        section_title: str | None,
        connection_to_previous: str | None,
        key_entities: list[EntityItem],
        timeline_markers: list[str],
    ):
        self.text = text
        self.positional_label = positional_label
        self.positional_index = positional_index
        self.section_title = section_title
        self.connection_to_previous = connection_to_previous
        self.key_entities = key_entities
        self.timeline_markers = timeline_markers

    def format_to_embed(self) -> str:
        parts: list[str] = []

        if self.section_title:
            parts.append(f"Раздел: {self.section_title}")

        positional_bits = []
        if self.positional_label:
            positional_bits.append(self.positional_label)
        if self.positional_index:
            positional_bits.append(f"блок {self.positional_index}")
        if positional_bits:
            parts.append(f"Позиция: {', '.join(positional_bits)}")

        if self.connection_to_previous:
            parts.append(f"Связь с предыдущим: {self.connection_to_previous}")

        if self.key_entities:
            entities_str = ', '.join(
                f"{e.name} ({e.type})" for e in self.key_entities
            )
            parts.append(f"Ключевые сущности: {entities_str}")

        if self.timeline_markers:
            parts.append(f"Временные маркеры: {', '.join(self.timeline_markers)}")

        header = "\n".join(parts)
        body = self.text or ""

        if header:
            return f"{header}\n\n{body}".strip()
        return body.strip()
