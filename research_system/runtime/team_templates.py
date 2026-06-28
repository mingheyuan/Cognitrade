from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TeamTemplate:
    name: str
    agents: tuple[str, ...]


TEAM_TEMPLATES: dict[str, TeamTemplate] = {
    "stock_check": TeamTemplate(name="stock_check_team", agents=("collector", "retriever", "reporter")),
    "sector_research": TeamTemplate(name="sector_research_team", agents=("collector", "retriever", "reporter")),
    "distill_kol_logic": TeamTemplate(name="distill_kol_logic_team", agents=("collector", "distiller", "critic", "retriever", "reporter")),
    "review_thesis": TeamTemplate(name="review_thesis_team", agents=("collector", "reviewer", "retriever", "reporter")),
    "methodology_evolution": TeamTemplate(name="methodology_evolution_team", agents=("collector", "retriever", "reporter")),
}


def get_team_template(intent: str) -> TeamTemplate:
    return TEAM_TEMPLATES[intent]
