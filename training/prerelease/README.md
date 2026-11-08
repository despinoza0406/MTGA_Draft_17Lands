# Pre-release ratings

Day-1 card ratings for a set that has no 17Lands games yet, so the advisor,
the deck builder and Deck Check work from the first Premier Draft.

## How

1. **Reviews.** MTGAZone (0–5, one page per color) and Draftsim (0–10, one
   page) rate every card of a set before it comes out. `reviews` downloads and
   caches both per set, in `<data-dir>/prerelease/reviews/<source>/`.
2. **Calibration.** Each source's ratings are turned into z-scores within the
   set, since their scales differ. For past sets with a review and a 17Lands
   dataset, the sources' consensus, each source on its own, and a few card
   features (rarity, gold, removal, card advantage, cost, creature, land) are
   fit to GIH WR, standardized within each set. Each set is checked with a fit
   made on the others. Predicted datasets are never used as past sets.
3. **Build.** Scryfall's card list for the new set, with the ETL's tags, gets a
   predicted GIH WR from the calibration, spread like a typical set
   (mean 55.3, SD 3.9). It's written into the tool's `Sets` folder as
   `<SET>_<EVENT>_All_Data.json` for every event, with `"predicted": true` in
   the meta and every sample count at 0. A file that already has 17Lands data
   is never overwritten, and the tool's sync replaces the predicted one when
   17Lands publishes data.

```bat
Preparar_Set_Nuevo.cmd TRK
```

or step by step, from the repository root's parent:

```sh
python mtga/training/prerelease/prerelease.py reviews TRK FRA BLB ... --data-dir data
python mtga/training/prerelease/prerelease.py calibrate --tool-dir C:/MtgaDraftTool --data-dir data
python mtga/training/prerelease/prerelease.py build TRK --tool-dir C:/MtgaDraftTool --data-dir data
```

## What the backtest found (12 sets, November 2026)

Spearman correlation with the set's final GIH WR, each set held out of its
own fit:

| set | MTGAZone | Draftsim | consensus | calibrated |
|---|---|---|---|---|
| BLB | 0.67 | 0.68 | 0.71 | 0.72 |
| DFT | 0.67 | 0.63 | 0.70 | 0.73 |
| DSK | 0.67 | 0.62 | 0.68 | 0.68 |
| EOE | 0.56 | 0.60 | 0.61 | 0.61 |
| FDN | 0.66 | – | 0.66 | 0.69 |
| FIN | 0.46 | 0.54 | 0.58 | 0.61 |
| FRA | 0.68 | 0.57 | 0.68 | 0.70 |
| OTJ | 0.59 | – | 0.59 | 0.62 |
| SOS | 0.67 | 0.56 | 0.62 | 0.64 |
| TDM | 0.55 | 0.54 | 0.61 | 0.61 |
| TLA | 0.54 | 0.59 | 0.65 | 0.68 |
| TMT | 0.55 | 0.61 | 0.61 | 0.60 |
| mean | 0.61 | 0.59 | 0.64 | 0.66 |

Neither reviewer is better than the other; together they are (+0.03), and the
calibration adds another +0.02. A typical card is still off by about 0.6 SD of
GIH WR (around 2.3 points).

Sources looked at and left out:

- **Card Game Base** (letter grades): several of its tier lists were published
  after the set came out (FIN, EOE, SOS), and those correlate 0.84–0.91 with
  GIH WR, so they already used 17Lands data. Its pre-release lists score like
  the others (0.59–0.68). It had nothing for Star Trek.
- **AetherHub's pro ratings** refuse automated downloads.

MTGAZone alone, where its ratings missed: mean of (real − predicted from the
rating alone) in SDs of GIH WR (± one standard error).

- Commons did better than rated (+0.10 ± 0.02) and rares worse (−0.11 ± 0.04):
  rares are rated on their ceiling.
- Removal (+0.11 ± 0.03), card advantage (+0.07 ± 0.03) and lands (+0.14 ± 0.07)
  were underrated.
- Cheap cards were underrated (+0.12 ± 0.03 at 0–1 mana, +0.06 at 2) and 3–4
  drops overrated (−0.06, −0.08).
- Gold cards were slightly overrated (−0.05 ± 0.03).
- The middle of the scale is compressed: 1.5s beat their rating, 3.0–3.5s fell
  short, and 4+ cards (bombs) did better than rated.

## Limits

- Two reviewers. Limited Resources or AetherHub's pro ratings would be the
  next ones to add.
- No archetype (color pair) win rates, ALSA or ATA: the advisor uses the
  overall rating and the Wheel column has nothing to go on.
- Cards the review skipped (mostly lands) are unrated.
