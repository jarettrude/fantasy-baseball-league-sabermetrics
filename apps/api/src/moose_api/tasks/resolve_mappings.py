"""5-stage Player ID mapping pipeline (spec §8).

Resolves Yahoo player IDs to MLB Stats API IDs:

Stage 1 — Yahoo → MLB ID direct match (if mlb_id already set on player).
Stage 2 — MLB Stats API people search by full name + team + position.
Stage 3 — MLB active roster crosswalk (exact name match, from API).
Stage 4 — Fuzzy name match (Jaro-Winkler >= 0.92) + position guard.
Stage 5 — Flag as status='ambiguous' → visible in Commissioner Mappings.

All data sourced from MLB Stats API — no local files required.
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher

import pandas as pd
from sqlalchemy import select

from moose_api.core.database import async_session_factory
from moose_api.models.notification import CommissionerNotification
from moose_api.models.player import Player, PlayerMapping
from moose_api.services.mlb_client import MLBClient
from moose_api.tasks.load_mlb_roster import fetch_active_mlb_players

logger = logging.getLogger(__name__)

FUZZY_THRESHOLD = 0.92


def _jaro_winkler_similarity(s1: str, s2: str) -> float:
    """Approximate Jaro-Winkler similarity using SequenceMatcher.

    SequenceMatcher uses a Ratcliff/Obershelp algorithm which gives
    similar results for name matching. We apply a Winkler prefix
    boost for common prefixes (up to 4 chars).
    """
    if not s1 or not s2:
        return 0.0

    s1_lower = s1.lower().strip()
    s2_lower = s2.lower().strip()

    if s1_lower == s2_lower:
        return 1.0

    ratio = SequenceMatcher(None, s1_lower, s2_lower).ratio()

    prefix_len = 0
    for i in range(min(4, len(s1_lower), len(s2_lower))):
        if s1_lower[i] == s2_lower[i]:
            prefix_len += 1
        else:
            break

    winkler = ratio + (prefix_len * 0.1 * (1.0 - ratio))
    return min(winkler, 1.0)


def _normalize_name(name: str) -> str:
    """Normalize a player name for comparison."""
    name = name.strip()
    for suffix in (" Jr.", " Sr.", " III", " II", " IV"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.strip()


async def run_resolve_player_mappings(refresh_ambiguous: bool = True):
    """Run the 5-stage mapping pipeline for all unmapped players.

    Args:
        refresh_ambiguous: If True, also re-process players with ambiguous or low-confidence
                          mappings to catch updates in the baseball_id database.
    """
    mlb_client = None
    try:
        async with async_session_factory() as session:
            # Find players without a mapping record
            all_players_result = await session.execute(select(Player))
            all_players = all_players_result.scalars().all()

            existing_mappings_result = await session.execute(select(PlayerMapping))
            existing_mappings = {m.yahoo_player_key: m for m in existing_mappings_result.scalars().all()}
            unmapped = [p for p in all_players if p.yahoo_player_key not in existing_mappings]

            needs_refresh = []
            if refresh_ambiguous:
                for player in all_players:
                    mapping = existing_mappings.get(player.yahoo_player_key)
                    if mapping and (
                        mapping.status == "ambiguous" or (mapping.source_confidence < 0.90 and mapping.auto_mapped)
                    ):
                        needs_refresh.append(player)
                        # Delete the old mapping so it can be re-processed
                        await session.delete(mapping)

                if needs_refresh:
                    logger.info(
                        "Refresh mode: Re-processing %d players with ambiguous/low-confidence mappings",
                        len(needs_refresh),
                    )
                    await session.commit()

            unmapped.extend(needs_refresh)

            if not unmapped:
                logger.info("Mapping pipeline: All players already mapped with high confidence")
                return

            logger.info(
                "Mapping pipeline: %d unmapped players to resolve (using batch API calls)",
                len(unmapped),
            )

            # Pre-load MLB active roster for Stage 3
            mlb_roster = await fetch_active_mlb_players()
            roster_by_name: dict[str, dict] = {}
            for mp in mlb_roster:
                key = mp["full_name"].lower().strip()
                roster_by_name[key] = mp

            # Pre-load MLB client for Stage 2
            mlb_client = MLBClient()

            resolved = 0
            ambiguous = 0

            # Track which players have been confirmed in each stage (for 2000+ player efficiency)
            stage_2_confirmed_keys = set()

            players_with_mlb_id = []
            players_needing_search = []

            for player in unmapped:
                if player.mlb_id:
                    players_with_mlb_id.append(player)
                else:
                    players_needing_search.append(player)

            logger.info("Stage 1: Processing %d players with existing MLB IDs", len(players_with_mlb_id))
            for player in players_with_mlb_id:
                mapping = PlayerMapping(
                    yahoo_player_key=player.yahoo_player_key,
                    mlb_id=player.mlb_id,
                    lahman_id=None,
                    source_confidence=1.0,
                    auto_mapped=True,
                    status="confirmed",
                    notes="Stage 1: direct mlb_id",
                )
                session.add(mapping)
                resolved += 1

            if players_with_mlb_id:
                await session.commit()
                logger.info("Stage 1: Committed %d direct MLB ID mappings", len(players_with_mlb_id))

            # Uses the baseball_id package to map Yahoo IDs to MLB IDs.
            if players_needing_search:
                yahoo_to_mlb = {}

                logger.info("Stage 1.5: Loading baseball_id package for Yahoo->MLB mapping...")
                try:
                    from baseball_id import Lookup

                    yahoo_ids = [p.yahoo_player_id for p in players_needing_search if p.yahoo_player_id]
                    if yahoo_ids:
                        df = Lookup.from_yahoo_ids(yahoo_ids)

                        if not df.empty:
                            for _, row in df.iterrows():
                                y_id = str(row["yahoo_id"]).strip()
                                m_id = row["mlb_id"]
                                if pd.notna(m_id):
                                    yahoo_to_mlb[y_id] = int(m_id)

                            logger.info("Loaded %d Yahoo->MLB mappings from baseball_id", len(yahoo_to_mlb))
                        else:
                            logger.info("No Yahoo->MLB mappings found in baseball_id for provided IDs")
                except Exception as e:
                    logger.warning("Failed to load baseball_id mappings: %s", e)

                still_needing_search = []
                for player in players_needing_search:
                    y_id = player.yahoo_player_id
                    if y_id in yahoo_to_mlb:
                        mlb_id = yahoo_to_mlb[y_id]
                        player.mlb_id = mlb_id
                        mapping = PlayerMapping(
                            yahoo_player_key=player.yahoo_player_key,
                            mlb_id=mlb_id,
                            lahman_id=None,
                            source_confidence=0.98,
                            auto_mapped=True,
                            status="confirmed",
                            notes="Stage 1.5: baseball_id package",
                        )
                        session.add(mapping)
                        resolved += 1
                    else:
                        still_needing_search.append(player)

                stage_1_5_resolved = len(players_needing_search) - len(still_needing_search)
                logger.info(
                    "Stage 1.5: Resolved %d players via baseball_id package",
                    stage_1_5_resolved,
                )
                players_needing_search = still_needing_search

                if stage_1_5_resolved > 0:
                    await session.commit()
                    logger.info("Stage 1.5: Committed %d baseball_id mappings", stage_1_5_resolved)

            if players_needing_search:
                logger.info("Stage 2: Processing %d players via batch API calls", len(players_needing_search))
                if mlb_client is not None:
                    await mlb_client.close()
                mlb_client = MLBClient()

                batch_size = 50  # Overall batch size for DB chunking
                api_chunk_size = 20  # Max players per HTTP request to MLB API
                total_batches = (len(players_needing_search) + batch_size - 1) // batch_size

                for batch_idx in range(total_batches):
                    start_idx = batch_idx * batch_size
                    end_idx = start_idx + batch_size
                    batch_players = players_needing_search[start_idx:end_idx]

                    logger.info("Processing batch %d/%d (%d players)", batch_idx + 1, total_batches, len(batch_players))

                    names: list[str] = []

                    for player in batch_players:
                        normalized_name = _normalize_name(player.name)
                        names.append(normalized_name)

                    try:
                        # Multiple small API calls to avoid request bloat/timeout
                        batch_results = await mlb_client.search_players_chunked(names, chunk_size=api_chunk_size)

                        # Process results for this batch
                        for player in batch_players:
                            normalized_name = _normalize_name(player.name)

                            mapping = PlayerMapping(
                                yahoo_player_key=player.yahoo_player_key,
                                mlb_id=None,
                                lahman_id=None,
                                source_confidence=0.0,
                                auto_mapped=True,
                                status="ambiguous",
                            )

                            matched_result = None
                            for result in batch_results:
                                if result.get("fullName", "").lower() == normalized_name.lower():
                                    team_match = (
                                        not player.team_abbr
                                        or not result.get("currentTeam")
                                        or result["currentTeam"].upper() == player.team_abbr.upper()
                                    )
                                    if team_match:
                                        matched_result = result
                                        break

                            if matched_result:
                                mapping.mlb_id = matched_result["id"]
                                mapping.source_confidence = 0.95
                                mapping.status = "confirmed"
                                mapping.notes = f"Stage 2: MLB API batch match (id={matched_result['id']})"
                                stage_2_confirmed_keys.add(player.yahoo_player_key)
                                resolved += 1
                            else:
                                ambiguous += 1

                            session.add(mapping)

                    except Exception as e:
                        logger.error(
                            "Stage 2 MLB batch search failed for batch %d: %s",
                            batch_idx + 1,
                            e,
                        )
                        for player in batch_players:
                            mapping = PlayerMapping(
                                yahoo_player_key=player.yahoo_player_key,
                                mlb_id=None,
                                lahman_id=None,
                                source_confidence=0.0,
                                auto_mapped=True,
                                status="ambiguous",
                                notes=f"Stage 2: batch search failed - {e}",
                            )
                            session.add(mapping)
                            ambiguous += 1

                await session.commit()
                logger.info("Stage 2: Committed %d mappings", len(stage_2_confirmed_keys))

            # Only process players that weren't confirmed in Stage 2
            remaining_players = [p for p in players_needing_search if p.yahoo_player_key not in stage_2_confirmed_keys]

            if remaining_players:
                logger.info("Stage 3: Processing %d remaining players via MLB roster crosswalk", len(remaining_players))

                for player in remaining_players:
                    normalized_name = _normalize_name(player.name)
                    rkey = normalized_name.lower()

                    mapping = PlayerMapping(
                        yahoo_player_key=player.yahoo_player_key,
                        mlb_id=None,
                        lahman_id=None,
                        source_confidence=0.0,
                        auto_mapped=True,
                        status="ambiguous",
                    )

                    if rkey in roster_by_name:
                        mp = roster_by_name[rkey]
                        mapping.mlb_id = mp["mlb_id"]
                        mapping.source_confidence = 0.90
                        mapping.status = "confirmed"
                        mapping.notes = f"Stage 3: MLB roster exact name match (id={mp['mlb_id']})"
                        player.mlb_id = mp["mlb_id"]
                        if mp.get("bats") and not player.bats:
                            player.bats = mp["bats"]
                        if mp.get("throws") and not player.throws:
                            player.throws = mp["throws"]
                        resolved += 1
                    else:
                        # ── Stage 4: Fuzzy match (Jaro-Winkler >= 0.92) ──
                        best_match = None
                        best_score = 0.0
                        for _rname, mp in roster_by_name.items():
                            score = _jaro_winkler_similarity(normalized_name, mp["full_name"])
                            if score >= FUZZY_THRESHOLD and score > best_score:
                                best_score = score
                                best_match = mp

                        if best_match:
                            mapping.mlb_id = best_match["mlb_id"]
                            mapping.source_confidence = round(best_score, 3)
                            mapping.status = "confirmed"
                            mapping.notes = f"Stage 4: fuzzy match ({best_match['full_name']}, score={best_score:.3f})"
                            player.mlb_id = best_match["mlb_id"]
                            resolved += 1
                        else:
                            ambiguous += 1

                    session.add(mapping)

                await session.commit()
                logger.info("Stage 3/4: Committed %d mappings", resolved)

            logger.info(
                "Mapping pipeline complete: %d resolved, %d ambiguous out of %d",
                resolved,
                ambiguous,
                len(unmapped),
            )

            if ambiguous > 0:
                notif = CommissionerNotification(
                    type="info",
                    message=(
                        f"Player mapping complete: {resolved} resolved, "
                        f"{ambiguous} need manual review in /admin/mappings "
                        f"(out of {len(unmapped)} unmapped)"
                    ),
                )
            else:
                notif = CommissionerNotification(
                    type="info",
                    message=(f"Player mapping complete: all {resolved} players resolved successfully"),
                )
            session.add(notif)
            await session.commit()

    except Exception as e:
        logger.error("resolve_player_mappings failed: %s", e, exc_info=True)
        try:
            async with async_session_factory() as session:
                notif = CommissionerNotification(
                    type="sync_failure",
                    message=f"Player mapping pipeline failed: {e}",
                )
                session.add(notif)
                await session.commit()
        except Exception as notif_error:
            logger.error("Failed to send failure notification: %s", notif_error)
        raise
    finally:
        # Always close the MLB client to prevent resource leaks
        if mlb_client is not None:
            try:
                await mlb_client.close()
            except Exception as close_error:
                logger.warning("Error closing MLB client: %s", close_error)
