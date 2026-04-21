"""Position-aware roster recommendation engine.

Produces the single source of truth for "who should I drop, and which
free agent should replace them?" for a given fantasy team. Consumed by
both the ``/players/bench/recommendations`` API endpoint and the daily
manager-briefing prompt so the UI and the LLM speak the same language.

Core idea
---------
Fantasy decisions are primarily positional: a manager benefits from
swapping a weak starter at position P for a stronger free agent eligible
at position P, even when the weak starter is not the globally lowest
composite value on the roster. The previous implementation surfaced only
the three lowest-composite players and the three highest-composite free
agents without aligning them, which the LLM could not reconcile.

This module buckets both pools by Yahoo ``eligible_positions`` (honoring
multi-eligibility), determines the "starter-required" positions from
``League.roster_positions``, and for each required position reports:

1. the weakest roster player eligible there ("incumbent"), and
2. the top-K free agents eligible there with their composite delta.

Position-aware drop candidates are the union of the global worst
starters and any incumbent whose position has a meaningful upgrade on
the waiver wire, keyed by ``UPGRADE_THRESHOLD``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from moose_api.models.free_agent import FreeAgentSnapshot
from moose_api.models.league import League
from moose_api.models.player import Player
from moose_api.models.roster import RosterSlot
from moose_api.models.stats import PlayerValueSnapshot
from moose_api.models.team import Team

logger = logging.getLogger(__name__)

# Composite-value delta (same units as PlayerValueSnapshot.composite_value,
# which is aggregated z-score) above which we flag a free agent as a true
# upgrade over the incumbent. 0.30 empirically filters out noise swaps
# while still surfacing meaningful mid-season waiver moves.
UPGRADE_THRESHOLD = Decimal("0.30")

# Top-N free agents per position bucket retained for the LLM / UI so the
# payload stays compact without hiding secondary options.
TOP_FA_PER_POSITION = 3

# Global "lowest value starters" count retained to preserve the existing
# Roster Alert card behavior on the bench page.
GLOBAL_WORST_COUNT = 3

# Overall top free agents (regardless of position) surfaced for
# hidden-gem flavor; keeps the previous morning-briefing signal.
TOP_FA_OVERALL = 5

# Yahoo slot codes that do not correspond to a real MLB position and
# therefore cannot be "upgraded at position" — bench, DL, NA, utility
# catch-alls. These still appear on the roster but are excluded from the
# per-position upgrade search because they accept any eligible player.
_NON_COMPETITIVE_SLOTS = frozenset({"BN", "IL", "IL10", "IL60", "NA", "DL"})

# Yahoo uses ``Util`` (batter flex) and ``P`` (any pitcher). We treat
# these as upgrade-worthy but compare only against same-handedness pools
# (hitter Util vs batters, P vs all pitchers) to avoid nonsense swaps.
_HITTER_FLEX_SLOTS = frozenset({"Util", "UTIL"})
_PITCHER_FLEX_SLOTS = frozenset({"P"})


@dataclass(frozen=True)
class PlayerLite:
    """Minimal projection of ``Player`` suitable for recommendation output."""

    id: int
    name: str
    primary_position: str
    eligible_positions: tuple[str, ...]
    team_abbr: str | None
    is_pitcher: bool
    injury_status: str | None


@dataclass(frozen=True)
class ValuedPlayer:
    """A player paired with their latest season composite value."""

    player: PlayerLite
    composite_value: Decimal
    our_rank: int | None
    yahoo_rank: int | None
    next_games_value: Decimal | None
    roster_slot: str | None = None


@dataclass
class PositionUpgrade:
    """Per-position comparison between the weakest starter and top waivers."""

    position: str
    incumbent: ValuedPlayer | None
    top_free_agents: list[ValuedPlayer] = field(default_factory=list)

    @property
    def best_fa(self) -> ValuedPlayer | None:
        return self.top_free_agents[0] if self.top_free_agents else None

    @property
    def delta(self) -> Decimal | None:
        if self.incumbent is None or self.best_fa is None:
            return None
        return self.best_fa.composite_value - self.incumbent.composite_value

    @property
    def recommend(self) -> bool:
        d = self.delta
        return d is not None and d >= UPGRADE_THRESHOLD


@dataclass
class DropCandidate:
    """A roster player the manager should consider dropping."""

    player: ValuedPlayer
    reason: str  # "lowest_overall" | "upgrade_available_at_position"
    position: str | None = None
    replacement: ValuedPlayer | None = None
    delta: Decimal | None = None


@dataclass
class RosterRecommendations:
    """Position-aligned drop/pickup plan for one fantasy team."""

    team_id: int
    team_name: str
    roster: list[ValuedPlayer]
    drop_candidates: list[DropCandidate]
    upgrades_by_position: dict[str, PositionUpgrade]
    top_fa_overall: list[ValuedPlayer]


def _required_starter_positions(roster_positions: list[dict]) -> set[str]:
    """Extract real on-field starter slots from the league config.

    Filters out BN / IL / DL / NA — those accept any eligible player and
    therefore carry no positional upgrade signal.
    """
    positions: set[str] = set()
    for entry in roster_positions or []:
        pos = (entry or {}).get("position")
        if not isinstance(pos, str):
            continue
        if pos in _NON_COMPETITIVE_SLOTS:
            continue
        positions.add(pos)
    return positions


def _eligible_for(position: str, eligible_positions: tuple[str, ...], is_pitcher: bool) -> bool:
    """Return True if this player can fill the given starter slot."""
    if not eligible_positions:
        return False
    if position in eligible_positions:
        return True
    if position in _HITTER_FLEX_SLOTS:
        return not is_pitcher
    if position in _PITCHER_FLEX_SLOTS:
        return is_pitcher
    return False


def _to_lite(player: Player) -> PlayerLite:
    return PlayerLite(
        id=player.id,
        name=player.name,
        primary_position=player.primary_position,
        eligible_positions=tuple(player.eligible_positions or ()),
        team_abbr=player.team_abbr,
        is_pitcher=player.is_pitcher,
        injury_status=player.injury_status,
    )


async def _latest_season_value_map(
    db: AsyncSession, player_ids: list[int]
) -> dict[int, PlayerValueSnapshot]:
    """Return the latest season snapshot per player (empty for missing)."""
    result: dict[int, PlayerValueSnapshot] = {}
    if not player_ids:
        return result
    rows = await db.execute(
        select(PlayerValueSnapshot)
        .where(
            PlayerValueSnapshot.player_id.in_(player_ids),
            PlayerValueSnapshot.type == "season",
        )
        .order_by(PlayerValueSnapshot.snapshot_date.desc())
    )
    for snap in rows.scalars():
        if snap.player_id not in result:
            result[snap.player_id] = snap
    return result


async def _latest_next_games_value_map(
    db: AsyncSession, player_ids: list[int]
) -> dict[int, PlayerValueSnapshot]:
    """Return the latest next_games snapshot per player."""
    result: dict[int, PlayerValueSnapshot] = {}
    if not player_ids:
        return result
    rows = await db.execute(
        select(PlayerValueSnapshot)
        .where(
            PlayerValueSnapshot.player_id.in_(player_ids),
            PlayerValueSnapshot.type == "next_games",
        )
        .order_by(PlayerValueSnapshot.snapshot_date.desc())
    )
    for snap in rows.scalars():
        if snap.player_id not in result:
            result[snap.player_id] = snap
    return result


def _valued(
    player: Player,
    season_snap: PlayerValueSnapshot | None,
    next_snap: PlayerValueSnapshot | None,
    roster_slot: str | None = None,
) -> ValuedPlayer | None:
    """Pair a player with their snapshots; skip if no season value."""
    if season_snap is None:
        return None
    return ValuedPlayer(
        player=_to_lite(player),
        composite_value=Decimal(season_snap.composite_value),
        our_rank=season_snap.our_rank,
        yahoo_rank=season_snap.yahoo_rank or player.yahoo_rank,
        next_games_value=(
            Decimal(next_snap.composite_value) if next_snap is not None else None
        ),
        roster_slot=roster_slot,
    )


async def build_recommendations(
    db: AsyncSession, team: Team, league: League | None, current_week: int
) -> RosterRecommendations:
    """Compute position-aligned drop/pickup recommendations for ``team``.

    Issues three bounded queries (roster join, free-agent subquery,
    snapshot lookup) and folds everything in Python. All heavy filtering
    is either index-supported or bounded by the size of one team's
    roster (~26) and the free-agent pool for the current league.

    Args:
        db: Async session bound to the caller's request/task scope.
        team: Target team; recommendations are computed against this
            team's active roster for ``current_week``.
        league: Parent league, used for ``roster_positions``. Pass
            ``None`` to fall back to a conservative default that only
            surfaces globally worst players (no positional upgrades).
        current_week: Fantasy week whose roster should be evaluated.

    Returns:
        ``RosterRecommendations`` suitable for both API serialization
        and direct inclusion in an LLM prompt payload.
    """

    roster_rows_result = await db.execute(
        select(RosterSlot, Player)
        .join(Player, Player.id == RosterSlot.player_id)
        .where(
            RosterSlot.team_id == team.id,
            RosterSlot.week == current_week,
            RosterSlot.is_active.is_(True),
        )
    )
    roster_rows = roster_rows_result.all()
    roster_player_ids = [p.id for _, p in roster_rows]

    fa_subq = (
        select(
            FreeAgentSnapshot.player_id,
            FreeAgentSnapshot.snapshot_at,
        )
        .where(FreeAgentSnapshot.league_id == (league.id if league else -1))
        .where(FreeAgentSnapshot.is_available.is_(True))
        .distinct(FreeAgentSnapshot.player_id)
        .order_by(FreeAgentSnapshot.player_id, FreeAgentSnapshot.snapshot_at.desc())
        .subquery()
    )
    fa_rows_result = await db.execute(
        select(Player).join(fa_subq, fa_subq.c.player_id == Player.id)
    )
    fa_players = list(fa_rows_result.scalars())
    fa_player_ids = [p.id for p in fa_players]

    all_ids = list({*roster_player_ids, *fa_player_ids})
    season_map = await _latest_season_value_map(db, all_ids)
    next_map = await _latest_next_games_value_map(db, all_ids)

    roster_valued: list[ValuedPlayer] = []
    roster_by_id: dict[int, ValuedPlayer] = {}
    for slot, player in roster_rows:
        vp = _valued(player, season_map.get(player.id), next_map.get(player.id), roster_slot=slot.position)
        if vp is None:
            continue
        roster_valued.append(vp)
        roster_by_id[player.id] = vp

    fa_valued: list[ValuedPlayer] = []
    for player in fa_players:
        vp = _valued(player, season_map.get(player.id), next_map.get(player.id))
        if vp is not None:
            fa_valued.append(vp)
    fa_valued.sort(key=lambda vp: vp.composite_value, reverse=True)

    required_positions = _required_starter_positions(
        league.roster_positions if league else []
    )

    upgrades_by_position: dict[str, PositionUpgrade] = {}
    for position in sorted(required_positions):
        eligible_roster = [
            vp
            for vp in roster_valued
            if _eligible_for(position, vp.player.eligible_positions, vp.player.is_pitcher)
            and (vp.roster_slot or "") not in _NON_COMPETITIVE_SLOTS
        ]
        incumbent = min(eligible_roster, key=lambda vp: vp.composite_value, default=None)

        eligible_fas = [
            vp
            for vp in fa_valued
            if _eligible_for(position, vp.player.eligible_positions, vp.player.is_pitcher)
        ][:TOP_FA_PER_POSITION]

        upgrades_by_position[position] = PositionUpgrade(
            position=position,
            incumbent=incumbent,
            top_free_agents=eligible_fas,
        )

    drops: list[DropCandidate] = []
    seen_player_ids: set[int] = set()

    starter_valued = [vp for vp in roster_valued if (vp.roster_slot or "") not in _NON_COMPETITIVE_SLOTS]
    for vp in sorted(starter_valued, key=lambda v: v.composite_value)[:GLOBAL_WORST_COUNT]:
        drops.append(DropCandidate(player=vp, reason="lowest_overall"))
        seen_player_ids.add(vp.player.id)

    for upgrade in upgrades_by_position.values():
        if not upgrade.recommend or upgrade.incumbent is None:
            continue
        incumbent = upgrade.incumbent
        if incumbent.player.id in seen_player_ids:
            # Already flagged globally; attach position-specific replacement
            # info to the existing entry instead of duplicating the player.
            for existing in drops:
                if existing.player.player.id == incumbent.player.id:
                    existing.reason = "upgrade_available_at_position"
                    existing.position = upgrade.position
                    existing.replacement = upgrade.best_fa
                    existing.delta = upgrade.delta
                    break
            continue
        drops.append(
            DropCandidate(
                player=incumbent,
                reason="upgrade_available_at_position",
                position=upgrade.position,
                replacement=upgrade.best_fa,
                delta=upgrade.delta,
            )
        )
        seen_player_ids.add(incumbent.player.id)

    top_fa_overall = fa_valued[:TOP_FA_OVERALL]

    return RosterRecommendations(
        team_id=team.id,
        team_name=team.name,
        roster=roster_valued,
        drop_candidates=drops,
        upgrades_by_position=upgrades_by_position,
        top_fa_overall=top_fa_overall,
    )


def recommendations_to_prompt_payload(rec: RosterRecommendations) -> dict:
    """Serialize recommendations into a JSON-safe dict for LLM prompts.

    Uses plain ``float`` for numerics (Decimal does not JSON-serialize
    natively) and omits object graph details the model doesn't need,
    keeping the prompt token budget small.
    """

    def _vp(vp: ValuedPlayer | None) -> dict | None:
        if vp is None:
            return None
        return {
            "id": vp.player.id,
            "name": vp.player.name,
            "primary_position": vp.player.primary_position,
            "eligible_positions": list(vp.player.eligible_positions),
            "team_abbr": vp.player.team_abbr,
            "is_pitcher": vp.player.is_pitcher,
            "injury_status": vp.player.injury_status,
            "composite_value": float(vp.composite_value),
            "next_games_value": float(vp.next_games_value) if vp.next_games_value is not None else None,
            "our_rank": vp.our_rank,
            "yahoo_rank": vp.yahoo_rank,
            "roster_slot": vp.roster_slot,
        }

    return {
        "team_name": rec.team_name,
        "roster": [_vp(vp) for vp in rec.roster],
        "drop_candidates": [
            {
                "player": _vp(d.player),
                "reason": d.reason,
                "position": d.position,
                "replacement": _vp(d.replacement),
                "delta": float(d.delta) if d.delta is not None else None,
            }
            for d in rec.drop_candidates
        ],
        "upgrades_by_position": {
            pos: {
                "incumbent": _vp(up.incumbent),
                "top_free_agents": [_vp(fa) for fa in up.top_free_agents],
                "delta": float(up.delta) if up.delta is not None else None,
                "recommend": up.recommend,
            }
            for pos, up in rec.upgrades_by_position.items()
        },
        "top_fa_overall": [_vp(vp) for vp in rec.top_fa_overall],
    }
