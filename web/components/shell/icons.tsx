/**
 * The navigation glyphs, drawn as single stroked paths so they inherit the current
 * colour and stay legible at 17px on both grounds.
 */
const PATHS: Record<string, string> = {
  home: "M3 10.5 12 3l9 7.5 M5 9.5V21h14V9.5",
  message: "M4 5h16v11H9l-5 4V5Z",
  phone: "M4 4h4l2 5-2.5 1.5a11 11 0 0 0 5.5 5.5L15 13.5l5 2V20a12 12 0 0 1-16-16Z",
  headset: "M4 15v-3a8 8 0 0 1 16 0v3 M4 14h3v6H5.5A1.5 1.5 0 0 1 4 18.5V14Z M20 14h-3v6h1.5A1.5 1.5 0 0 0 20 18.5V14Z",
  calendar: "M4 6h16v15H4V6Z M4 10.5h16 M8.5 3v4 M15.5 3v4",
  users: "M3 20c0-2.8 2.4-4.5 5.5-4.5S14 17.2 14 20 M8.5 12a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z M16 15.6c2.2.4 3.8 1.9 3.8 4.4 M16.5 12a2.7 2.7 0 1 0 0-5.4",
  bot: "M6 8h12v9H6V8Z M9.5 12v1.5 M14.5 12v1.5 M12 5v3 M3.5 11.5v3 M20.5 11.5v3",
  bell: "M6 9a6 6 0 0 1 12 0c0 4 1.5 5.5 1.5 5.5h-15S6 13 6 9Z M10 18.5a2 2 0 0 0 4 0",
  book: "M4 5h6a2 2 0 0 1 2 2v12a2 2 0 0 0-2-2H4V5Z M20 5h-6a2 2 0 0 0-2 2v12a2 2 0 0 1 2-2h6V5Z",
  // The sidebar toggle. A panel with its rail drawn in, and no arrow: an arrow would
  // point the wrong way the moment the interface is read right to left.
  panel: "M4 5h16v14H4V5Z M9.5 5v14",
};

export function NavIcon({ name, color }: { name: string; color: string }) {
  const segments = (PATHS[name] ?? PATHS.home).split(" M");

  return (
    <svg
      viewBox="0 0 24 24"
      width={17}
      height={17}
      fill="none"
      stroke={color}
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {segments.map((segment, index) => (
        <path key={index} d={(index ? "M" : "") + segment} />
      ))}
    </svg>
  );
}
