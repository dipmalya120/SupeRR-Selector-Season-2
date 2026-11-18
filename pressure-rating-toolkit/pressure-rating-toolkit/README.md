# 🛠️ Pressure Rating Toolkit

**One Python file that rates players under pressure on any ball-by-ball T20 dataset.**
Use it on the IPL, a domestic tournament, an uncapped-player league or a women's league. It runs the full method from the main project, end to end.

> Method in one line: measure how much each ball can swing the match (leverage), keep the top 20% of balls where the player's side is not cruising, compare what happened with what a typical player would do there, and shrink the result for small samples.
> The full story, results and dashboard are in the [main README](../README.md).

---

## ⚡ Quick start

```bash
pip install -r requirements.txt

python pressure_rating.py --template                   # writes template.csv: the columns your data needs
python pressure_rating.py --selftest                   # checks everything works (about 15 seconds, no data needed)
python pressure_rating.py --input my_balls.csv --out results/
```

On the full IPL dataset (about 200,000 balls) a run takes about a minute.

---

## 📥 What your data must look like

One row per delivery, in one file (`.csv`, `.parquet` or `.xlsx`).

| Column | Required | Meaning |
|---|:-:|---|
| `match_id` | ✅ | one value per match |
| `innings` | ✅ | 1 or 2 (super overs, innings 3 and above, are dropped) |
| `over` | ✅ | over number; use `--over-base 0` if numbered 0 to 19 |
| `batting_team`, `bowling_team` | ✅ | team names or ids |
| `striker`, `bowler` | ✅ | player names or ids (ids are safer: names can clash) |
| `runs_batter` | ✅ | runs off the bat on this ball |
| `extras_runs` | ✅ | extra runs on this ball (0 if none) |
| `is_wicket` | ✅ | 1 if a wicket fell on this ball |
| `match_winner` | ✅ | winning team, **using the same labels as the team columns** |
| `ball_seq` | recommended | order of the ball inside the innings (otherwise file order is used) |
| `extras_type` | recommended | wide / noball / byes / legbyes / penalty |
| `dismissed_player`, `dismissal_type` | recommended | so run-outs are not charged to the bowler |
| `venue`, `season` | optional | ground par and the "scores rise over time" trend |
| `competition` | optional | needed if one file mixes leagues (for example IPL and domestic) |
| `bowler_type`, `batter_hand` | optional | pace or spin; left or right (small accuracy gain) |

Column names are recognised automatically for most common exports. If yours differ, map them:

```bash
python pressure_rating.py --input my_balls.csv --map striker=batter_name bowler=bowler_name over=over_no
```

---

## 🎛️ Useful options

| Option | What it does |
|---|---|
| `--out DIR` | output folder (default `pressure_results`) |
| `--primary-q 0.20` | main pressure definition: top share of balls by leverage |
| `--min-pressure-balls 60` | pressure balls a player needs to be rated. **Lower it for small datasets** |
| `--picks 10` | length of each final list |
| `--exclude file.csv` | CSV with columns `player,reason`: keeps retired or unavailable players off the lists |
| `--over-base 0` | if your overs are numbered 0 to 19 |
| `--fast` | quick look (fewer folds and trees). Not for final results |
| `--save-balls` | also saves the ball-level results (large) |

---

## 📤 What you get

| File | Contents |
|---|---|
| `ratings_batters.csv`, `ratings_bowlers.csv` | rating, 80% interval, chance of a true top 10, tier A/B/C, rank at each of the four pressure cut-offs, split of rating into runs and wickets, status (`PICK`, `reserve`, `watchlist`) |
| `shortlist.xlsx` | the same two tables in Excel (needs `openpyxl`) |
| `diagnostics.json` | model quality, pressure thresholds, wicket prices by phase, data-cleaning audit, sanity checks |
| `run_config.json`, `run.log` | the exact settings and a log, so a run can be reproduced |

**How to read a rating.** It is the runs a player adds above what the situation expected on his pressure balls, per 100 balls (batters) or per 24 balls (bowlers). Tier A means a more than 60% chance of being a true top 10, B is 30 to 60%, C is below 30%. A `PICK` is top 10 under at least 2 of the 4 pressure cut-offs.

---

## 🧭 Using it on uncapped or domestic cricket

- **Retrain, do not reuse.** The script fits every model on the data you give it, so nothing from the IPL is carried over. That is intended: scoring patterns and wicket prices differ by league.
- **Expect wider intervals.** Fewer matches mean fewer pressure balls per player, more shrinkage and mostly Tier C names. That is the method being honest.
- **Compare within a league only.** A rating means "better than a typical player *in this dataset*". It is not on the same scale as an IPL rating, because opposition strength differs.
- **If one file mixes leagues,** add a `competition` column so each league is compared with its own scoring level.
- **Missing columns are fine.** Without pace or spin, or dismissal type, the core rating still works; the script warns you about what is approximate.

---

## ✅ How it was checked

- **Self-test.** On a synthetic league with known planted skill, ratings recover the true order (rank correlation about 0.9).
- **IPL reproduction.** On the project's own 200,961-ball dataset it finds exactly **40,193 pressure balls**, a chase win-probability score of 0.899 against the report's 0.904, and wicket prices close to the report's. It agrees with the published lists on **8 of 10 batters and 8 of 10 bowlers**, with a rating rank correlation of about 0.84 (batters) and 0.94 (bowlers).
- **Why not identical.** The project notebooks also use team strength and tournament stakes, and apply nationality and availability checks. Use `--exclude` for the last two. Treat the toolkit as the same method in a portable form, and the notebooks as the reference.

---

## 🧩 What happens inside

1. Load and validate the columns; drop super overs; log every repair.
2. Build match state before each ball: score, wickets, balls left, target, required rate.
3. Estimate ground par from other matches only, shrunk when a ground has few matches.
4. Learn what a typical player does on each ball (runs and wicket chance), with no player identity, using grouped cross-validation so no ball is scored by a model that saw its match.
5. Estimate win probability ball by ball, then the **leverage index** (expected swing on the next ball, average ball = 1.00).
6. Flag **pressure balls** at four cut-offs (top 15, 20, 25, 30%).
7. Price a wicket in runs, by phase, from the data.
8. Combine runs and wickets into **net runs above expected**.
9. Shrink each player toward the group average, using both his pressure and everyday records.
10. Simulate 4,000 re-draws for the 80% interval and the chance of a true top 10, then apply the 2-of-4 rule.

---

## ⚠️ Limits

- Built for **T20** (20 overs). Other formats need changes beyond `--max-overs`.
- Needs enough **complete, decided matches** to learn win probability (a few dozen at least; hundreds is better). The script warns when there are too few.
- Ratings are **descriptive**, not forecasts: they say who has handled pressure best in the data supplied.

## 🆘 Troubleshooting

| Message | Fix |
|---|---|
| `Required columns not found` | run `--template`, or use `--map key=your_column` |
| `No match has both innings, a recorded winner...` | make `match_winner` use the same labels as `batting_team` and `bowling_team` |
| `Only N players reach the rating floor` | lower `--min-pressure-balls`, or add more matches |
| `No ball-order column found` | add `ball_seq`, or make sure the file is already in playing order |
