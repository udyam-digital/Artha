# Artha UI Design Skill

## Context
`artha-ui` (at `../artha-ui` relative to the Artha backend) is a Next.js 14 dashboard that connects to the FastAPI backend at `localhost:8000`.

## Design Language: Zerodha Kite Inspired

The UI should look like Zerodha Kite (India's leading trading platform) designed it — dark, dense, professional trading terminal aesthetic.

### Color System

```
Page bg:    #1B1B1B  (kite-bg)
Sidebar:    #111111  (kite-sidebar)
Card:       #252525  (kite-surface)
Hover:      #2C2C2C  (kite-hover)
Border:     #363636  (kite-border)

Primary:    #387ED1  (Zerodha signature blue)
Gain:       #1CB088  (Zerodha green)
Loss:       #EA4747  (Zerodha red)
Warning:    #F5A623  (amber)

Text:       #DDDDDD  (primary)
Dim:        #BBBBBB  (secondary)
Muted:      #9EA3AD  (labels)
Faint:      #6B7280  (micro text)
```

### Layout Principles
- Sidebar: 200px, dark (#111), grouped sections, Zerodha-style logo area
- PortfolioBar: horizontal strip at top of content (Portfolio total | P&L | Cash | Age)
- Content max-width: 1400px, padding: 20px 24px
- Cards: radius 6px (flat), border #363636
- Tables: compact rows (40px), trading terminal density
- Numbers: always tabular-nums, monospace feel

### Tailwind Config
Add `kite` colors to tailwind.config.ts:
```ts
kite: {
  bg: '#1B1B1B',
  surface: '#252525',
  hover: '#2C2C2C',
  sidebar: '#111111',
  border: '#363636',
  blue: '#387ED1',
  green: '#1CB088',
  red: '#EA4747',
  amber: '#F5A623',
  text: '#DDDDDD',
  dim: '#BBBBBB',
  muted: '#9EA3AD',
  faint: '#6B7280',
}
```

### CSS Variable Mapping (globals.css)
```css
--background: #1B1B1B;
--foreground: #DDDDDD;
--card: #252525;
--primary: #387ED1;
--primary-foreground: #FFFFFF;
--muted: #2C2C2C;
--muted-foreground: #9EA3AD;
--border: #363636;
--destructive: #EA4747;
--radius: 0.375rem;  /* 6px - flatter than default */
--sidebar: #111111;
--sidebar-primary: #387ED1;
```

### Key Components
- `PortfolioBar` — Top strip showing live portfolio stats (new component)
- `StatCard` — Compact metric tiles, no large padding
- `HoldingsTable` — Dense 40px rows, trading positions style
- `Sidebar` — Grouped navigation with section dividers

### Verdict Colors
- BUY/STRONG_BUY: `text-kite-green`, `bg-kite-green/10`, `border-kite-green/30`
- SELL/STRONG_SELL: `text-kite-red`, `bg-kite-red/10`, `border-kite-red/30`
- HOLD: `text-kite-muted`, `bg-kite-hover`, `border-kite-border`

### File Locations
- `artha-ui/src/app/globals.css` — CSS variable definitions
- `artha-ui/tailwind.config.ts` — Tailwind color palette
- `artha-ui/src/components/layout/Sidebar.tsx` — Navigation sidebar
- `artha-ui/src/components/layout/PortfolioBar.tsx` — Top portfolio metrics strip
- `artha-ui/src/lib/utils.ts` — Tone/color utility functions
