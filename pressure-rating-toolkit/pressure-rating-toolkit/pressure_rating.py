#!/usr/bin/env python3
"""
pressure_rating.py
==================
SupeRR Selector, Season 2 · Round 1  ·  "Who performs best under pressure?"

ONE FILE that runs the whole Pressure Rating pipeline on ANY ball-by-ball T20 dataset
(IPL, a domestic tournament, an uncapped-player league, a women's league...).

    python pressure_rating.py --input my_balls.csv --out results/
    python pressure_rating.py --template                # writes an empty CSV with the required columns
    python pressure_rating.py --selftest                # runs the whole pipeline on synthetic data (about 1 minute)

WHAT IT DOES (the same steps as the project notebooks, in order)
  1. LOAD + VALIDATE   check the columns, repair obvious problems, drop super overs, log every decision.
  2. MATCH STATE       score, wickets and balls left BEFORE each ball; target and required rate in a chase.
  3. GROUND PAR        what is a normal first-innings total at this ground in this season (leave-one-out, shrunk).
  4. EXPECTATION       "what would a typical player do on this ball?" (runs, wicket chance), with NO player identity.
  5. WIN PROBABILITY   how likely is the batting side to win, ball by ball (out-of-fold, grouped by match).
  6. LEVERAGE INDEX    how much can the NEXT ball swing that probability (average ball = 1.00).
  7. PRESSURE BALLS    top 15/20/25/30% of balls by leverage, and the player's own side is not cruising.
  8. WICKET PRICES     what a dismissal is worth, in runs, in each phase (learned from the data, not assumed).
  9. NET RUNS          runs and wickets in ONE currency: runs above what the situation expected.
 10. RATING            empirical-Bayes shrinkage combining pressure record and everyday record (honest uncertainty).
 11. RANK + SELECT     80% interval, chance of a true top 10 (simulation), 2-of-4-cutoffs rule, tiers A/B/C.

WHAT YOU GET (in --out): ratings_batters.csv, ratings_bowlers.csv, shortlist.xlsx (if openpyxl is installed),
                         diagnostics.json (model quality, wicket prices, thresholds, checks), run_config.json, run.log

REQUIREMENTS: Python 3.10+, numpy, pandas, scikit-learn, lightgbm  (optional: pyarrow for parquet input, openpyxl for Excel output)

HOW THE RESULTS SHOULD BE READ
  * A rating is "runs above what the situation expected" on pressure balls, per 100 balls (batters) or per 24 balls (bowlers),
    measured against THIS dataset only. Ratings from two different leagues are NOT on one scale (opposition strength differs).
  * Small samples give wide intervals. The 80% interval and "chance of true top 10" are part of the answer, not decoration.
  * Ratings are descriptive: they say who handled pressure best in the data supplied, not who will be best next season.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
log = logging.getLogger("pressure_rating")
__version__ = "1.0.0"


# ════════════════════════════════════════════════════════════════════════════════════════════════
# 0 · CONFIGURATION
# Every tunable choice lives here, in one place, so a run can be described by one JSON file (run_config.json).
# ════════════════════════════════════════════════════════════════════════════════════════════════
@dataclass
class Config:
    # --- format ---
    max_overs: int = 20                 # T20 = 20. (A 10-over or 100-ball format needs more than changing this: see README.)
    powerplay_end: int = 6              # overs 1..6   = powerplay
    death_start: int = 16               # overs 16..20 = death; the rest = middle
    # --- what counts as pressure ---
    quantiles: tuple = (0.15, 0.20, 0.25, 0.30)   # "top q% of balls by leverage". The MAIN one is primary_q; the others test robustness.
    primary_q: float = 0.20
    comfort: float = 0.60               # batter pressure needs win chance <= 0.60; bowler pressure needs win chance >= 1 - 0.60
    # --- ratings ---
    min_pressure_balls: int = 60        # rating floor: a player needs at least this many pressure balls to be rated
    min_pool: int = 30                  # minimum balls (pressure AND everyday) for a player to enter the shrinkage model
    scale_bat: int = 100                # batters are rated per 100 balls
    scale_bowl: int = 24                # bowlers per 24 balls (4 overs)
    n_sims: int = 4000                  # simulations behind the 80% interval and the chance of a true top 10
    # --- selection rule ---
    n_picks: int = 10                   # list length
    pass_min_cutoffs: int = 2           # a pick must be top 10 under at least this many of the 4 cutoffs
    reserve_rank: int = 20              # a passing player ranked below the picks but within this rank is a reserve
    # --- modelling ---
    n_folds: int = 5                    # grouped cross-validation folds (grouped by MATCH: no match is in train and test together)
    seed: int = 42
    fast: bool = False                  # quick-look settings (3 folds, bigger learning rate). Fine for testing, not for a final run.
    wicket_boot: int = 0                # bootstrap resamples for the wicket-price bands (0 = skip, prices are still computed)
    illegal_extras: tuple = ("wide", "noball", "other")
    bowler_credit: tuple = ("bowled", "caught", "lbw", "stumped", "hit wicket")   # dismissal types credited to the bowler


# Column names the script understands. If your file uses other names, either rename them, or pass --map key=your_column.
# `aliases` are tried automatically (case-insensitive) so most common exports need no mapping at all.
SCHEMA: dict[str, dict] = {
    # key               required  aliases (first match wins)                                          meaning
    "match_id":        dict(req=True,  aliases=["match_id", "matchid", "match", "id"]),            # one value per match
    "innings":         dict(req=True,  aliases=["innings", "inning", "inns", "innings_no"]),       # 1 or 2 (3+ = super over, dropped)
    "over":            dict(req=True,  aliases=["over", "overs", "overid", "over_number"]),        # over number (1-based, or 0-based: see --over-base)
    "ball_seq":        dict(req=False, aliases=["ball_seq", "ball_id", "innings_ball_num", "delivery", "ball_number", "ball"]),   # order inside the innings
    "batting_team":    dict(req=True,  aliases=["batting_team", "bat_team", "batting_team_id", "team_batting"]),
    "bowling_team":    dict(req=True,  aliases=["bowling_team", "bowl_team", "bowling_team_id", "team_bowling"]),
    "striker":         dict(req=True,  aliases=["striker", "striker_id", "batter", "batsman", "batter_id"]),
    "bowler":          dict(req=True,  aliases=["bowler", "bowler_id"]),
    "runs_batter":     dict(req=True,  aliases=["runs_batter", "runs_off_bat", "batter_runs", "runs", "batsman_runs"]),   # runs off the bat
    "extras_runs":     dict(req=True,  aliases=["extras_runs", "extras", "extra_runs"]),            # extra runs on this ball (0 if none)
    "extras_type":     dict(req=False, aliases=["extras_type", "extra_type", "extras_kind"]),       # wide / noball / byes / legbyes / penalty
    "is_wicket":       dict(req=True,  aliases=["is_wicket", "wicket", "is_out", "player_out_flag"]),   # 1 if a wicket fell on this ball
    "dismissed_player": dict(req=False, aliases=["dismissed_player", "dismissed_player_id", "player_dismissed", "player_out"]),
    "dismissal_type":  dict(req=False, aliases=["dismissal_type", "wicket_type", "how_out", "dismissal_kind"]),
    "match_winner":    dict(req=True,  aliases=["match_winner", "winner", "win_team", "win_team_id", "winning_team"]),   # same labels as the team columns
    "venue":           dict(req=False, aliases=["venue", "venue_id", "ground", "stadium"]),
    "season":          dict(req=False, aliases=["season", "year", "edition"]),
    "competition":     dict(req=False, aliases=["competition", "tier", "league", "tournament", "comp_name"]),   # use if ONE file mixes leagues (IPL + domestic...)
    "bowler_type":     dict(req=False, aliases=["bowler_type", "pace_or_spin", "bowl_kind"]),       # optional: 'pace' / 'spin' (adds a little accuracy)
    "batter_hand":     dict(req=False, aliases=["batter_hand", "batting_hand", "batter_left_hand"]),   # optional: left / right
}


# ════════════════════════════════════════════════════════════════════════════════════════════════
# 1 · LOAD + VALIDATE
# ════════════════════════════════════════════════════════════════════════════════════════════════
def load_table(path: Path) -> pd.DataFrame:
    """Read CSV / Parquet / Excel. Quoting every dtype guess is avoided: IDs stay strings so '007' is not turned into 7."""
    suffix = path.suffix.lower()
    if suffix in (".parquet", ".pq"):
        return pd.read_parquet(path)
    if suffix in (".xlsx", ".xls"):
        return pd.read_excel(path)
    return pd.read_csv(path, low_memory=False)


def standardise_columns(df: pd.DataFrame, user_map: dict[str, str]) -> pd.DataFrame:
    """Rename the user's columns to the canonical names in SCHEMA; fail early and clearly if a required one is missing."""
    lower = {c.lower().strip(): c for c in df.columns}
    rename, missing = {}, []
    for key, spec in SCHEMA.items():
        if key in user_map:                                   # explicit --map always wins
            if user_map[key] not in df.columns:
                raise SystemExit(f"--map {key}={user_map[key]}: column '{user_map[key]}' not found in the file.")
            rename[user_map[key]] = key
            continue
        hit = next((lower[a] for a in spec["aliases"] if a in lower and lower[a] not in rename), None)
        if hit is not None:
            rename[hit] = key
        elif spec["req"]:
            missing.append(key)
    if missing:
        raise SystemExit("Required columns not found: " + ", ".join(missing) + "\n"
                         "Run with --template to see the expected layout, or use --map key=your_column.\n"
                         f"Columns in your file: {list(df.columns)}")
    out = df.rename(columns=rename)[[c for c in SCHEMA if c in rename.values()]].copy()
    log.info("Columns mapped: %s", {v: k for k, v in rename.items()})
    return out


def normalise_extras(s: pd.Series) -> pd.Series:
    """Map the many spellings of extras to: wide / noball / byes / legbyes / penalty / other / '' (none)."""
    t = s.fillna("").astype(str).str.lower().str.strip()
    out = pd.Series("", index=s.index)
    out[t.str.contains("wide")] = "wide"
    out[t.str.contains("no ?ball|overstep|noball")] = "noball"
    out[t.str.contains("leg ?bye|legbye")] = "legbyes"
    out[t.str.contains(r"^bye|\bbyes\b") & (out == "")] = "byes"
    out[t.str.contains("pen")] = "penalty"
    out[(t != "") & (t != "none") & (t != "nan") & (out == "")] = "other"
    return out


def prepare_balls(raw: pd.DataFrame, cfg: Config, over_base: int) -> tuple[pd.DataFrame, dict]:
    """
    Turn the raw file into one clean, ordered ball table with the columns every later step needs.
    Returns (balls, audit) where `audit` records every row dropped or repaired (it ends up in diagnostics.json).
    """
    b = raw.copy()
    audit: dict = {"rows_in": int(len(b))}

    # -- types --------------------------------------------------------------------------------------
    # Labels (ids or names) become clean strings. A numeric id column that contains blanks is read as 12.0 instead of 12, which would make
    # "12.0" != "12" when the winner is compared with the batting team, so a trailing ".0" is removed.
    for c in ("match_id", "batting_team", "bowling_team", "striker", "bowler", "match_winner") + (("dismissed_player",) if "dismissed_player" in b.columns else ()):
        b[c] = b[c].astype("string").str.strip().str.replace(r"\.0$", "", regex=True)
    b["innings"] = pd.to_numeric(b.innings, errors="coerce")
    b["over"] = pd.to_numeric(b.over, errors="coerce") + (1 - over_base)         # canonical: over 1..20
    for c in ("runs_batter", "extras_runs"):
        b[c] = pd.to_numeric(b[c], errors="coerce").fillna(0).astype(int)
    b["is_wicket"] = pd.to_numeric(b.is_wicket, errors="coerce").fillna(0).astype(int).clip(0, 1).astype(bool)
    before = len(b)
    b = b.dropna(subset=["match_id", "innings", "over", "batting_team", "bowling_team", "striker", "bowler"])
    audit["dropped_missing_keys"] = int(before - len(b))

    # -- super overs and odd innings: kept out of the analysis (different rules and pressure) ---------
    so = ~b.innings.isin([1, 2])
    audit["super_over_or_other_innings_dropped"] = int(so.sum())
    b = b[~so].copy()
    b["innings"] = b.innings.astype(int)

    # -- order: the "next ball" must be the next row. Use ball_seq if supplied, else the file order -----
    if "ball_seq" in b.columns:
        b["ball_seq"] = pd.to_numeric(b.ball_seq, errors="coerce")
        b = b.sort_values(["match_id", "innings", "over", "ball_seq"], kind="stable") if b.ball_seq.notna().all() else b
    else:
        log.warning("No ball-order column found: using the row order of the file. Make sure it is playing order.")
    b = b.reset_index(drop=True)

    # -- extras, legal balls, runs ------------------------------------------------------------------
    b["extras_type"] = normalise_extras(b["extras_type"]) if "extras_type" in b.columns else \
        pd.Series(np.where(b.extras_runs > 0, "other", ""), index=b.index)
    b["is_legal"] = ~b.extras_type.isin(cfg.illegal_extras)          # does this delivery use up one of the 6 balls?
    b["total_runs"] = b.runs_batter + b.extras_runs
    b["faced"] = b.extras_type != "wide"                              # the batter did not face a wide
    # Runs charged to the bowler: everything except byes, leg-byes and penalty runs (those are not his fault).
    b["runs_conceded"] = np.where(b.extras_type.isin(["byes", "legbyes", "penalty"]), b.runs_batter, b.total_runs)

    # -- wickets: who is out, and does the bowler get the credit? ---------------------------------------
    dtype = b["dismissal_type"].fillna("").astype(str).str.lower().str.strip() if "dismissal_type" in b.columns else pd.Series("", index=b.index)
    if "dismissal_type" not in b.columns:
        log.warning("No dismissal_type column: every wicket is credited to the bowler, and charged to the striker. "
                    "Run-outs will therefore be slightly mis-assigned.")
    credit = dtype.apply(lambda t: any(k in t for k in cfg.bowler_credit)) if "dismissal_type" in b.columns else pd.Series(True, index=b.index)
    b["bowler_wicket"] = b.is_wicket & credit
    if "dismissed_player" in b.columns:
        dis = b["dismissed_player"].astype("string").str.strip()
        b["y_bat_out"] = (b.is_wicket & (dis == b.striker)).astype(int)
    else:
        b["y_bat_out"] = (b.is_wicket & ~dtype.str.contains("run out")).astype(int)
    b["y_bat_out"] = b.y_bat_out.where(b.faced)
    inn = b.groupby(["match_id", "innings"])
    too_many = (inn.is_wicket.transform("sum") > 10)
    audit["balls_in_innings_with_more_than_10_wickets"] = int(too_many.sum())
    if too_many.any():
        log.warning("%d balls sit in innings with more than 10 wickets: check the is_wicket column.", int(too_many.sum()))

    # -- match table: winner, innings totals, did the match finish normally? ---------------------------
    t = b.groupby(["match_id", "innings"]).agg(runs=("total_runs", "sum"), legal=("is_legal", "sum"), wkts=("is_wicket", "sum"),
                                               bat=("batting_team", "first")).reset_index()
    t1 = t[t.innings == 1].set_index("match_id")
    t2 = t[t.innings == 2].set_index("match_id")
    mt = pd.DataFrame(index=b.match_id.unique())
    mt["t1"], mt["legal1"], mt["wk1"] = t1.runs, t1.legal, t1.wkts
    mt["t2"], mt["legal2"], mt["wk2"] = t2.runs, t2.legal, t2.wkts
    mt["winner"] = b.groupby("match_id").match_winner.first()
    mt["team1"], mt["team2"] = t1.bat, t2.bat
    full1 = (mt.legal1 >= cfg.max_overs * 6 - 0) | (mt.wk1 >= 10)         # first innings ran its course
    # A match teaches the win-probability model only if: two innings, a decided winner who is one of the two sides, and an unshortened first innings.
    mt["clean"] = (mt.t2.notna() & full1 & (mt.winner == mt.team1) | mt.t2.notna() & full1 & (mt.winner == mt.team2)).fillna(False)
    audit["matches"] = int(len(mt)); audit["matches_clean_for_win_model"] = int(mt.clean.sum())
    b["t1"] = b.match_id.map(mt.t1)
    b["bat_won"] = (b.match_id.map(mt.winner) == b.batting_team).astype(float).where(b.match_id.map(mt.winner).notna())
    b["clean_match"] = b.match_id.map(mt.clean).fillna(False).astype(bool)
    audit["rows_out"] = int(len(b))
    return b, audit


# ════════════════════════════════════════════════════════════════════════════════════════════════
# 2 · MATCH STATE + GROUND PAR
# ════════════════════════════════════════════════════════════════════════════════════════════════
def add_match_state(b: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """
    Everything a cricketer knows BEFORE the ball is bowled. (Anything that includes the ball itself would leak the answer.)
    """
    g = b.groupby(["match_id", "innings"])
    b["legal_before"] = g.is_legal.cumsum() - b.is_legal                       # legal balls already bowled
    b["score_before"] = g.total_runs.cumsum() - b.total_runs
    b["wkts_before"] = g.is_wicket.cumsum() - b.is_wicket.astype(int)
    b["balls_left"] = cfg.max_overs * 6 - b.legal_before
    b["innings_ball"] = g.cumcount() + 1
    b["phase"] = np.select([b.over <= cfg.powerplay_end, b.over >= cfg.death_start], ["powerplay", "death"], "middle")
    b["is_chase"] = (b.innings == 2).astype(int)
    b["target"] = np.where(b.innings == 2, b.t1 + 1, 0)
    b["runs_needed"] = np.where(b.innings == 2, b.target - b.score_before, 0)
    # required run rate: capped so that "37 needed off 1 ball" does not become a number the models cannot digest
    b["rrr"] = np.where(b.innings == 2, np.clip(6 * b.runs_needed / b.balls_left.clip(lower=1), -6, 60), 0.0)
    b["crr"] = np.where(b.legal_before > 0, 6 * b.score_before / b.legal_before.clip(lower=1), 0.0)
    # Smoothed current run rate: after one ball the raw rate reads 24 or 0 an over. Blend in 12 balls of a typical 8-an-over start.
    b["crr_s"] = 6 * (b.score_before + 16) / (b.legal_before + 12)
    return b


def add_ground_par(b: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """
    GROUND PAR = what a normal first-innings total looks like at this ground in this season.
      era_base      : average first-innings total of that season (removes the "scores keep rising" trend)
      venue effect  : how much higher/lower this ground runs than its season average, estimated from OTHER matches only
                      (leave-one-out, so a match never helps define its own par) and SHRUNK toward 0 when the ground has few matches
                      (a ground with 2 matches should not be trusted as much as one with 40).
    Then par_at_ball = par total x (share of an innings normally scored by this legal ball); score_vs_par = score_before - par_at_ball.
    In a chase the 'par total' is the target itself.
    """
    m = b.groupby("match_id").agg(t1=("t1", "first"), season=("season", "first") if "season" in b.columns else ("t1", "size"),
                                  venue=("venue", "first") if "venue" in b.columns else ("t1", "size"),
                                  comp=("competition", "first") if "competition" in b.columns else ("t1", "size"))
    m = m[m.t1.notna()].copy()
    if "season" not in b.columns:
        m["season"] = "all"
    if "competition" not in b.columns:
        m["comp"] = "all"
    # era base = average first-innings total of that competition and season (scoring levels differ between leagues and drift over time)
    m["era_base"] = m.groupby(["comp", "season"]).t1.transform("mean")
    m["resid"] = m.t1 - m.era_base
    m["effect"] = 0.0
    if "venue" in b.columns:
        gsum, gn = m.groupby("venue").resid.transform("sum"), m.groupby("venue").resid.transform("size")
        # pooled within-ground variance (s2) and real between-ground spread (tau2, DerSimonian-Laird): how far to trust a ground's mean
        within = ((m.resid - m.groupby("venue").resid.transform("mean")) ** 2).sum() / max(len(m) - m.venue.nunique(), 1)
        gv = m.groupby("venue").resid.agg(n="size", mean="mean")
        w = gv.n / max(within, 1e-9); mu_w = (w * gv["mean"]).sum() / w.sum()
        Q = (w * (gv["mean"] - mu_w) ** 2).sum()
        tau2 = max(0.0, (Q - (len(gv) - 1)) / max(w.sum() - (w ** 2).sum() / w.sum(), 1e-9))
        k = within / tau2 if tau2 > 0 else 1e6                              # k = how many matches the league average is "worth"
        n_other = gn - 1
        loo_mean = np.where(n_other > 0, (gsum - m.resid) / n_other.clip(lower=1), 0.0)
        m["effect"] = n_other * loo_mean / (n_other + k)
        log.info("Ground effect: %d venues, real spread tau=%.1f runs, shrinkage k=%.1f matches", len(gv), np.sqrt(tau2), min(k, 1e6))
    m["venue_par"] = m.era_base + m.effect
    b["venue_par"] = b.match_id.map(m.venue_par).fillna(b.t1.mean() if b.t1.notna().any() else 160.0)
    # share of the final total scored by each legal ball, learned from first innings (a cumulative share cannot fall)
    i1 = b[(b.innings == 1) & b.t1.notna() & (b.t1 > 0)]
    share = (i1.score_before / i1.t1).groupby(i1.legal_before).mean().reindex(range(0, cfg.max_overs * 6 + 1)).interpolate().bfill().ffill()
    curve = pd.Series(np.maximum.accumulate(share.to_numpy()), index=share.index)
    curve.iloc[-1] = 1.0
    pos = (b.legal_before * (cfg.max_overs * 6) / (cfg.max_overs * 6)).round().clip(0, cfg.max_overs * 6).astype(int)
    par_total = np.where(b.innings == 1, b.venue_par, b.target)
    b["par_at_ball"] = par_total * pos.map(curve).to_numpy()
    b["score_vs_par"] = b.score_before - b.par_at_ball
    return b


# ════════════════════════════════════════════════════════════════════════════════════════════════
# 3 · MODEL HELPERS (grouped, out-of-fold)
# ════════════════════════════════════════════════════════════════════════════════════════════════
def _lgb():
    import lightgbm as lgb
    return lgb


def make_folds(b: pd.DataFrame, cfg: Config):
    """
    GROUPED CROSS-VALIDATION. Every ball is predicted by a model that NEVER SAW ITS MATCH ("out-of-fold").
    Why it matters: balls in one match are not independent (a collapse colours the next 30 balls). If a model saw part of a match,
    it would "remember" it, and the pressure it reports on that match would be fake.
    """
    from sklearn.model_selection import GroupKFold
    n = 3 if cfg.fast else cfg.n_folds
    n = max(2, min(n, b.match_id.nunique()))
    return list(GroupKFold(n_splits=n).split(b, groups=b.match_id.to_numpy()))


def lgb_oof(b: pd.DataFrame, folds, X: pd.DataFrame, y: pd.Series, rows: np.ndarray, objective: str, cfg: Config,
            params: dict | None = None, rounds: int = 1500, mono: list | None = None) -> tuple[pd.Series, list]:
    """Out-of-fold LightGBM predictions. A slice of the TRAINING matches is held out to decide when to stop adding trees (early stopping)."""
    lgb = _lgb()
    rng = np.random.default_rng(cfg.seed)
    groups = b.match_id.to_numpy()
    base = dict(objective=objective, learning_rate=0.1 if cfg.fast else 0.05, num_leaves=31, min_child_samples=200, subsample=0.8,
                subsample_freq=1, colsample_bytree=0.8, reg_lambda=5.0, n_jobs=-1, random_state=cfg.seed, verbosity=-1)
    base.update(params or {})
    if mono is not None:
        base["monotone_constraints"] = mono
    oof = pd.Series(np.nan, index=b.index)
    best = []
    for tr, te in folds:
        tr = tr[rows[tr]]
        te_r = te[rows[te]]
        m_tr = np.unique(groups[tr])
        m_val = rng.choice(m_tr, size=max(1, int(0.1 * len(m_tr))), replace=False)
        is_val = np.isin(groups[tr], m_val)
        d_fit = lgb.Dataset(X.iloc[tr[~is_val]], y.iloc[tr[~is_val]])
        d_val = lgb.Dataset(X.iloc[tr[is_val]], y.iloc[tr[is_val]], reference=d_fit)
        mdl = lgb.train(base, d_fit, num_boost_round=(300 if cfg.fast else rounds), valid_sets=[d_val],
                        callbacks=[lgb.early_stopping(50, verbose=False)])
        oof.iloc[te_r] = mdl.predict(X.iloc[te_r], num_iteration=mdl.best_iteration)
        best.append(int(mdl.best_iteration))
    return oof, best


# ════════════════════════════════════════════════════════════════════════════════════════════════
# 4 · EXPECTATION ENGINE  ("what would a typical player do on this ball?")
# ════════════════════════════════════════════════════════════════════════════════════════════════
def add_expectation(b: pd.DataFrame, folds, cfg: Config, diag: dict) -> pd.DataFrame:
    """
    Four models, trained out-of-fold, using ONLY the situation (never who the player is):
        exp_bat_runs   runs off the bat a typical batter scores on this ball (wides excluded: he did not face them)
        exp_runs_conc  runs a typical bowler concedes on this ball (wides / no-balls included: they cost him)
        p_bat_out      chance the striker is dismissed
        p_bowl_wkt     chance the bowler takes a wicket
    DESIGN CHOICE: no input tells the model how good this batter/bowler is (no career strike rate, no 'runs so far'). If it knew
    he was already on 60 off 25, it would EXPECT him to score fast and he would earn no credit. Skill must show up as the GAP between
    what happened and what the situation expected.
    """
    feats = ["innings", "over", "legal_before", "balls_left", "score_before", "wkts_before", "crr_s", "target", "runs_needed", "rrr",
             "venue_par", "score_vs_par"]
    X = b[feats].copy()
    X["phase_code"] = b.phase.map({"powerplay": 0, "middle": 1, "death": 2})
    for opt in ("bowler_type", "batter_hand", "competition"):      # optional context: adds a little accuracy when present
        if opt in b.columns:
            X[opt] = b[opt].astype("category").cat.codes
    y_bat = b.runs_batter.where(b.faced)
    y_conc = b.runs_conceded
    y_out = b.y_bat_out
    y_wkt = b.bowler_wicket.astype(int)
    all_rows = np.ones(len(b), bool)
    faced = b.faced.to_numpy()
    b["exp_bat_runs"], _ = lgb_oof(b, folds, X, y_bat, faced, "regression", cfg)
    b["exp_runs_conc"], _ = lgb_oof(b, folds, X, y_conc, all_rows, "regression", cfg)
    b["p_bat_out"], _ = lgb_oof(b, folds, X, y_out, faced, "binary", cfg)
    b["p_bowl_wkt"], _ = lgb_oof(b, folds, X, y_wkt, all_rows, "binary", cfg)
    b["y_bat_runs"], b["y_runs_conc"], b["y_bowl_wkt"] = y_bat, y_conc, y_wkt
    # CHECK: expected totals should match actual totals (a fair engine is right on average).
    diag["expectation_checks"] = {
        "batter_runs_expected_vs_actual": [float(b.exp_bat_runs.sum()), float(y_bat.sum())],
        "runs_conceded_expected_vs_actual": [float(b.exp_runs_conc.sum()), float(y_conc.sum())],
        "batter_dismissals_expected_vs_actual": [float(b.p_bat_out.sum()), float(y_out.sum())],
        "bowler_wickets_expected_vs_actual": [float(b.p_bowl_wkt.sum()), float(y_wkt.sum())]}
    log.info("Expectation engine checks (expected vs actual): %s", diag["expectation_checks"])
    return b


# ════════════════════════════════════════════════════════════════════════════════════════════════
# 5 · WIN PROBABILITY  →  LEVERAGE INDEX  →  PRESSURE BALLS
# ════════════════════════════════════════════════════════════════════════════════════════════════
def add_win_probability(b: pd.DataFrame, folds, cfg: Config, diag: dict) -> pd.DataFrame:
    """
    Win chance of the BATTING side before each ball, from the situation only.
    Chase: the target is known, so the key quantity is  z = (runs needed - runs a typical innings still adds) / spread.
           z = 0 is a coin flip, positive z = behind. 'runs a typical innings still adds' (E_rem) is learned from first innings
           (a Duckworth-Lewis-style "resources" idea, learned from your own data).
    First innings: nobody knows the winning total, so we use how the score compares with par, wickets and balls left.
    Model: logistic regression per innings. WHY NOT a big tree model? ~hundreds of matches, balls inside a match are correlated,
    and flexible models memorise match stories. A smooth model behaves sensibly in rare situations (60 needed off 6).
    """
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
    lgb = _lgb()
    groups = b.match_id.to_numpy()
    # E_rem: expected runs still to come in an innings, out-of-fold
    ok_rem = ((b.innings == 1) & b.match_id.map(b.groupby("match_id").clean_match.first())).to_numpy()
    rem_actual = b.t1 - b.score_before
    feats_rem = ["balls_left", "wkts_before", "venue_par"]
    b["e_rem"] = np.nan
    for tr, te in folds:
        tr = tr[ok_rem[tr]]
        mdl = lgb.train(dict(objective="regression", learning_rate=0.05, num_leaves=15, min_child_samples=max(50, min(400, len(tr) // 50)),
                             reg_lambda=10, n_jobs=-1, random_state=cfg.seed, verbosity=-1, monotone_constraints=[1, -1, 1]),
                        lgb.Dataset(b.iloc[tr][feats_rem], rem_actual.iloc[tr]), 300)
        b.iloc[te, b.columns.get_loc("e_rem")] = mdl.predict(b.iloc[te][feats_rem])
    b["e_rem"] = b.e_rem.fillna(b.e_rem.mean())
    b["z"] = (b.runs_needed - b.e_rem) / (2.8 * np.sqrt(b.balls_left.clip(lower=0) + 3))   # spread of what can still happen ~ 2.8 x sqrt(balls left)
    b["z_sq"], b["z_bl"], b["z_wk"] = b.z ** 2, b.z * b.balls_left / 120, b.z * b.wkts_before / 10
    b["wk_bl"] = b.wkts_before * b.balls_left / 120
    b["svp_bl"] = b.score_vs_par * (120 - b.balls_left) / 120
    b["svp_wk"] = b.score_vs_par * b.wkts_before
    FEATS = {1: ["score_vs_par", "wkts_before", "balls_left", "svp_bl", "svp_wk", "wk_bl"],
             2: ["z", "z_sq", "z_bl", "z_wk", "balls_left", "wkts_before"]}
    b["wp_before"] = np.nan
    metrics = {}
    for inn in (1, 2):
        train_rows = ((b.innings == inn) & b.clean_match).to_numpy()
        all_rows = (b.innings == inn).to_numpy()
        X = b[FEATS[inn]].fillna(0)
        for tr, te in folds:
            tr, te = tr[train_rows[tr]], te[all_rows[te]]
            if len(tr) < 200:
                b.iloc[te, b.columns.get_loc("wp_before")] = 0.5
                continue
            mu, sd = X.iloc[tr].mean(), X.iloc[tr].std().replace(0, 1)
            lr = LogisticRegression(max_iter=3000).fit((X.iloc[tr] - mu) / sd, b.bat_won.iloc[tr])
            b.iloc[te, b.columns.get_loc("wp_before")] = lr.predict_proba((X.iloc[te] - mu) / sd)[:, 1]
        r = train_rows & b.wp_before.notna().to_numpy()
        if r.sum() > 100 and b.bat_won[r].nunique() == 2:
            y, p = b.bat_won[r], np.clip(b.wp_before[r], 1e-6, 1 - 1e-6)
            metrics[f"innings_{inn}"] = dict(auc=float(roc_auc_score(y, p)), log_loss=float(log_loss(y, p)), brier=float(brier_score_loss(y, p)), balls=int(r.sum()))
    diag["win_probability_model"] = metrics
    cal = b[b.clean_match & b.wp_before.notna()].assign(band=lambda d: pd.cut(d.wp_before, np.linspace(0, 1, 11)))
    diag["win_probability_calibration"] = (cal.groupby(["innings", "band"], observed=True).agg(balls=("bat_won", "size"), predicted=("wp_before", "mean"),
                                                                                             actual=("bat_won", "mean")).round(3).reset_index()
                                           .assign(band=lambda d: d.band.astype(str)).to_dict("records"))
    log.info("Win-probability model: %s", metrics)
    return b


def add_leverage(b: pd.DataFrame, folds, cfg: Config, diag: dict) -> pd.DataFrame:
    """
    LEVERAGE INDEX (LI) = the EXPECTED size of the swing in win chance on the next ball, given only what is known before it.
    Step 1: swing = win chance after the ball - win chance before it. After = next ball's before (same innings); on the last ball of the
            chase it is the actual result (1 if the chasing side won). The last ball of innings 1 is left empty (the two innings use
            different models, so the gap at the break would be a fake spike).
    Step 2: train a model to predict |swing| from the situation (out-of-fold). Its prediction IS the expected swing.
    Step 3: divide by the average so the average ball = 1.00 ("LI 2.0" = twice as tense as normal). Borrowed from baseball analytics.
    """
    g = b.groupby(["match_id", "innings"])
    b["wp_after"] = g.wp_before.shift(-1)
    end2 = (b.innings == 2) & b.wp_after.isna()
    b.loc[end2, "wp_after"] = b.loc[end2, "bat_won"].where(b.loc[end2, "clean_match"])
    b["dwp"] = b.wp_after - b.wp_before
    ok = (b.clean_match & b.dwp.notna()).to_numpy()
    feats = ["innings", "over", "legal_before", "balls_left", "score_before", "wkts_before", "wp_before", "crr_s", "target", "runs_needed", "rrr", "z",
             "score_vs_par", "venue_par"]
    oof, best = lgb_oof(b, folds, b[feats], b.dwp.abs(), ok, "regression", cfg,
                        params=dict(learning_rate=0.1 if cfg.fast else 0.03, num_leaves=15, min_child_samples=400 if len(b) > 50000 else 100,
                                    colsample_bytree=0.8, reg_lambda=10.0), rounds=2000)
    # rows that were not trainable (e.g. matches without a winner) still need a leverage: predict them with a model fitted on all clean rows
    if oof.isna().any():
        lgb = _lgb()
        mdl = lgb.train(dict(objective="regression", learning_rate=0.05, num_leaves=15, min_child_samples=100, n_jobs=-1, random_state=cfg.seed, verbosity=-1),
                        lgb.Dataset(b.loc[ok, feats], b.dwp.abs()[ok]), int(np.median(best)))
        miss = oof.isna()
        oof[miss] = mdl.predict(b.loc[miss, feats])
    b["li_raw"] = oof.clip(lower=0)
    b["li"] = b.li_raw / b.li_raw.mean()
    d = pd.DataFrame({"li": b.li[ok], "real": b.dwp.abs()[ok]})
    diag["leverage"] = {"corr_predicted_vs_realised_abs_swing": float(d.li.corr(d.real)), "mean_abs_swing": float(d.real.mean()),
                        "li_percentiles": {str(p): float(b.li.quantile(p / 100)) for p in (50, 90, 99)}}
    return b


def add_pressure_flags(b: pd.DataFrame, cfg: Config, diag: dict) -> pd.DataFrame:
    """
    PRESSURE BALL = top q% of balls by leverage AND the player's own side is not cruising.
        batter pressure : win chance of batting side <= comfort (0.60)    - a batter already winning easily is not tested
        bowler pressure : win chance of batting side >= 1 - comfort (0.40) - a bowling side already winning easily is not tested
    We compute q = 15/20/25/30%. q = primary_q is the MAIN definition; the others test whether the list survives other reasonable choices.
    """
    thr = {q: float(b.li.quantile(1 - q)) for q in cfg.quantiles}
    for q in cfg.quantiles:
        k = int(round(q * 100))
        top = b.li >= thr[q]
        b[f"press_top{k}"] = top
        b[f"bat_press{k}"] = top & (b.wp_before <= cfg.comfort)
        b[f"bowl_press{k}"] = top & (b.wp_before >= 1 - cfg.comfort)
    pk = int(round(cfg.primary_q * 100))
    b["press_main"] = b[f"press_top{pk}"]
    b["bat_press"], b["bowl_press"] = b[f"bat_press{pk}"], b[f"bowl_press{pk}"]
    b["late_press"] = b.press_main & (b.over >= cfg.death_start)
    diag["pressure_thresholds_li"] = {f"top_{int(round(q*100))}pct": thr[q] for q in cfg.quantiles}
    diag["pressure_balls"] = {"all_balls": int(len(b)), "main_top_share": int(b.press_main.sum()), "for_batters": int(b.bat_press.sum()),
                              "for_bowlers": int(b.bowl_press.sum()), "share_in_chase": float(b.loc[b.press_main, "innings"].eq(2).mean())}
    log.info("Pressure balls: %s | LI thresholds: %s", diag["pressure_balls"], {k: round(v, 2) for k, v in diag["pressure_thresholds_li"].items()})
    return b


# ════════════════════════════════════════════════════════════════════════════════════════════════
# 6 · WICKET PRICES + NET RUNS  (runs and wickets in ONE currency)
# ════════════════════════════════════════════════════════════════════════════════════════════════
def wicket_price(d: pd.DataFrame, y_run, e_run, y_out, e_out, sign: int, n_boot: int, rng) -> tuple[float, tuple | None]:
    """
    How many runs is a wicket worth? Regress the swing in win chance (dwp) on 'runs above expected' and 'wickets above expected'.
    The ratio of the two coefficients converts a wicket into runs, on the scale of WIN PROBABILITY (so a wicket is worth a lot
    in the powerplay, little at the death).  sign=+1 batting view, -1 bowling view.
    """
    X = np.column_stack([np.ones(len(d)), (d[y_run] - d[e_run]).to_numpy(), (d[y_out] - d[e_out]).to_numpy()])
    y = (sign * d.dwp).to_numpy()

    def fit(idx):
        beta = np.linalg.lstsq(X[idx], y[idx], rcond=None)[0]
        return -beta[2] / beta[1] if sign == 1 else beta[2] / (-beta[1])
    est = float(fit(np.arange(len(d))))
    ci = None
    if n_boot > 0:
        m = d.match_id.to_numpy(); ids = np.unique(m); idx_by = {k: np.where(m == k)[0] for k in ids}
        boots = [fit(np.concatenate([idx_by[k] for k in rng.choice(ids, len(ids))])) for _ in range(n_boot)]
        ci = tuple(np.percentile(boots, [2.5, 97.5]))
    return est, ci


def add_net_runs(b: pd.DataFrame, cfg: Config, diag: dict) -> pd.DataFrame:
    rng = np.random.default_rng(cfg.seed)
    dbat = b[b.bat_press & b.faced & b.dwp.notna()]
    dbowl = b[b.bowl_press & b.dwp.notna()]
    overall_b, _ = wicket_price(dbat, "y_bat_runs", "exp_bat_runs", "y_bat_out", "p_bat_out", 1, 0, rng)
    overall_w, _ = wicket_price(dbowl, "y_runs_conc", "exp_runs_conc", "y_bowl_wkt", "p_bowl_wkt", -1, 0, rng)
    vb, vw, bands = {}, {}, {}
    for ph in ("powerplay", "middle", "death"):
        sb, sw = dbat[dbat.phase == ph], dbowl[dbowl.phase == ph]
        if len(sb) >= 300:
            vb[ph], cb = wicket_price(sb, "y_bat_runs", "exp_bat_runs", "y_bat_out", "p_bat_out", 1, cfg.wicket_boot, rng)
        else:
            vb[ph], cb = overall_b, None
        if len(sw) >= 300:
            vw[ph], cw = wicket_price(sw, "y_runs_conc", "exp_runs_conc", "y_bowl_wkt", "p_bowl_wkt", -1, cfg.wicket_boot, rng)
        else:
            vw[ph], cw = overall_w, None
        bands[ph] = {"batter_ci95": cb, "bowler_ci95": cw}
    # A price must be positive (a wicket cannot be worth negative runs). If the data is too thin the fit can go wrong: fall back and say so.
    for d_, name in ((vb, "batter"), (vw, "bowler")):
        for ph, v in d_.items():
            if not np.isfinite(v) or v <= 0:
                log.warning("Wicket price for %s/%s came out as %.2f; using the overall price instead.", name, ph, v)
                d_[ph] = overall_b if name == "batter" else overall_w
    diag["wicket_prices_runs"] = {"batter_by_phase": vb, "bowler_by_phase": vw, "batter_overall": overall_b, "bowler_overall": overall_w, "bands": bands}
    log.info("Wicket prices (runs). Batter: %s | Bowler: %s", {k: round(v, 1) for k, v in vb.items()}, {k: round(v, 1) for k, v in vw.items()})
    b["v_bat"], b["v_bowl"] = b.phase.map(vb), b.phase.map(vw)
    # NET RUNS ABOVE EXPECTED: runs and wickets together.  Positive = better than the situation expected.
    b["run_bat"] = (b.y_bat_runs - b.exp_bat_runs).where(b.faced)
    b["wkt_bat"] = (-b.v_bat * (b.y_bat_out - b.p_bat_out)).where(b.faced)
    b["run_bowl"] = b.exp_runs_conc - b.y_runs_conc
    b["wkt_bowl"] = b.v_bowl * (b.y_bowl_wkt - b.p_bowl_wkt)
    b["net_bat"], b["net_bowl"] = b.run_bat + b.wkt_bat, b.run_bowl + b.wkt_bowl
    diag["net_runs_league_mean_per_ball"] = {"bat": float(b.net_bat.mean()), "bowl": float(b.net_bowl.mean())}
    return b


# ════════════════════════════════════════════════════════════════════════════════════════════════
# 7 · RATING: EMPIRICAL-BAYES SHRINKAGE (honest uncertainty)
# ════════════════════════════════════════════════════════════════════════════════════════════════
def eb_shrink(m, se2):
    """
    Empirical Bayes: pull each player's raw average toward the group average, HARDER when his sample is small.
      m   : raw averages,  se2 : squared standard errors (noisier = bigger)
      tau2: the REAL spread of talent between players (what is left after removing sampling noise; DerSimonian-Laird estimate)
    A player with 40 pressure balls and a huge average is mostly luck; one with 300 balls is mostly real.
    """
    m, se2 = np.asarray(m, float), np.asarray(se2, float)
    w = 1 / se2
    mu_fe = (w * m).sum() / w.sum()
    Q = (w * (m - mu_fe) ** 2).sum()
    k = len(m)
    tau2 = max(0.0, (Q - (k - 1)) / (w.sum() - (w ** 2).sum() / w.sum()))
    ws = 1 / (se2 + tau2)
    mu0 = (ws * m).sum() / ws.sum()
    B = tau2 / (tau2 + se2)
    return mu0 + B * (m - mu0), B, tau2, mu0


def player_table(b: pd.DataFrame, side: str, flag: str, net: pd.Series, cfg: Config) -> pd.DataFrame:
    """
    One row per player: his average net runs on PRESSURE balls and on EVERYDAY balls (not a pressure ball at all), with the sample size
    of each and the pooled variance (needed to know how noisy each average is).
    """
    idc = "striker" if side == "bat" else "bowler"
    scale = cfg.scale_bat if side == "bat" else cfg.scale_bowl
    base = b.faced if side == "bat" else pd.Series(True, index=b.index)
    P, U = base & b[flag], base & ~b.press_main
    out = pd.DataFrame(index=sorted(b.loc[P, idc].unique()))
    pv = {}
    for k, mask in (("net", P), ("usual", U)):
        g = net[mask].groupby(b.loc[mask, idc])
        out[f"n_{k}"], out[f"m_{k}"] = g.size(), g.mean() * scale
        pv[k] = float(net[mask].var() * scale ** 2)
    out["n_usual"] = out.n_usual.fillna(0)
    out.attrs["pv"] = pv
    out["share_powerplay"] = b[P].groupby(idc).phase.apply(lambda s: (s == "powerplay").mean())
    out["share_late"] = b[P].groupby(idc).late_press.mean()
    out.index.name = "player"
    return out


def hier_posterior(t: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """
    Combine TWO records per player into one estimate of his PRESSURE level:
        everyday level  (many balls: precise)    and    pressure level (few balls: noisy).
    A bivariate-normal prior links them: pressure level = everyday level + a 'pressure shift' that varies between players.
    The posterior mean is the best guess of his true pressure level given both records; posterior sd is its uncertainty.
    WHY: a pressure record alone (~100 balls) is mostly luck, while the everyday record carries real information about skill.
    """
    pv_p, pv_u = t.attrs["pv"]["net"], t.attrs["pv"]["usual"]
    ok = (t.n_net >= cfg.min_pool) & (t.n_usual >= cfg.min_pool)
    d = t[ok]
    out = pd.DataFrame(index=d.index, columns=["post_mean", "post_sd", "post_u"], dtype=float)
    if len(d) < 10:
        return out
    se_p2, se_u2 = pv_p / d.n_net, pv_u / d.n_usual
    _, _, tau_u2, mu_u = eb_shrink(d.m_usual, se_u2)                       # spread of everyday talent
    _, _, tau_d2, mu_d = eb_shrink(d.m_net - d.m_usual, se_p2 + se_u2)       # spread of the pressure shift
    m0 = np.array([mu_u, mu_u + mu_d])
    S0 = np.array([[tau_u2, tau_u2], [tau_u2, tau_u2 + tau_d2]])
    for pid, r in d.iterrows():
        R = np.diag([max(pv_u / r.n_usual, 1e-9), max(pv_p / r.n_net, 1e-9)])
        y = np.array([r.m_usual, r.m_net])
        K = S0 @ np.linalg.inv(S0 + R)
        mean = m0 + K @ (y - m0)
        cov = S0 - K @ S0
        out.loc[pid] = [mean[1], np.sqrt(max(cov[1, 1], 0)), mean[0]]
    out.attrs.update(tau_u=float(np.sqrt(tau_u2)), tau_d=float(np.sqrt(tau_d2)))
    return out


def two_part_rating(b: pd.DataFrame, side: str, flag: str, cfg: Config) -> pd.DataFrame:
    """
    The rating is built from TWO parts, each with its own reliability (runs are plentiful, wickets are rare and noisy):
        rating = run part (runs above expected) + wicket part (wickets above expected x the phase wicket price).
    """
    run_net = b.run_bat if side == "bat" else b.run_bowl
    wkt_net = b.wkt_bat if side == "bat" else b.wkt_bowl
    tr, tw = player_table(b, side, flag, run_net, cfg), player_table(b, side, flag, wkt_net, cfg)
    hr, hw = hier_posterior(tr, cfg), hier_posterior(tw, cfg)
    j = hr.join(hw, lsuffix="_run", rsuffix="_wkt", how="inner")
    out = tr[["n_net", "n_usual", "share_powerplay", "share_late"]].copy()
    out = out.join(pd.DataFrame({"post_mean": j.post_mean_run + j.post_mean_wkt, "post_sd": np.sqrt(j.post_sd_run ** 2 + j.post_sd_wkt ** 2),
                                 "post_u": j.post_u_run + j.post_u_wkt, "part_run": j.post_mean_run, "part_wkt": j.post_mean_wkt}), how="left")
    out["raw_run"], out["raw_wkt"] = tr.m_net, tw.m_net
    out["rated"] = (out.n_net >= cfg.min_pressure_balls) & out.post_mean.notna()
    out.attrs["tau"] = {"run": hr.attrs, "wkt": hw.attrs}
    return out


def simulate_top_k(mean: np.ndarray, sd: np.ndarray, cfg: Config, rng) -> dict:
    """
    MONTE CARLO. Each player's TRUE rating is uncertain (bell curve: mean = shrunk rating, spread = posterior sd).
    Draw a possible world n_sims times, rank everyone in it, and count how often each player lands in the top 10.
    That share IS his 'chance of being a true top 10'.
    """
    draws = rng.normal(mean[:, None], sd[:, None], size=(len(mean), cfg.n_sims))
    rank = (-draws).argsort(axis=0).argsort(axis=0) + 1
    return {"p_top10": (rank <= 10).mean(axis=1), "p_top20": (rank <= 20).mean(axis=1), "exp_rank": rank.mean(axis=1)}


def build_ratings(b: pd.DataFrame, cfg: Config, diag: dict, excluded: dict | None = None) -> dict[str, pd.DataFrame]:
    """
    Rate every player on every cutoff, then apply the selection rule.
    `excluded` = {player: reason} for players who must not appear on a list (retired, unavailable, wrong nationality for your purpose...).
    They are still used to build the models, but are removed BEFORE ranking, so the chances of a top-10 place are computed among the
    players who can actually be selected.
    """
    excluded = {str(k): v for k, v in (excluded or {}).items()}
    rng = np.random.default_rng(cfg.seed)
    pk = int(round(cfg.primary_q * 100))
    results = {}
    for side in ("bat", "bowl"):
        main = two_part_rating(b, side, f"{side}_press{pk}", cfg)
        r = main[main.rated & ~main.index.astype(str).isin(excluded)].copy()
        if len(r) < 10:
            log.warning("Only %d %s players reach the %d-ball rating floor: lower --min-pressure-balls or supply more matches.",
                        len(r), side, cfg.min_pressure_balls)
        r["rank"] = r.post_mean.rank(ascending=False, method="min").astype(int)
        r["low80"], r["high80"] = r.post_mean - 1.2816 * r.post_sd, r.post_mean + 1.2816 * r.post_sd
        sim = simulate_top_k(r.post_mean.to_numpy(), r.post_sd.to_numpy(), cfg, rng)
        r["p_top10"], r["p_top20"], r["exp_rank"] = sim["p_top10"], sim["p_top20"], sim["exp_rank"]
        r["tier"] = pd.cut(r.p_top10, [-1, 0.30, 0.60, 2], labels=["C", "B", "A"]).astype(str)
        # robustness: rank under each cutoff (rank among players rated at THAT cutoff)
        for q in cfg.quantiles:
            k = int(round(q * 100))
            alt = two_part_rating(b, side, f"{side}_press{k}", cfg)
            alt = alt[alt.rated & ~alt.index.astype(str).isin(excluded)]
            r[f"rank_top{k}"] = alt.post_mean.rank(ascending=False, method="min").reindex(r.index)
        rank_cols = [f"rank_top{int(round(q*100))}" for q in cfg.quantiles]
        r["top10_of4"] = (r[rank_cols] <= 10).sum(axis=1)
        # SELECTION RULE: a pick must be top 10 under at least `pass_min_cutoffs` cutoffs, so the list does not hinge on one arbitrary line.
        passes = r.top10_of4 >= cfg.pass_min_cutoffs
        r = r.sort_values("rank")
        picks = r.index[passes.reindex(r.index)][: cfg.n_picks]
        r["status"] = "watchlist"
        r.loc[r.index.isin(picks), "status"] = "PICK"
        res = passes.reindex(r.index) & ~r.index.isin(picks) & (r["rank"] <= cfg.reserve_rank)
        r.loc[res, "status"] = "reserve"
        r["list_position"] = np.nan
        r.loc[picks, "list_position"] = range(1, len(picks) + 1)
        r.index.name = "player"
        results[side] = r
        diag[f"rating_summary_{side}"] = {
            "players_rated": int(len(r)), "median_pressure_balls": float(r.n_net.median()), "median_posterior_sd": float(r.post_sd.median()),
            "picks_top10_under_all_cutoffs": int(((r.status == "PICK") & (r.top10_of4 == len(cfg.quantiles))).sum()),
            "talent_spread": {k: {kk: float(vv) for kk, vv in v.items()} for k, v in main.attrs["tau"].items()}}
        log.info("%s: %d rated; picks=%d; all-four-cutoff picks=%d", side, len(r), int((r.status == "PICK").sum()), diag[f"rating_summary_{side}"]["picks_top10_under_all_cutoffs"])
    return results


# ════════════════════════════════════════════════════════════════════════════════════════════════
# 8 · OUTPUT
# ════════════════════════════════════════════════════════════════════════════════════════════════
def tidy(r: pd.DataFrame, side: str, cfg: Config) -> pd.DataFrame:
    unit = f"per {cfg.scale_bat if side == 'bat' else cfg.scale_bowl} pressure balls"
    cols = {"list_position": "list_position", "status": "status", "rank": "rank", "n_net": "pressure_balls", "post_mean": f"rating ({unit})",
            "low80": "low80", "high80": "high80", "p_top10": "chance_true_top10", "tier": "tier", "top10_of4": "top10_in_4_cutoffs",
            "part_run": "from_runs", "part_wkt": "from_wickets", "post_u": "everyday_level", "share_powerplay": "share_powerplay_balls",
            "share_late": "share_death_balls"}
    cols.update({f"rank_top{int(round(q*100))}": f"rank_at_top{int(round(q*100))}pct" for q in cfg.quantiles})
    out = r[list(cols)].rename(columns=cols).reset_index()
    for c in out.columns:
        if out[c].dtype.kind == "f":
            out[c] = out[c].round(3)
    return out


def write_outputs(results: dict, cfg: Config, diag: dict, out_dir: Path, balls: pd.DataFrame | None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    tabs = {"batters": tidy(results["bat"], "bat", cfg), "bowlers": tidy(results["bowl"], "bowl", cfg)}
    for name, t in tabs.items():
        t.to_csv(out_dir / f"ratings_{name}.csv", index=False)
    try:
        with pd.ExcelWriter(out_dir / "shortlist.xlsx") as xw:
            for name, t in tabs.items():
                t.to_excel(xw, sheet_name=name.title(), index=False)
    except Exception as e:                                           # Excel output is a convenience, never a reason to fail the run
        log.warning("Excel file not written (%s). The CSV files are complete.", e)
    (out_dir / "diagnostics.json").write_text(json.dumps(diag, indent=2, default=lambda o: float(o) if isinstance(o, (np.floating, np.integer)) else str(o)))
    (out_dir / "run_config.json").write_text(json.dumps({"version": __version__, **asdict(cfg)}, indent=2, default=str))
    if balls is not None:
        keep = ["match_id", "innings", "over", "striker", "bowler", "phase", "wp_before", "li", "press_main", "bat_press", "bowl_press",
                "exp_bat_runs", "exp_runs_conc", "p_bat_out", "p_bowl_wkt", "net_bat", "net_bowl"]
        try:
            balls[keep].to_parquet(out_dir / "ball_level_results.parquet", index=False)
        except Exception:
            balls[keep].to_csv(out_dir / "ball_level_results.csv.gz", index=False)
    log.info("Wrote results to %s", out_dir.resolve())


def print_summary(results: dict, cfg: Config) -> None:
    for side, label, unit in (("bat", "BATTERS", cfg.scale_bat), ("bowl", "BOWLERS", cfg.scale_bowl)):
        r = results[side]
        top = r[r.status == "PICK"]
        print(f"\n{label}  (rating = runs above expected per {unit} pressure balls; interval = 80%)")
        print(f"{'#':>2}  {'player':<28}{'balls':>6}{'rating':>8}{'  80% interval':>18}{'top10':>7}{'tier':>5}{'cutoffs':>9}")
        for pid, x in top.iterrows():
            print(f"{int(x.list_position):>2}  {str(pid)[:27]:<28}{int(x.n_net):>6}{x.post_mean:>8.1f}{f'  {x.low80:+.1f} to {x.high80:+.1f}':>18}{x.p_top10:>6.0%}{x.tier:>5}{int(x.top10_of4):>7}/4")
        res = r[r.status == "reserve"]
        if len(res):
            print("   reserve:", ", ".join(map(str, res.index)))


# ════════════════════════════════════════════════════════════════════════════════════════════════
# 9 · PIPELINE
# ════════════════════════════════════════════════════════════════════════════════════════════════
def run_pipeline(raw: pd.DataFrame, cfg: Config, over_base: int = 1, excluded: dict | None = None) -> tuple[dict, dict, pd.DataFrame]:
    t0 = time.time()
    diag: dict = {"version": __version__, "config": asdict(cfg)}
    b, audit = prepare_balls(raw, cfg, over_base)
    diag["data_audit"] = audit
    if audit["matches_clean_for_win_model"] == 0:
        raise SystemExit("No match has both innings, a recorded winner and a full first innings. Check that match_winner uses the SAME labels "
                         "as batting_team / bowling_team (for example both team names, or both team ids).")
    if audit["matches_clean_for_win_model"] < 30:
        log.warning("Only %d complete, decided matches are available to learn win probability. Results will be unstable.", audit["matches_clean_for_win_model"])
    b = add_match_state(b, cfg)
    b = add_ground_par(b, cfg)
    folds = make_folds(b, cfg)
    log.info("%d balls, %d matches, %d folds. Training expectation engine...", len(b), b.match_id.nunique(), len(folds))
    b = add_expectation(b, folds, cfg, diag)
    b = add_win_probability(b, folds, cfg, diag)
    b = add_leverage(b, folds, cfg, diag)
    b = add_pressure_flags(b, cfg, diag)
    b = add_net_runs(b, cfg, diag)
    results = build_ratings(b, cfg, diag, excluded)
    diag["excluded_players"] = excluded or {}
    diag["runtime_seconds"] = round(time.time() - t0, 1)
    return results, diag, b


# ════════════════════════════════════════════════════════════════════════════════════════════════
# 10 · SELF-TEST: a small synthetic league so the whole pipeline can be checked without any data
# ════════════════════════════════════════════════════════════════════════════════════════════════
def make_synthetic(n_matches: int = 220, seed: int = 7) -> tuple[pd.DataFrame, pd.Series]:
    """
    A toy T20 league with KNOWN planted batter skill, so the self-test can check that ratings recover it.
    Not realistic cricket: it only needs to be good enough to exercise every step.
    """
    rng = np.random.default_rng(seed)
    teams = [f"T{i}" for i in range(8)]
    squad = {t: [f"{t}_P{j}" for j in range(11)] for t in teams}
    skill = {p: rng.normal(0, 1) for t in teams for p in squad[t]}
    rows = []
    for m in range(n_matches):
        t_a, t_b = rng.choice(teams, 2, replace=False)
        venue, season = f"V{m % 6}", 2020 + m % 4
        totals = []
        for inn, (bat, bowl) in enumerate([(t_a, t_b), (t_b, t_a)], 1):
            score, wk, legal, over_no, ball_in_over = 0, 0, 0, 1, 0
            target = totals[0] + 1 if inn == 2 else None
            striker_i, non_i = 0, 1
            nxt = 2
            while legal < 120 and wk < 10:
                bt = squad[bat][striker_i]
                bw = squad[bowl][5 + (over_no % 5)]
                stake = 1.0 + (0.5 if (over_no >= 16 or (target and target - score < 40)) else 0)
                p_out = np.clip(0.045 + 0.01 * (over_no > 15) - 0.01 * skill[bt] + 0.008 * skill[bw], 0.01, 0.2)
                mean_r = max(0.3, 1.2 + 0.25 * skill[bt] - 0.1 * skill[bw] + 0.015 * over_no) * stake
                wide = rng.random() < 0.04
                if wide:
                    rows.append((m, inn, over_no, legal * 1 + 1, bat, bowl, bt, bw, 0, 1, "wide", 0, None, None, t_a, venue, season))
                    score += 1
                else:
                    out = rng.random() < p_out
                    runs = 0 if out else int(rng.choice([0, 1, 2, 3, 4, 6], p=_probs(mean_r)))
                    legal += 1
                    score += runs
                    rows.append((m, inn, over_no, legal, bat, bowl, bt, bw, runs, 0, "", int(out), bt if out else None, "caught" if out else None, t_a, venue, season))
                    if out:
                        wk += 1
                        striker_i = nxt if nxt < 11 else striker_i
                        nxt += 1
                    elif runs % 2 == 1:
                        striker_i, non_i = non_i, striker_i
                    if legal % 6 == 0:
                        over_no += 1
                        striker_i, non_i = non_i, striker_i
                if target and score >= target:
                    break
            totals.append(score)
        winner = t_a if totals[0] > totals[1] else t_b
        for i in range(len(rows) - 1, -1, -1):
            if rows[i][0] != m:
                break
            rows[i] = rows[i][:14] + (winner,) + rows[i][15:]
    cols = ["match_id", "innings", "over", "ball_seq", "batting_team", "bowling_team", "striker", "bowler", "runs_batter", "extras_runs",
            "extras_type", "is_wicket", "dismissed_player", "dismissal_type", "match_winner", "venue", "season"]
    df = pd.DataFrame(rows, columns=cols)
    df["ball_seq"] = df.groupby(["match_id", "innings"]).cumcount() + 1
    return df, pd.Series(skill)


def _probs(mean_r: float) -> np.ndarray:
    base = np.array([.34, .34, .08, .02, .14, .08])
    vals = np.array([0, 1, 2, 3, 4, 6])
    tilt = np.exp(0.35 * (mean_r - 1.4) * (vals - (base * vals).sum()))
    p = base * tilt
    return p / p.sum()


def selftest() -> int:
    log.info("SELF-TEST: building a synthetic league and running the full pipeline...")
    df, skill = make_synthetic()
    cfg = Config(fast=True, n_sims=1000, min_pressure_balls=25, min_pool=15)
    results, diag, _ = run_pipeline(df, cfg)
    ok = True
    r = results["bat"]
    rho = pd.concat([r.post_mean, skill.reindex(r.index)], axis=1).corr(method="spearman").iloc[0, 1]
    log.info("Batters rated: %d | rank correlation of rating with PLANTED skill: %.2f", len(r), rho)
    ok &= len(r) >= 10 and rho > 0.3
    ok &= diag["pressure_balls"]["main_top_share"] > 0
    ok &= abs(diag["net_runs_league_mean_per_ball"]["bat"]) < 0.05
    ok &= (results["bat"].status == "PICK").sum() >= 1
    print("\nSELF-TEST", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


# ════════════════════════════════════════════════════════════════════════════════════════════════
# 11 · COMMAND LINE
# ════════════════════════════════════════════════════════════════════════════════════════════════
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Pressure Rating: who performs best under pressure? Runs on any ball-by-ball T20 dataset.",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--input", type=Path, help="ball-by-ball file (.csv / .parquet / .xlsx)")
    ap.add_argument("--out", type=Path, default=Path("pressure_results"), help="output folder")
    ap.add_argument("--map", nargs="*", default=[], metavar="KEY=COLUMN", help="map your column names, e.g. --map striker=batter_name over=over_no")
    ap.add_argument("--over-base", type=int, default=1, choices=[0, 1], help="1 if overs are numbered 1..20, 0 if 0..19")
    ap.add_argument("--primary-q", type=float, default=0.20, help="main pressure definition: top share of balls by leverage")
    ap.add_argument("--min-pressure-balls", type=int, default=60, help="rating floor (pressure balls a player needs to be rated)")
    ap.add_argument("--picks", type=int, default=10, help="length of each final list")
    ap.add_argument("--max-overs", type=int, default=20)
    ap.add_argument("--exclude", type=Path, help="optional CSV with columns player,reason: players to keep OFF the lists (retired, unavailable...)")
    ap.add_argument("--fast", action="store_true", help="quick-look mode (fewer folds and trees). Not for final results.")
    ap.add_argument("--save-balls", action="store_true", help="also save the ball-level results (large)")
    ap.add_argument("--template", action="store_true", help="write template.csv with the expected columns and exit")
    ap.add_argument("--selftest", action="store_true", help="run on synthetic data and exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S",
                        stream=sys.stdout)
    if a.template:
        cols = [k for k in SCHEMA]
        pd.DataFrame(columns=cols).to_csv("template.csv", index=False)
        print("Wrote template.csv. Required columns:", [k for k, v in SCHEMA.items() if v["req"]])
        print("Optional columns (improve accuracy or add detail):", [k for k, v in SCHEMA.items() if not v["req"]])
        return 0
    if a.selftest:
        return selftest()
    if a.input is None:
        ap.error("--input is required (or use --template / --selftest)")
    user_map = dict(kv.split("=", 1) for kv in a.map)
    cfg = Config(primary_q=a.primary_q, min_pressure_balls=a.min_pressure_balls, n_picks=a.picks, max_overs=a.max_overs, fast=a.fast)
    if cfg.primary_q not in cfg.quantiles:
        cfg.quantiles = tuple(sorted(set(cfg.quantiles) | {cfg.primary_q}))
    a.out.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(a.out / "run.log", mode="w")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(fh)
    raw = standardise_columns(load_table(a.input), user_map)
    excluded = None
    if a.exclude:
        ex = pd.read_csv(a.exclude, dtype=str)
        excluded = dict(zip(ex.iloc[:, 0].str.strip(), ex.iloc[:, 1] if ex.shape[1] > 1 else [""] * len(ex)))
    results, diag, balls = run_pipeline(raw, cfg, a.over_base, excluded)
    write_outputs(results, cfg, diag, a.out, balls if a.save_balls else None)
    print_summary(results, cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
