# Coding agent logos

The pair in the Contributing section: Antigravity, Codex — in that order. Two marks on
a 62×24 grid, 24 px slots on a 38 px pitch, the same geometry as
[`../models/`](../models/).

This row means something different from the models row. The models strip names what
Tel-Agent can be *pointed at*; this one names the coding agents the repository is set
up to be *worked on by*. Mixing the two would tell a reader that Tel-Agent answers
phone calls with an IDE.

| File | Ink | For |
|---|---|---|
| `agents-light.svg` | `#18181B` | GitHub's light theme |
| `agents-dark.svg` | `#FFFFFF` | GitHub's dark theme |

## Sources

| Mark | Source | Slug | Variant | Licence |
|---|---|---|---|---|
| Antigravity | [theSVG](https://thesvg.org) | `antigravity-google` | `mono` | MIT |
| Codex | theSVG | `codex-openai` | `mono` | MIT |

theSVG also carries a plain `codex` slug marked `brand-use`, which would bring OpenAI's
brand guidelines with it. `codex-openai` is MIT and is the one taken. Same check applies
to anything added here later — read the manifest's `license` field before shipping the
file, never after.

Both marks are single-colour and arrive painted with `currentColor`. Inside an `<img>`
there is nothing to inherit from, so each strip states its ink outright — which is why
there are two files rather than one.

## Adding a mark

Keep the pitch at 38 and the slot at 24. A mark whose source is not square gets scaled
by its longest side and centred vertically; a mark that carries its own brand colours
does not belong in a row that is inked per theme.

## Trademarks

Each mark belongs to its owner and appears here to name a tool this repository is
configured for. No endorsement or affiliation is implied.
