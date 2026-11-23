// Small line icons for the sidebar and top bar (24x24 grid, drawn with strokes so they follow the text colour).
const PATHS = {
  home: 'M3 11l9-8 9 8M5 10v10h5v-6h4v6h5V10',
  sales: 'M6 2h9l5 5v15H6zM14 2v6h6M9 13h8M9 17h8',
  purchases: 'M3 4h2l2.4 11h11l2-8H6.5M9 20.5a.8.8 0 100-1.6.8.8 0 000 1.6zM18 20.5a.8.8 0 100-1.6.8.8 0 000 1.6z',
  accounting: 'M5 3h12a2 2 0 012 2v16H7a2 2 0 01-2-2zM5 19a2 2 0 012-2h12M9 7h6',
  crm: 'M16 20v-2a4 4 0 00-4-4H7a4 4 0 00-4 4v2M9.5 10a3.5 3.5 0 100-7 3.5 3.5 0 000 7zM21 20v-2a4 4 0 00-3-3.9M16 3.1a3.5 3.5 0 010 6.8',
  tax: 'M19 5L5 19M7 9a2 2 0 100-4 2 2 0 000 4zM17 19a2 2 0 100-4 2 2 0 000 4z',
  projects: 'M3 7h18v13H3zM8 7V4h8v3M3 13h18',
  hr: 'M4 4h16v16H4zM12 11a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM7.5 18a4.5 4.5 0 019 0',
  partners: 'M12 3v9h9a9 9 0 11-9-9zM15 3.5A9 9 0 0120.5 9H15z',
  approvals: 'M12 22a10 10 0 100-20 10 10 0 000 20zM8 12l3 3 5-6',
  compliance: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6zM9 12l2 2 4-4',
  workplace: 'M4 5h16v16H4zM4 10h16M8 3v4M16 3v4',
  reports: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
  documents: 'M7 3h8l4 4v14H7zM15 3v4h4M10 12h6M10 16h6',
  settings: 'M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M14 4v4M8 10v4M16 16v4',
  company: 'M4 21V4h10v17M14 9h6v12M8 8h2M8 12h2M8 16h2M2 21h20',
  admin: 'M5 11h14v10H5zM8 11V7a4 4 0 018 0v4',
  bell: 'M6 8a6 6 0 1112 0c0 7 3 8 3 8H3s3-1 3-8M10 20a2 2 0 004 0',
  plus: 'M12 5v14M5 12h14',
  menu: 'M4 6h16M4 12h16M4 18h16',
  search: 'M11 18a7 7 0 100-14 7 7 0 000 14zM21 21l-5-5',
  chevron: 'M9 6l6 6-6 6',
  grid: 'M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z',
  back: 'M19 12H5M11 6l-6 6 6 6',
  guide: 'M4 5.5A2.5 2.5 0 016.5 3H20v15H6.5A2.5 2.5 0 004 20.5zM4 20.5A2.5 2.5 0 006.5 18H20M9 8h6M9 11.5h4',
  arrow: 'M5 12h14M13 6l6 6-6 6',
  play: 'M7 4l13 8-13 8z',
  pause: 'M7 4h3v16H7zM14 4h3v16h-3z',
  help: 'M12 22a10 10 0 100-20 10 10 0 000 20zM9.4 9.2a2.7 2.7 0 115.1 1.2c-.6 1.1-2 1.5-2 3M12 17.2h.01',
  logout: 'M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10',
};

export default function Icon({ name, size = 18 }) {
  const d = PATHS[name] || PATHS.documents;
  return (
    <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d={d} /></svg>
  );
}
