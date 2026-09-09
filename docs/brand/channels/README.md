# Channel icons

The icons in the README's opening rows, one file each. Every file is vendored here:
the product is self-hosted, so nothing in it may fetch a logo from a CDN.

> **Eleven icons, not eleven channels** in the first row. `call` and `landline` are two
> drawings of the *one* phone channel — the row is about recognition, not arithmetic.
> Slack is a separate question: see the note at the bottom.

| File | Shows | Origin | Colour |
|---|---|---|---|
| `call.svg` | A call in progress | Drawn here (Material "call") | `#7C3AED` |
| `landline.svg` | Desk phone, handset across the top over a keypad | Supplied | `#0284C7` |
| `web-chat.svg` | Globe | Drawn here (Feather "globe") | `#0D9488` |
| `sms.svg` | Speech bubble | Drawn here (Material "sms") | `#D97706` |
| `email.svg` | Envelope | Drawn here (Material "email") | `#EF4444` |
| `whatsapp.svg` | WhatsApp | SVG Repo, CC0 | Brand green |
| `telegram.svg` | Telegram | Simple Icons style | `#0088cc` |
| `messenger.svg` | Messenger | Meta's mark | `#0084FF` |
| `instagram.svg` | Instagram | Simple Icons style | Brand gradient |
| `discord.svg` | Discord | Simple Icons style | `#5865F2` |
| `slack.png` | Slack | 96x96, resized from a 1280px source | Brand four-colour |

## Second row — wave-2 channels

Added 2026-09-09 for the channels tracked in the issues labelled `channel` (#220–#235).
Source is [theSVG](https://thesvg.org) unless stated; the per-icon licence is the
registry's own field, checked before copying.

| File | Shows | Origin | Licence | Colour |
|---|---|---|---|---|
| `teams.svg` | Microsoft Teams | theSVG `microsoft-teams` | MIT | Brand gradient |
| `signal.svg` | Signal | theSVG `signal` | CC0-1.0 | `#3B45FD` |
| `viber.svg` | Viber | theSVG `viber` | CC0-1.0 | `#7360F2` |
| `google-chat.svg` | Google Chat | theSVG `google-chat` | CC0-1.0 | Brand four-colour |
| `mattermost.svg` | Mattermost | theSVG `mattermost`, mono variant recoloured | CC0-1.0 | `#0058CC` |
| `matrix.svg` | Matrix `[m]` | theSVG `matrix-org`, recoloured | CC0-1.0 | `#0DBD8B` |
| `irc.svg` | Speech bubble with a `#` | Drawn here | — | `#475569` |
| `line.svg` | LINE | theSVG `line` | CC0-1.0 | `#00C300` |
| `wechat.svg` | WeChat | theSVG `wechat` | CC0-1.0 | `#07C160` |
| `wecom.svg` | WeCom | Iconify `tdesign:logo-wecom`, recoloured | MIT | `#0082EF` |
| `qq.svg` | QQ | theSVG `qq` | CC0-1.0 | `#1EBAFC` |
| `dingtalk.svg` | DingTalk | Iconify `ant-design:dingtalk`, recoloured | MIT | `#0089FF` |
| `feishu.svg` | Feishu / Lark | Iconify `icon-park:lark` (ByteDance), recoloured | Apache-2.0 | `#3370FF` |
| `imessage.svg` | iMessage | theSVG `imessage` | CC0-1.0 | `#34DA50` |

WeCom, DingTalk and Feishu are absent from theSVG, Simple Icons and Lobe Icons; the
Iconify sets above were the nearest faithful marks. The theSVG Mattermost `default`
variant is white-on-transparent and vanishes on GitHub's light theme, which is why
the mono variant is used and filled by hand. IRC has no owner and no logo, so it
gets a drawn glyph in the same family as `sms` and `email`.

## One hue each

The six drawn glyphs used to share a single violet, which made them read as one grey
block beside the brand marks rather than as six things. Each now has its own hue.

Every value clears **3:1 against both `#ffffff` and `#0d1117`**, which is the contrast
floor for a graphic and the reason the obvious picks are missing: `sky-500`,
`amber-500` and `emerald-500` all fail against white. The darker -600 steps are used
instead. The brand marks keep their own colours, which are mid-tone already.

## Spacing

A newline between two `<img>` collapses to a single space, so the icons render almost
touching. GitHub strips `style` from README HTML, which rules out margins - the
separator has to be content, and the row uses `&nbsp;&nbsp;` per gap.

## Sizing

`landline.svg` arrived as landscape artwork filling a 122.9 x 98.9 box, while every
neighbour is a square glyph. A plain contain-fit would have left it squat in the row,
so it is fitted to 23.4 units wide and lands at 18.8 tall inside a 24 box, which
balances optically against a 22-tall square. The bounds came from `getBBox()` in a
browser rather than from reading the path data.

Its keypad holes depend on `fill-rule="evenodd"`, which the source carried in a
`<style>` block via `class="st0"`. That block does not survive being lifted out of its
own `<svg>`, so the rule was moved onto the element - without it the holes fill in
solid.

Every drawn glyph is 24x24. `whatsapp.svg` is a 58x58 Illustrator export and
`slack.png` a 96x96 raster - the only formats those two were available in. All fill
their box, so at a shared `height` they render at matching weight.

## Where the prose stands

**Slack - resolved.** `docs/SPEC.md` and `docs/ROADMAP.md` used to name Slack as the
counter-example in the sentence that keeps the channel list finite. Slack is now the
tenth channel, so that example named the thing it was excluding: both documents were
rewritten to drop Slack from the integrations list and to state the case it passes on
- an outside customer in a shared channel is a route in, an internal workspace is
not.

**Analog — resolved.** An earlier draft of `landline.svg` drew a rotary phone, which
sat badly against "Not analog-capable ... Tel-Agent only speaks SIP". The supplied
artwork is a modern office desk phone, which is what a SIP handset actually looks
like, so the conflict is gone.

## Trademarks

WhatsApp, Telegram, Messenger, Instagram, Discord, Slack, Microsoft Teams, Signal,
Viber, Google Chat, Mattermost, Matrix, LINE, WeChat, WeCom, QQ, DingTalk, Feishu,
Lark and iMessage are trademarks of their respective owners. They appear here to name the channel a user would reach us on -
nominative use - and imply no endorsement of, or affiliation with, Tel-Agent.
