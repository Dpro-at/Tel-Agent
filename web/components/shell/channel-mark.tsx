import { BrandGlyph, brandSlug } from "@/components/brands/brand-mark";

/**
 * The channels that are not a company.
 *
 * A phone line, a web chat, SMS and email have no owner and no logo, so they are drawn -
 * as single stroked paths in the same idiom as the navigation glyphs, so they sit beside
 * a vendored logo without looking like a different kind of thing. A typographic character
 * (☎, ▤) was the first attempt and read as a placeholder next to real artwork.
 */
const DRAWN: Record<string, string> = {
  // A handset, the same one the sidebar uses for calls.
  phone: "M4 4h4l2 5-2.5 1.5a11 11 0 0 0 5.5 5.5L15 13.5l5 2V20a12 12 0 0 1-16-16Z",
  // A globe: a web chat is the one channel that arrives from the open web.
  web_chat:
    "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z M3.5 9h17 M3.5 15h17 M12 3c2.4 2.7 2.4 15.3 0 18 M12 3c-2.4 2.7-2.4 15.3 0 18",
  // A bubble with two written lines. Three dots was the first draft and they collapse to
  // sub-pixel specks at 13px, which is the size this is actually used at.
  sms: "M4 5h16v10.5H9.5L5 19.5V15.5H4V5Z M7.5 9h9 M7.5 12h5.5",
  // An envelope.
  email: "M3 6h18v12H3V6Z M3.5 6.5l8.5 6 8.5-6",
  // A bubble with a hash: the channel sign IRC gave every chat room since.
  irc: "M4 5h16v10.5H9.5L5 19.5V15.5H4V5Z M10.5 7.5l-1 6 M14.5 7.5l-1 6 M8 9.25h8 M7.5 11.75h8",
};

/** Where a screen's own id for a channel differs from the id the marks are keyed by. */
const IDS: Record<string, string> = {
  webchat: "web_chat",
  web: "web_chat",
  "web chat": "web_chat",
};

/**
 * One channel's mark, sized for a line of text: the owner's logo where there is one, a
 * drawn glyph where there is not, and nothing at all for a channel we have neither for -
 * which is the honest outcome, and leaves the label to speak for itself.
 */
export function ChannelMark({ id, size = 13 }: { id: string; size?: number }) {
  const key = IDS[id] ?? id;

  if (brandSlug(key)) return <BrandGlyph id={key} size={size} />;

  const drawn = DRAWN[key];
  if (!drawn) return null;

  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      style={{ flex: "none" }}
    >
      {drawn.split(" M").map((segment, index) => (
        <path key={index} d={(index ? "M" : "") + segment} />
      ))}
    </svg>
  );
}
