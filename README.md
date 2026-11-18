<div align="center">

# 🏏 SupeRR Selector · Season 2

### Who performs best under pressure?
**A ball-by-ball study of 200,000 IPL deliveries to find the top 10 batters and top 10 bowlers when the match is on the line.**

[![Live dashboard](https://img.shields.io/badge/▶_Live_dashboard-pinkcity--pressure--lab-EA1A85?style=for-the-badge)](https://pinkcity-pressure-lab.vercel.app/)
[![Summary notebook](https://img.shields.io/badge/Notebook-summary_of_findings-254AA5?style=for-the-badge&logo=jupyter&logoColor=white)](notebooks/SupeRR_Selector_Summary.ipynb)
[![Methodology](https://img.shields.io/badge/PDF-methodology_report-1B2340?style=for-the-badge)](docs/)

*Rajasthan Royals SupeRR Selector, Season 2 (Unstop) · Round 1*

</div>

---

## 👀 Start here

| If you want to… | Go to |
|---|---|
| **Explore the results interactively** (rankings, head-to-head, simulations, Royals gaps) | 👉 **[pinkcity-pressure-lab.vercel.app](https://pinkcity-pressure-lab.vercel.app/)** |
| **Read the story in charts** (descriptive, diagnostic, predictive, prescriptive) | [`notebooks/SupeRR_Selector_Summary.ipynb`](notebooks/SupeRR_Selector_Summary.ipynb) |
| **Read the full method, assumptions and checks** | [`docs/`](docs/) (methodology PDF) |
| **Reproduce the numbers from raw data** | [How to reproduce](#-how-to-reproduce) |
| **Run the method on your own data** (IPL, domestic, uncapped players) | [`pressure-rating-toolkit/`](pressure-rating-toolkit/) |

---

## 🎯 The question

Cricket averages treat every ball the same. A single off the first ball of the innings and a single off the last ball of a tight chase carry the same weight in a strike rate, but they are not the same moment. Round 1 asks a sharper question: **who delivers when it matters most?**

## 💡 The idea in one minute

1. **Leverage.** For every ball, estimate how far it can swing the chance of winning. The average ball is scaled to 1.00; a last-over ball with the game level can be worth 10 to 20 times that.
2. **Pressure ball.** The top 20% of balls by leverage *and* the player's own side is not cruising (batting side's win chance of 60% or less; for the bowler, 40% or more). If the game is already decided, nobody is being tested.
3. **Rating.** On each pressure ball, compare what happened with what a typical player would produce there (runs and wickets, with each wicket priced by phase). The surplus per 100 balls (batters) or per 24 balls (bowlers) is the **pressure rating**.
4. **Honest uncertainty.** Ratings are shrunk toward the average for small samples. Every player gets an 80% interval and a **chance of being a true top 10** from 4,000 simulated re-draws.
5. **Robust selection.** A player must be top 10 under **at least 2 of 4** definitions of pressure (top 15, 20, 25 and 30% of balls), so the list does not hinge on one arbitrary cut-off.

## 🏆 The results

### Top 10 batters
*Rating = runs above par per 100 pressure balls.*

| # | Player | Team | Rating | 80% interval | Chance of true top 10 | Tier | Top 10 in |
|:-:|---|:-:|:-:|:-:|:-:|:-:|:-:|
| 1 | **Vaibhav Sooryavanshi** | RR | +41.8 | +26.1 to +57.5 | 95% | A | 4/4 |
| 2 | **Tim David** | RCB | +28.5 | +14.3 to +42.7 | 76% | A | 4/4 |
| 3 | **Suryakumar Yadav** | MI | +28.3 | +16.3 to +40.4 | 78% | A | 4/4 |
| 4 | **Rajat Patidar** | RCB | +21.4 | +5.5 to +37.3 | 51% | B | 3/4 |
| 5 | **Phil Salt** | RCB | +20.8 | +7.1 to +34.5 | 50% | B | 4/4 |
| 6 | **Heinrich Klaasen** | SRH | +18.8 | +4.2 to +33.4 | 44% | B | 4/4 |
| 7 | **Priyansh Arya** | PBKS | +18.0 | +1.4 to +34.7 | 41% | B | 3/4 |
| 8 | **Nicholas Pooran** | LSG | +15.9 | +3.1 to +28.7 | 31% | B | 2/4 |
| 9 | **Shivam Dube** | CSK | +14.7 | +0.3 to +29.1 | 28% | C | 2/4 |
| 10 | **Abhishek Sharma** | SRH | +14.4 | +2.1 to +26.6 | 25% | C | 3/4 |

Reserve: **Jos Buttler**.

### Top 10 bowlers
*Rating = runs saved against par per 24 pressure balls.*

| # | Player | Team | Rating | 80% interval | Chance of true top 10 | Tier | Top 10 in |
|:-:|---|:-:|:-:|:-:|:-:|:-:|:-:|
| 1 | **Jasprit Bumrah** | MI | +5.8 | +4.0 to +7.6 | 98% | A | 4/4 |
| 2 | **Sunil Narine** | KKR | +4.1 | +2.3 to +6.0 | 80% | A | 4/4 |
| 3 | **Nathan Ellis** | CSK | +3.0 | +1.0 to +5.0 | 54% | B | 4/4 |
| 4 | **Matheesha Pathirana** | KKR | +2.9 | +1.0 to +4.8 | 52% | B | 4/4 |
| 5 | **Bhuvneshwar Kumar** | RCB | +2.7 | +1.0 to +4.4 | 43% | B | 4/4 |
| 6 | **Rashid Khan** | GT | +2.6 | +1.0 to +4.3 | 42% | B | 3/4 |
| 7 | **Mustafizur Rahman** | KKR | +2.4 | +0.6 to +4.3 | 37% | B | 3/4 |
| 8 | **Eshan Malinga** | SRH | +2.4 | +0.4 to +4.5 | 40% | B | 3/4 |
| 9 | **Blessing Muzarabani** | KKR | +2.4 | +0.3 to +4.5 | 39% | B | 4/4 |
| 10 | **Varun Chakravarthy** | KKR | +1.7 | 0.0 to +3.5 | 20% | C | 3/4 |

> **How to read the tables.** Tier A: more than a 60% chance of being a true top 10. Tier B: 30 to 60%. Tier C: below 30%. Order inside Tiers B and C is soft because pressure balls are a small sample; the intervals show that openly. **Top 10 in** counts how many of the four pressure cut-offs put the player in the top 10; a final pick needs at least two.

## 🔍 What we found

- **Pressure is concentrated.** The top 20% of balls carry **42.8%** of all win-chance movement, 2.1× their fair share. About **93%** of pressure balls occur in the chase.
- **A wicket is not a wicket.** An early wicket costs a batting side about **4×** a death-over wicket, so wickets are priced by phase, not flat.
- **Two tempting ideas failed and were dropped.** Match stakes do not change how often pressure arises, and death-over bowling variety shows no relationship with runs saved. Neither is used to rank anyone.
- **The lists are robust.** 5 batters and 6 bowlers are top 10 under all four pressure definitions; every other pick passes at least two.
- **Rajasthan Royals' clearest pressure gap is middle-overs batting** (about −5.5 runs per 100 balls, worst against pace), with a smaller gap in middle-overs bowling. The dashboard and notebook show which picks do their pressure work in those overs.

## 🧭 Explore the dashboard

The **[live dashboard](https://pinkcity-pressure-lab.vercel.app/)** is a single interactive page with nine tabs:

| Tab | What you can do |
|---|---|
| **Overview** | See the headline numbers and a flashcard tour of the picks |
| **Pressure framework** | Follow how a ball becomes a pressure ball, step by step |
| **Picks** | Compare players head to head, filter the tables and download the Top 30 as Excel |
| **Explorer** | Change the pressure cut-off and run the simulation to see how rankings wobble |
| **Stress tests** | Check the lists against alternative definitions and delivery types |
| **Matchups** | Replay famous chases over by over, including Royals games |
| **Insights** | Descriptive findings across phases, pitches, eras and bowler types |
| **RR gaps** | See where Royals sit below the league, and who fills the gap |
| **Method** | Read the full flowchart, assumptions and checks, and find the code |

## 🧪 Method at a glance

```
Ball-by-ball data ─► Clean & validate ─► Win-probability model ─► Leverage index
                                                                        │
         Final lists ◄─ 2-of-4 rule ◄─ Shrinkage + simulation ◄─ Rating vs par on pressure balls
```

- **Win probability:** gradient-boosted model (LightGBM), predictions made out-of-fold and grouped by match so no ball is scored by a model that has seen its own match. Chase AUC **0.904**, log-loss **0.388**; first-innings AUC **0.714** (weaker, so first-innings pressure is treated cautiously).
- **Par engine:** expected runs and wickets for the situation and venue, built *without* player identity so it cannot absorb a player's own skill.
- **Shrinkage:** empirical-Bayes pull toward the group average, strongest for players with few pressure balls.
- **Uncertainty:** 4,000 simulations give the 80% interval and the chance of a true top 10; Royals' gaps use 2,000 bootstrap resamples of matches.
- **Data:** 200,999 raw deliveries; 200,961 analysed after 38 super-over balls are set aside. 40,193 pressure balls across 867 matches.

Full detail, with every assumption and the checks we ran, is in the methodology report and the Method tab of the dashboard.

## 📁 Repository structure

```
.
├── README.md
├── notebooks/
│   └── SupeRR_Selector_Summary.ipynb      # the story in charts (start here)
├── data/
│   └── pressure_rating_results.json       # pipeline outputs the summary notebook reads
├── docs/
│   └── (methodology report PDF)
├── dashboard/
│   └── index.html                         # the single-file dashboard, as deployed
├── pipeline/
│   └── (the project notebooks: cleaning, models, ratings and simulation)
└── pressure-rating-toolkit/
    ├── pressure_rating.py                 # the whole method in ONE file, for your own datasets
    ├── requirements.txt
    ├── template.csv                       # the columns your data needs
    └── README.md                          # how to use it
```

## 🔁 How to reproduce

**Summary notebook (fast, no raw data needed)**

```bash
git clone https://github.com/dipmalya120/SupeRR-Selector-Season-2.git
cd SupeRR-Selector-Season-2
pip install numpy pandas matplotlib jupyter
jupyter notebook notebooks/SupeRR_Selector_Summary.ipynb
```

Run all cells (*Kernel → Restart & Run All*). The notebook reads `data/pressure_rating_results.json`.

**Full pipeline (from raw ball-by-ball data)**

Place the ball-by-ball file where the pipeline README in `pipeline/` says, then run the scripts in the order listed there. Typical requirements: Python 3.10+, `pandas`, `numpy`, `lightgbm`, `scikit-learn`, `pyarrow`.

**Dashboard locally.** Open `dashboard/index.html` in any browser. It is one self-contained file.

## 🛠️ Run it on your own data

Want to rate players in a different tournament, such as a domestic league or an uncapped-player competition? The [`pressure-rating-toolkit/`](pressure-rating-toolkit/) folder holds the whole method as **one Python file**.

```bash
cd pressure-rating-toolkit
pip install -r requirements.txt
python pressure_rating.py --template                   # shows the columns your data needs
python pressure_rating.py --input my_balls.csv --out results/
```

You get ratings with 80% intervals, each player's chance of a true top 10, tiers, the 2-of-4-cutoffs shortlist, and a diagnostics file with model quality and checks. On this project's own IPL data it finds the same 40,193 pressure balls and agrees with the published lists on 8 of 10 batters and 8 of 10 bowlers.

Two things to keep in mind: the models are **retrained on your data**, and ratings are **comparable only within one dataset**, because opposition strength differs between leagues. See the [toolkit README](pressure-rating-toolkit/README.md) for the full guide.

## ⚠️ Limits worth knowing

- Pressure balls are a small slice of the data, so intervals are wide and the order inside Tiers B and C is soft. In the simulation, about five of ten names in each list land in the true top 10; the rest are close neighbours.
- The win-probability model is strong in chases but weaker in the first innings.
- Ratings are **descriptive**: they say who has handled pressure best in this data, not who will be best next season or at what auction price.
- Eligibility thresholds, the 60/40 win-chance filter and the 2-of-4 rule are our choices; the robustness checks show how much they matter.

## 🙏 Data and credits

Built for the Rajasthan Royals SupeRR Selector, Season 2, hosted on Unstop. Ball-by-ball IPL data as supplied for the competition. Team names and colours are used only to identify teams in an analytical context; this project is not affiliated with or endorsed by the Rajasthan Royals or the IPL.

## 👤 Author

**Dipmalya Chakraborty** · [GitHub @dipmalya120](https://github.com/dipmalya120)

---

<div align="center">
<sub>Descriptive analysis for a case competition. Not betting or investment advice.</sub>
</div>
