# AI model logos

The row under the tagline, which promises "any AI model": OpenAI, Claude, Gemini,
Mistral, Ollama, Perplexity, Copilot, Manus, DeepSeek, Grok, Qwen, Groq, OpenRouter —
in that order. Thirteen marks on a 480×24 grid: 24 px slots on a 38 px pitch.

| File | Ink for the single-colour marks | For |
|---|---|---|
| `models-light.svg` | `#18181B` | GitHub's light theme |
| `models-dark.svg` | `#FFFFFF` | GitHub's dark theme |

## Where the five newest marks came from

The first eight predate this table. The five added on 2026-08-27 are recorded here so
the strip can be rebuilt without guessing:

| Mark | Source | Variant | Licence | Kind here |
|---|---|---|---|---|
| DeepSeek | [theSVG](https://thesvg.org) | `default` | MIT | colour, unmodified (`#4D6BFE`) |
| Grok | theSVG (`xai`) | `mono` | MIT | single-colour, inked per strip |
| Qwen | [Lobe Icons](https://lobehub.com/icons) | mono | MIT | single-colour, inked per strip |
| Groq | Lobe Icons | mono | MIT | single-colour, inked per strip |
| OpenRouter | theSVG | `mono` | CC0-1.0 | single-colour, inked per strip |

theSVG's `default` for Groq is a full-bleed orange tile, not a glyph — it would have
read as a coloured block in a row of line marks. Lobe's mono mark is the one that
belongs here. Same reasoning sent Grok to `xai/mono`: the `xai-grok` file is an
841×595 box, and scaling that to a 24 px slot leaves the glyph swimming in it.

## Why one strip and not thirteen images

The sources come from different places and disagree about everything: twelve are
24×24, Mistral's is 397×282; five carry their own brand colours, and the rest are
single-colour marks painted with `currentColor` or a hard-coded white. Composing them
into one file fixes the alignment once, at build time, and reduces the README to a
single `<picture>`.

`currentColor` is the reason there are two files. Inside an `<img>` there is
nothing to inherit from, so those marks resolve to black and vanish on a
dark page. Each strip therefore states the ink outright.

## Three traps, if this is ever rebuilt

**Paint attributes on the root.** Several of these logos set `fill` on their
`<svg>` element and nowhere else. Lift the body out of that root and the fill is
gone — the logo silently goes black. The build copies those attributes onto the
wrapping `<g>`.

**A white fill is not a `currentColor` fill.** Several AI marks ship with
`fill="#ffff"` on the root — invisible on a light page and not something a wrapping
`<g>` can override, because the attribute is already there. The build treats a root
fill of `#ffff`/`#fff`/`#ffffff`/`currentColor` as "no opinion" and replaces it with
the strip's ink.

**Editor metadata.** `mistral-ai.svg` is an Inkscape save carrying
`<sodipodi:namedview>` and `inkscape:*` attributes, whose `xmlns` declarations
also live on the root. Dropped into a strip they become unbound prefixes, and an
unbound prefix is not a cosmetic problem: the file stops being well-formed XML and
the browser refuses to render *any* of it. The build strips that metadata and then
parses the result to prove it.

## Trademarks

Each mark belongs to its owner and appears here to name a model Tel-Agent can be
pointed at. No endorsement or affiliation is implied.
