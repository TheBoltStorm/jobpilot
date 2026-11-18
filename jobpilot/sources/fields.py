"""Map free-text target fields ("data science", "SWE") onto JobPilot's field tags."""

from __future__ import annotations

import re

FIELD_TAGS: dict[str, list[str]] = {
    "software": ["software", "swe", "sde", "developer", "programming", "backend", "back end", "frontend",
                 "front end", "full stack", "fullstack", "web", "mobile", "ios", "android", "devops", "sre",
                 "infrastructure", "cloud", "platform", "computer science", "engineer", "engineering",
                 "technical staff", "programmer"],
    "data": ["data", "machine learning", "ml", "ai", "artificial intelligence", "analytics", "analyst",
             "statistics", "research scientist", "deep learning", "nlp", "computer vision"],
    "product": ["product", "pm", "program manager", "product management", "apm"],
    "hardware": ["hardware", "electrical", "embedded", "firmware", "mechanical", "robotics", "aerospace",
                 "fpga", "asic", "chip", "semiconductor"],
    "quant": ["quant", "quantitative", "trading", "trader", "finance", "fintech"],
    "security": ["security", "cybersecurity", "infosec", "appsec"],
    "design": ["design", "ux", "ui", "user experience", "product design", "graphic"],
}

# How the GitHub lists' "category" values map onto tags.
LIST_CATEGORY_TAGS = {
    "software": "software", "software engineering": "software",
    "ai/ml/data": "data", "data science, ai & machine learning": "data",
    "product": "product", "product management": "product",
    "hardware": "hardware", "hardware engineering": "hardware",
    "quant": "quant", "quantitative finance": "quant",
}


def tags_for(texts: list[str]) -> set[str]:
    tags: set[str] = set()
    for text in texts:
        low = text.lower()
        for tag, words in FIELD_TAGS.items():
            if any(re.search(rf"\b{re.escape(w)}\b", low) for w in words):
                tags.add(tag)
    return tags


def job_tags(title: str, source_field: str) -> set[str]:
    cat = LIST_CATEGORY_TAGS.get(source_field.strip().lower())
    tags = {cat} if cat else set()
    return tags | tags_for([title, source_field])
