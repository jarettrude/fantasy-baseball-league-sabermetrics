"""Player valuation engine for fantasy baseball.

Calculates fantasy player values based on advanced metrics, category
performance, and league scoring settings with injury adjustments.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel

logger = logging.getLogger(__name__)

NEGATIVE_CATEGORIES = {"ERA", "WHIP"}

INJURY_WEIGHTS = {
    None: Decimal("1.0"),
    "DTD": Decimal("0.6"),
    "IL10": Decimal("0.2"),
    "IL60": Decimal("0.0"),
    "OUT": Decimal("0.0"),
    "UNKNOWN": Decimal("0.8"),
}


class StatCategory(BaseModel):
    """
    Representation of a statistical category used for valuation.
    """

    stat_id: int
    display_name: str
    position_type: str


class PlayerStatSummary(BaseModel):
    """
    Summary of a player's statistics and situational modifiers.
    """

    player_id: int
    stats: dict[str, float]
    injury_status: str | None = None
    games_played: int = 0
    yahoo_rank: int | None = None
    data_sources: list[str] | None = None
    schedule_multiplier: float = 1.0
    missed_games_next_7_days: int = 0
    matchup_multiplier: float = 1.0


class PlayerValueResult(BaseModel):
    """
    Result of the valuation calculation for a player.
    """

    player_id: int
    category_scores: dict[str, float]
    composite_value: Decimal
    injury_weight: Decimal
    data_sources_count: int = 0


class ComputeZScoresRequest(BaseModel):
    """
    Request payload for computing z-scores.
    """

    players: list[PlayerStatSummary]
    categories: list[StatCategory]
    snapshot_type: Literal["season", "next_games"]


class ComputeZScoresResponse(BaseModel):
    snapshots: list[PlayerValueResult]


def compute_z_scores(request: ComputeZScoresRequest) -> ComputeZScoresResponse:
    """Compute z-scores with progressive valuation support.

    Players with full stat data get standard z-score valuations.
    Players with no stat data but a Yahoo rank get a rank-based
    fallback score so they aren't all lumped at zero. Players
    with no data at all get a neutral zero score.
    """
    if not request.players or not request.categories:
        return ComputeZScoresResponse(snapshots=[])

    cat_names = [c.display_name for c in request.categories]

    rows = []
    for p in request.players:
        row = {
            "player_id": p.player_id,
            "injury_status": p.injury_status,
            "yahoo_rank": p.yahoo_rank,
            "schedule_multiplier": p.schedule_multiplier,
            "missed_games": p.missed_games_next_7_days,
            "matchup_multiplier": p.matchup_multiplier,
            "data_sources_count": len(p.data_sources)
            if p.data_sources
            else (1 if any(p.stats.get(cat, 0.0) != 0.0 for cat in cat_names) else 0),
        }
        for cat in cat_names:
            row[cat] = p.stats.get(cat, 0.0)
        rows.append(row)

    df = pd.DataFrame(rows)

    for cat in cat_names:
        col = pd.to_numeric(df[cat], errors="coerce").fillna(0.0)
        mean = col.mean()
        std = col.std()
        if std == 0 or np.isnan(std):
            df[f"{cat}_z"] = 0.0
        else:
            z = (col - mean) / std
            if cat in NEGATIVE_CATEGORIES:
                z = -z
            df[f"{cat}_z"] = z

    z_cols = [f"{cat}_z" for cat in cat_names]
    df["composite"] = df[z_cols].mean(axis=1)

    results = []
    for _, row in df.iterrows():
        cat_scores = {}
        for cat in cat_names:
            val = row[f"{cat}_z"]
            cat_scores[cat] = round(float(val), 4) if not np.isnan(val) else 0.0

        composite = round(float(row["composite"]), 4) if not np.isnan(row["composite"]) else 0.0

        # Progressive fallback: for players with zero data sources their
        # z-scores from zero-stat values are meaningless noise — override
        # them entirely. Players with a Yahoo rank get a small positive
        # score; players with no rank get a neutral 0.
        data_src_count = int(row["data_sources_count"])
        if data_src_count == 0:
            if row["yahoo_rank"] is not None and not np.isnan(row["yahoo_rank"]):
                yahoo_rank = int(row["yahoo_rank"])
                # Lower rank = better. Scale: rank 1 → +0.5, rank 500+ → 0
                composite = round(max(0.0, 0.5 * (1.0 - (yahoo_rank - 1) / 500.0)), 4)
            else:
                composite = 0.0

        injury_status = row["injury_status"]
        injury_weight = INJURY_WEIGHTS.get(injury_status, Decimal("0.8"))

        if request.snapshot_type == "next_games":
            base = Decimal("1.0")
            schedule_weight = Decimal(str(row["schedule_multiplier"]))
            matchup_weight = Decimal(str(row["matchup_multiplier"]))

            # Granular injury adjustment: if we know exactly how many games they miss,
            # use (Games Active / 7 Total).
            # Otherwise fall back to the generic injury_status weight.
            granular_missed = int(row["missed_games"])
            if granular_missed > 0:
                # Max 7 games in a week usually, but we scale by 7.0 for consistency.
                actual_injury_weight = Decimal(max(0, 7 - granular_missed)) / Decimal("7.0")
            else:
                actual_injury_weight = injury_weight

            # Apply modifiers. Matchup weight comes from Vegas odds (Phase 2).
            adjusted_composite = (
                (base + Decimal(str(composite))) * actual_injury_weight * schedule_weight * matchup_weight
            )
            injury_weight = actual_injury_weight  # Return the computed weight for snapshot storage
        else:
            adjusted_composite = Decimal(str(composite))

        results.append(
            PlayerValueResult(
                player_id=int(row["player_id"]),
                category_scores=cat_scores,
                composite_value=adjusted_composite,
                injury_weight=injury_weight,
                data_sources_count=data_src_count,
            )
        )

    return ComputeZScoresResponse(snapshots=results)
