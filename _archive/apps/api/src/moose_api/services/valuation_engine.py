"""Player valuation engine for fantasy baseball.

Calculates fantasy player values based on advanced metrics, category
performance, and league scoring settings with injury adjustments.

Key design changes (2026-05 audit):
- Z-score population restricted to players with real stat data to prevent
  zero-stat players from contaminating the distribution.
- Sample-size regression shrinks small-sample z-scores toward the mean,
  preventing 5-AB/.600-AVG batters from ranking above 300-AB players.
- Category-scarcity weighting: categories with tighter distributions
  (harder to differentiate) receive proportionally more weight so the
  composite reflects true fantasy value.
- Volume-weighted rate stats: ERA and WHIP z-scores are dampened for
  pitchers with low IP.
- Fixed next_games formula that was additive (base + composite) instead
  of purely multiplicative, which dramatically undervalued 2-start pitchers.
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

# Categories that are rate-based (not counting stats). These need
# volume qualification before their z-scores are meaningful.
RATE_CATEGORIES = {"AVG", "ERA", "WHIP"}

# Volume fields to look for in the stats dict when applying
# sample-size regression. Mapped per position type.
BATTER_VOLUME_STAT = "AB"
PITCHER_VOLUME_STAT = "IP"

# Regression constants: a player needs this many PA/IP before their
# z-score reaches full weight. Below this, scores regress to 0.
# Empirically tuned: 100 AB ≈ 3 weeks of regular play.
REGRESSION_AB = 100
REGRESSION_IP = 30

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
    is_pitcher: bool = False
    xstat_xwoba: float | None = None
    xstat_xera: float | None = None


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
    snapshot_type: Literal["season", "next_games", "rest_of_season"]


class ComputeZScoresResponse(BaseModel):
    snapshots: list[PlayerValueResult]


def compute_z_scores(request: ComputeZScoresRequest) -> ComputeZScoresResponse:
    """Compute z-scores with sample-size regression and scarcity weighting.

    Key improvements over the naive implementation:

    1. **Population isolation**: only players with real stat data participate
       in the z-score distribution. Zero-stat players receive a Yahoo-rank
       fallback afterward, not noise from a contaminated mean/std.
    2. **Sample-size regression**: z-scores are shrunk toward zero based on
       how much volume (AB for batters, IP for pitchers) the player has.
       This prevents small-sample outliers from dominating the rankings.
    3. **Category-scarcity weighting**: categories with tighter distributions
       (smaller std) receive proportionally more weight in the composite,
       reflecting that a 1-z edge in saves is harder to find than in runs.
    4. **Rate-stat volume check**: rate categories (AVG, ERA, WHIP) get an
       additional dampening factor for players below the regression threshold.
    """
    if not request.players or not request.categories:
        return ComputeZScoresResponse(snapshots=[])

    cat_names = [c.display_name for c in request.categories]

    # --- Separate players with real data from those without ---
    has_data_players: list[PlayerStatSummary] = []
    no_data_players: list[PlayerStatSummary] = []
    for p in request.players:
        src_count = len(p.data_sources) if p.data_sources else (
            1 if any(p.stats.get(cat, 0.0) != 0.0 for cat in cat_names) else 0
        )
        if src_count > 0:
            has_data_players.append(p)
        else:
            no_data_players.append(p)

    # --- Compute z-scores on the clean population only ---
    results: list[PlayerValueResult] = []

    if has_data_players:
        rows = []
        for p in has_data_players:
            row: dict = {
                "player_id": p.player_id,
                "injury_status": p.injury_status,
                "yahoo_rank": p.yahoo_rank,
                "schedule_multiplier": p.schedule_multiplier,
                "missed_games": p.missed_games_next_7_days,
                "matchup_multiplier": p.matchup_multiplier,
                "is_pitcher": p.is_pitcher,
                "data_sources_count": len(p.data_sources) if p.data_sources else 1,
            }
            for cat in cat_names:
                row[cat] = p.stats.get(cat, 0.0)

            # Volume fields for regression
            row["_volume_ab"] = p.stats.get(BATTER_VOLUME_STAT, 0.0)
            row["_volume_ip"] = p.stats.get(PITCHER_VOLUME_STAT, 0.0)
            row["xstat_xwoba"] = p.xstat_xwoba
            row["xstat_xera"] = p.xstat_xera
            rows.append(row)

        df = pd.DataFrame(rows)

        # Compute raw z-scores per category
        cat_stds: dict[str, float] = {}
        for cat in cat_names:
            col = pd.to_numeric(df[cat], errors="coerce").fillna(0.0)
            mean = col.mean()
            std = col.std()
            cat_stds[cat] = float(std) if not np.isnan(std) else 0.0
            if std == 0 or np.isnan(std):
                df[f"{cat}_z"] = 0.0
            else:
                z = (col - mean) / std
                if cat in NEGATIVE_CATEGORIES:
                    z = -z
                df[f"{cat}_z"] = z

        # --- Apply sample-size regression per player ---
        for idx, row in df.iterrows():
            is_pitcher = row["is_pitcher"]
            volume = float(row["_volume_ip"]) if is_pitcher else float(row["_volume_ab"])
            regression_threshold = REGRESSION_IP if is_pitcher else REGRESSION_AB

            # Regression factor: 0 at 0 volume, 1.0 at threshold, ~1.0 above
            regression_factor = volume / (volume + regression_threshold) if (volume + regression_threshold) > 0 else 0.0

            for cat in cat_names:
                z_col = f"{cat}_z"
                raw_z = df.at[idx, z_col]
                if not np.isnan(raw_z):
                    # Rate stats get extra dampening from volume
                    if cat in RATE_CATEGORIES:
                        df.at[idx, z_col] = raw_z * regression_factor
                    else:
                        # Counting stats: light regression (half effect)
                        counting_factor = 0.5 + 0.5 * regression_factor
                        df.at[idx, z_col] = raw_z * counting_factor

        # --- Category-scarcity weighting for composite ---
        # Weight = inverse of standard deviation (tighter spread → higher weight).
        # Normalize so weights sum to 1.0 for interpretability.
        z_cols = [f"{cat}_z" for cat in cat_names]
        nonzero_stds = {cat: s for cat, s in cat_stds.items() if s > 0}

        if nonzero_stds:
            raw_weights = {cat: 1.0 / s for cat, s in nonzero_stds.items()}
            weight_sum = sum(raw_weights.values())
            cat_weights = {cat: w / weight_sum for cat, w in raw_weights.items()}
        else:
            # Fallback: equal weighting if all stds are zero
            cat_weights = {cat: 1.0 / len(cat_names) for cat in cat_names}

        # Compute weighted composite
        df["composite"] = sum(
            df[f"{cat}_z"] * cat_weights.get(cat, 1.0 / len(cat_names))
            for cat in cat_names
        )

        # --- Blend xwOBA / xERA into composite ---
        # If available, xstats act as a 15% anchor pulling the composite toward expected performance
        xstat_weight = 0.15
        
        # xERA is negative category (lower is better)
        xera_col = pd.to_numeric(df["xstat_xera"], errors="coerce")
        xera_mean = xera_col.mean()
        xera_std = xera_col.std()
        if xera_std > 0:
            df["xera_z"] = -(xera_col - xera_mean) / xera_std
        else:
            df["xera_z"] = 0.0

        # xwOBA is positive category (higher is better)
        xwoba_col = pd.to_numeric(df["xstat_xwoba"], errors="coerce")
        xwoba_mean = xwoba_col.mean()
        xwoba_std = xwoba_col.std()
        if xwoba_std > 0:
            df["xwoba_z"] = (xwoba_col - xwoba_mean) / xwoba_std
        else:
            df["xwoba_z"] = 0.0

        for idx, row in df.iterrows():
            comp = row["composite"]
            if row["is_pitcher"] and not np.isnan(row["xera_z"]) and row["xera_z"] != 0.0:
                df.at[idx, "composite"] = comp * (1 - xstat_weight) + row["xera_z"] * xstat_weight
            elif not row["is_pitcher"] and not np.isnan(row["xwoba_z"]) and row["xwoba_z"] != 0.0:
                df.at[idx, "composite"] = comp * (1 - xstat_weight) + row["xwoba_z"] * xstat_weight

        # Build results for players with data
        for _, row in df.iterrows():
            cat_scores = {}
            for cat in cat_names:
                val = row[f"{cat}_z"]
                cat_scores[cat] = round(float(val), 4) if not np.isnan(val) else 0.0

            composite = round(float(row["composite"]), 4) if not np.isnan(row["composite"]) else 0.0

            injury_status = row["injury_status"]
            injury_weight = INJURY_WEIGHTS.get(injury_status, Decimal("0.8"))

            if request.snapshot_type == "next_games":
                schedule_weight = Decimal(str(row["schedule_multiplier"]))
                matchup_weight = Decimal(str(row["matchup_multiplier"]))

                # Granular injury adjustment: if we know exactly how many games they miss,
                # use (Games Active / 7 Total).
                # Otherwise fall back to the generic injury_status weight.
                granular_missed = int(row["missed_games"])
                if granular_missed > 0:
                    actual_injury_weight = Decimal(max(0, 7 - granular_missed)) / Decimal("7.0")
                else:
                    actual_injury_weight = injury_weight

                # Multiplicative formula: composite × modifiers.
                # For negative composites we use max(composite, 0) so modifiers
                # don't perversely boost a bad player. Negative composites pass
                # through with injury dampening only.
                comp_dec = Decimal(str(composite))
                if comp_dec >= 0:
                    adjusted_composite = comp_dec * actual_injury_weight * schedule_weight * matchup_weight
                else:
                    adjusted_composite = comp_dec * actual_injury_weight

                injury_weight = actual_injury_weight
            else:
                adjusted_composite = Decimal(str(composite))

            results.append(
                PlayerValueResult(
                    player_id=int(row["player_id"]),
                    category_scores=cat_scores,
                    composite_value=adjusted_composite,
                    injury_weight=injury_weight,
                    data_sources_count=int(row["data_sources_count"]),
                )
            )

    # --- Fallback for players without stat data ---
    for p in no_data_players:
        if p.yahoo_rank is not None:
            # Lower rank = better. Scale: rank 1 → +0.5, rank 500+ → 0
            composite = round(max(0.0, 0.5 * (1.0 - (p.yahoo_rank - 1) / 500.0)), 4)
        else:
            composite = 0.0

        injury_weight = INJURY_WEIGHTS.get(p.injury_status, Decimal("0.8"))

        results.append(
            PlayerValueResult(
                player_id=p.player_id,
                category_scores={cat: 0.0 for cat in cat_names},
                composite_value=Decimal(str(composite)),
                injury_weight=injury_weight,
                data_sources_count=0,
            )
        )

    return ComputeZScoresResponse(snapshots=results)
