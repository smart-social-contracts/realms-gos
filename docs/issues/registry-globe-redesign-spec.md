# Registry Frontend: Globe.gl Redesign with H3 Hex Regions

> **Status:** Spec / reference document for GitHub issue  
> **App:** `src/realm_registry_frontend/`  
> **Repo:** smart-social-contracts/realms-gos

---

## Summary

Redesign the Realm Registry home page (`/`) to use an interactive **3D globe** ([globe.gl](https://github.com/vasturiano/globe.gl)) as the primary visual, replacing the current list-first layout and Leaflet 2D map toggle. The globe must display **H3 hex influence regions** for each registered realm, support slow auto-rotation with user drag interaction, and maintain the Realms **light gray** design language.

Inspired by the spatial drama of the [Internet Computer Dashboard](https://dashboard.internetcomputer.org), but **not** a dark-theme clone — Realms stays light, elegant, and monochrome.

---

## Motivation

The current registry home (`src/routes/+page.svelte`, ~3,000 lines) presents realms as a card grid with an optional full-screen Leaflet map. The map works (H3 hex zones, zone data from realm backends) but feels utilitarian compared to the network-scale story Realms tells.

Goals:
- Make the registry feel like a **living global network**, not a directory
- Keep **H3 hex regions** as a first-class visualization (area-of-influence is core to Realms)
- Preserve **text search** and realm discovery
- Align visually with the gray palette used across `realm_frontend`

---

## Design Principles

| Principle | Detail |
|-----------|--------|
| **Light theme only** | Background `#FAFAFA`, surfaces `#FFFFFF` / `#F5F5F5`. No dark mode for this redesign. |
| **Grays only** | No color accents on the globe or UI chrome. Markers, hex fills, and strokes use the gray scale (`#171717` → `#E5E5E5`). |
| **No country flags** | Geographic spread shown as a number ("8 countries"), not flag icons. |
| **Discrete metrics** | A single muted KPI line — no dashboard cards, no charts in v1. |
| **Globe is hero** | Full-viewport globe on landing. Not a toggle or overlay. |
| **Search always available** | Persistent header search; filters globe + side panel simultaneously. |
| **Restraint over spectacle** | Slow rotation, subtle hex opacity, minimal chrome. Elegant, not cyberpunk. |

### Design tokens (source of truth)

Adopt CSS variables aligned with `src/realm_frontend/src/lib/theme/theme.ts`:

```css
--bg:              #FAFAFA;
--surface:         #FFFFFF;
--surface-2:       #F5F5F5;
--border:          #E5E5E5;
--text-primary:    #171717;
--text-secondary:  #525252;
--text-tertiary:   #737373;
--text-faint:      #A3A3A3;
--gray-300:        #D4D4D4;
--gray-400:        #A3A3A3;
```

Font: **Inter** (already in use).

---

## Target UX

### Layout (desktop)

```
┌──────────────────────────────────────────────────────────────────┐
│  [Logo]     [ 🔍 Search realms...                    ]  Auth  ☰  │  fixed header, ~56px
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│                    ╭──── 3D globe (globe.gl) ────╮              │
│                    │   H3 hex patches on surface  │              │
│                    │   realm HQ point markers     │              │
│                    ╰──────────────────────────────╯              │
│                                                                  │
│  12 realms · 3,420 users · 8 countries                           │  discrete KPI line
│                                                                  │
│                                         ┌─────────────────────┐  │
│                                         │ Browse Realms    [×]  │  │  slide-over panel
│                                         │ [stage ▾] [sort ▾]  │  │  ~360px, right
│                                         │ ─────────────────── │  │
│                                         │ Realm card          │  │
│                                         │ Realm card          │  │
│                                         └─────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

### Layout (mobile)

- Globe remains full-viewport hero
- Header: logo, search icon (expands to full-width input), hamburger
- KPI line below header or overlaid bottom-left
- Realm list becomes a **bottom sheet** (swipe up), triggered by search or "Browse" button
- RegistryAssistant FAB remains (existing behavior from `+layout.svelte`)

---

## Globe Specification

### Library

- **[globe.gl](https://github.com/vasturiano/globe.gl)** (Three.js-based)
- Wrapper: new Svelte component `GlobeView.svelte`
- Dynamic import on client only (`browser` guard) — globe.gl touches DOM/WebGL

### Dependencies to add

```json
{
  "globe.gl": "^2.x",
  "three": "^0.x"
}
```

`h3-js` is already a dependency (`^4.1.0`). **Remove `leaflet`** once migration is complete.

### Globe appearance (light / gray)

| Element | Spec |
|---------|------|
| Page background | `#FAFAFA` — globe canvas transparent or matching |
| Globe sphere | Very light gray material (`#F5F5F5` or `#FFFFFF`), subtle Phong shading, no satellite texture |
| Atmosphere | Disabled or extremely faint |
| Country/land outlines | Optional: faint `#E5E5E5` wireframe or no land detail (minimal is preferred) |
| Grid/graticule | None |

Reference: globe.gl [custom globe styling example](https://github.com/vasturiano/globe.gl/blob/master/example/custom-globe-styling/index.html).

### Interaction

| Behavior | Spec |
|----------|------|
| **Auto-rotate** | Slow continuous rotation (~0.3°/s) via `controls().autoRotate = true` |
| **User drag** | OrbitControls: user can rotate globe freely |
| **Pause on interaction** | Auto-rotate pauses while user drags; resumes after 5s idle |
| **Zoom** | Disabled or heavily limited (`minDistance === maxDistance`) — keep globe at fixed apparent size |
| **Hex hover** | Tooltip with zone info (reuse current popup content, styled for light theme) |
| **Hex click** | Highlight hex; open/update slide-over with realm(s) in that zone |
| **Point markers** | Small gray dots at realm HQ / zone centers; click → fly-to + select realm in panel |
| **Search fly-to** | Selecting a realm from search animates `pointOfView({ lat, lng, altitude }, 1000)` |

### Point markers (HQ)

- Use globe.gl `pointsData` layer
- One marker per realm at primary zone center (highest `user_count` zone, or first zone)
- Color: `#525252`; altitude: minimal (~0.01 globe radii)
- Label on hover: realm name

---

## H3 Hex Regions on Globe

### Why polygons layer (not hexBin)

The current Leaflet implementation builds **per-cell H3 polygons** with influence rings (`gridDisk(center, 3)`). This must be preserved — not replaced by a generic binning layer.

Use globe.gl **`polygonsData`** with GeoJSON `Polygon` geometries derived from `h3.cellToBoundary()`.

### Data pipeline (port from existing code)

Existing logic in `+page.svelte` → extract to `src/lib/globe/hex-data.ts`:

1. For each filtered realm, fetch zones via `get_zones(resolution)` on realm backend canister
2. For each zone center, compute H3 index: `h3.latLngToCell(center_lat, center_lng, H3_RESOLUTION)`
3. Expand influence: `h3.gridDisk(centerHexIndex, INFLUENCE_RINGS)` where `INFLUENCE_RINGS = 3`
4. Aggregate into `hexData[h3Index] = { realms[], totalUsers }` (same structure as today)
5. For each hex, build GeoJSON polygon:

```javascript
const boundary = h3.cellToBoundary(hexIndex);
// globe.gl expects [lng, lat] — h3-js returns [lat, lng]
const coords = boundary.map(([lat, lng]) => [lng, lat]);
coords.push(coords[0]); // close ring
geometry: { type: 'Polygon', coordinates: [coords] }
```

### Visual encoding (gray scale)

Replace current rainbow `getRealmColor()` with gray-scale differentiation:

| Cell type | Fill | Stroke | Opacity |
|-----------|------|--------|---------|
| Center hex (HQ) | `#525252` | `#171717` | 0.35–0.55 (scales with user count) |
| Influence ring 1 | `#737373` | `#525252` | 0.15–0.25 |
| Influence ring 2 | `#A3A3A3` | `#737373` | 0.10–0.18 |
| Influence ring 3 | `#D4D4D4` | `#A3A3A3` | 0.06–0.12, dashed stroke |
| Multi-realm overlap | `#404040` stroke | mixed | use dominant realm by user count |

Constants (unchanged from current):
- `H3_RESOLUTION = 6` (~3.2 km edge)
- `INFLUENCE_RINGS = 3`

globe.gl polygon accessors:
- `polygonCapColor(d => d.fillColor)`
- `polygonSideColor(d => d.sideColor)` — same as cap or slightly darker
- `polygonStrokeColor(d => d.strokeColor)`
- `polygonAltitude(d => d.altitude)` — flat (`0`) or very slight lift for center hexes (`0.005`)
- `polygonLabel(d => d.labelHtml)` — tooltip on hover

### Hex tooltip content

Port existing Leaflet popup HTML to light-themed tooltip:
- Total users in zone
- List of realms with influence (name, user count, location name)
- HQ / Base badges (gray, not colored)
- H3 index (monospace, faint)

---

## Data Layer

### Existing APIs (no backend changes for v1)

| Source | Method | Used for |
|--------|--------|----------|
| `realm_registry_backend` | `list_realms()` | Realm list |
| Each `realm_backend` | `status()` | `users_count`, `realm_stage`, `realm_name`, manifesto |
| Each `realm_backend` | `get_zones(resolution)` | H3 zone centers, user counts, location names |

N+1 fan-out to realm backends is **acceptable at current scale** (~12 realms). No registry aggregation endpoint required for v1.

### Zone response shape (from `get_zones`)

```json
{
  "success": true,
  "zones": [
    {
      "h3_index": "...",
      "center_lat": 52.52,
      "center_lng": 13.405,
      "user_count": 42,
      "location_name": "Berlin"
    }
  ],
  "total_users": 120
}
```

Realms without zone data: **no hexes, no marker** on globe (same as current — no fallback demo coordinates).

### Derived KPIs (client-side)

Computed from loaded realm + zone data:

```
{totalRealms} realms · {sum(users_count)} users · {uniqueCountries} countries
```

Country count: derive from zone `center_lat`/`center_lng` via reverse-geocoding is **out of scope** for v1. Approximate with unique rounded lat/lng clusters or omit until a backend field exists. If omitted, show: `12 realms · 3,420 users`.

Optional second line (whisper): `3 live · 5 beta · 4 alpha` from `realm_stage`.

---

## Search & Filtering

### Header search

- Persistent text input (existing `searchQuery` logic)
- Filters by realm name, id, manifesto text
- Updates: globe hex layer, point markers, slide-over list — all reactively

### Slide-over filters

Move existing controls into panel header:
- Stage filter (`filterStage` dropdown)
- Sort (`sortBy` dropdown: name, users, newest)

### Search → globe sync

When user types or selects a realm:
1. Filter hexes/markers to matching realms (or dim non-matching to ~30% opacity)
2. If single match or explicit selection: `globe.pointOfView({ lat, lng, altitude: 1.8 }, 1000)`
3. Open slide-over panel if closed
4. Scroll selected realm card into view

---

## Slide-over Panel (`RealmPanel.svelte`)

### Behavior

- Default: **closed** on desktop (globe unobstructed)
- Opens via: "Browse" button, search focus, hex/marker click, keyboard `/`
- Closes via: `×`, Escape, click outside (desktop)
- Width: 360px; background `#FFFFFF`; border-left `1px solid #E5E5E5`; subtle shadow
- Scrollable realm card list (reuse existing card markup from `+page.svelte`)

### Realm card (unchanged content)

Each card retains:
- Logo / fallback initial
- Stage badge, user count
- Name, manifesto (truncated, click for full)
- Canister ID, created date
- "Visit" button

Remove colored `card-accent` bar or make it `#E5E5E5`.

---

## Component Architecture

### New files

```
src/realm_registry_frontend/
├── src/
│   ├── lib/
│   │   ├── theme/
│   │   │   └── tokens.css              # CSS variables from realm_frontend palette
│   │   ├── globe/
│   │   │   ├── hex-data.ts             # H3 aggregation (extracted from +page.svelte)
│   │   │   ├── globe-config.ts         # Colors, rotation speed, resolution constants
│   │   │   └── zone-fetcher.ts         # fetchZoneData / status fan-out
│   │   └── components/
│   │       ├── GlobeView.svelte        # globe.gl wrapper
│   │       ├── RegistryHeader.svelte   # logo, search, auth, browse toggle
│   │       ├── RegistryKpiLine.svelte  # discrete metrics
│   │       ├── RealmPanel.svelte       # slide-over list
│   │       └── RealmCard.svelte        # extracted card
│   └── routes/
│       └── +page.svelte                # orchestrator (~200 lines target)
```

### Refactor scope

- **Decompose** `+page.svelte` (~3,000 lines) — do not add globe into the monolith
- **Remove** Leaflet map view, `viewMode` toggle, `map-back-btn`
- **Keep** footer, auth, i18n, create-realm link, marketplace link
- **Keep** `RegistryAssistant` in `+layout.svelte` (adjust z-index so panel/globe don't conflict)

---

## i18n

Add/update keys in `src/lib/i18n/locales/*.json`:

```json
{
  "globe": {
    "loading": "Loading globe…",
    "browse_realms": "Browse realms",
    "kpi_realms": "{count} realms",
    "kpi_users": "{count} users",
    "kpi_countries": "{count} countries",
    "zone_users": "{count} users in this zone",
    "realms_influence": "{count} realms with influence"
  }
}
```

Deprecate: `map.back_to_list`, `controls.map_view`, `controls.list_view`.

---

## Performance Considerations

| Concern | Mitigation |
|---------|------------|
| WebGL bundle size | Dynamic import globe.gl + three.js only on `/` route |
| Many H3 polygons | Merge adjacent same-realm hexes where possible; cap at ~2,000 polygons |
| N+1 backend calls | Acceptable for v1; show loading state on globe |
| Re-render on search | Debounce search 150ms; diff hex data rather than full rebuild |
| Mobile GPU | Test on mid-range Android; reduce polygon count if needed |

---

## Accessibility

- Search input: proper `label` / `aria-label`
- Globe: `aria-label="Interactive globe showing realm locations"`; keyboard users rely on search + list (globe is supplementary)
- Panel: focus trap when open; restore focus on close
- Reduced motion: respect `prefers-reduced-motion` — disable auto-rotate

---

## Out of Scope (v1)

- Dark theme
- Country flags or flag grids
- Charts / stage breakdown widgets
- Registry-level aggregation backend endpoint
- 3D globe on other routes (`/create-realm`, `/my-dashboard`)
- Svelte 5 migration (stay on Svelte 4 unless separately planned)
- Reverse geocoding for country count
- Hex altitude extrusion proportional to users (keep flat/subtle)

---

## Acceptance Criteria

- [ ] Home page loads with full-viewport globe.gl globe (no list-first layout)
- [ ] Globe auto-rotates slowly; user can drag to rotate; auto-rotate resumes after idle
- [ ] H3 hex influence regions visible on globe for realms with zone data
- [ ] Hex opacity fades with distance from center (3 influence rings)
- [ ] All globe/UI colors use gray palette — no rainbow realm colors
- [ ] Header search filters realms and updates globe + panel
- [ ] Selecting a realm flies globe to that location
- [ ] Slide-over panel with realm cards opens/closes correctly
- [ ] Discrete KPI line shows realm count and total users
- [ ] Hex hover shows zone tooltip; hex click highlights realm in panel
- [ ] Mobile: bottom sheet panel, usable search
- [ ] Leaflet removed from dependencies
- [ ] `RegistryAssistant` still works (float + dock)
- [ ] i18n keys updated across all 6 locales
- [ ] No regression: auth, create-realm link, marketplace link, footer

---

## Implementation Phases

### Phase 1 — Foundation
- Add `tokens.css`, globe.gl dependency
- Create `GlobeView.svelte` with bare globe (light gray sphere, auto-rotate, drag)
- New layout shell with header

### Phase 2 — H3 hex layer
- Extract `hex-data.ts` from current Leaflet logic
- Render polygons on globe
- Port tooltips

### Phase 3 — Panel + search
- `RealmPanel.svelte`, `RealmCard.svelte`
- Wire search/filter to hex layer and panel
- Fly-to on select

### Phase 4 — KPI + polish
- `RegistryKpiLine.svelte`
- Loading states, reduced motion, mobile bottom sheet
- Remove Leaflet, delete dead code from `+page.svelte`

### Phase 5 — QA
- Cross-browser (Chrome, Firefox, Safari)
- Mobile (iOS Safari, Android Chrome)
- Performance profiling with full realm set

---

## References

- Current implementation: `src/realm_registry_frontend/src/routes/+page.svelte`
- Theme tokens: `src/realm_frontend/src/lib/theme/theme.ts`
- globe.gl docs: https://github.com/vasturiano/globe.gl
- globe.gl polygons layer: https://github.com/vasturiano/globe.gl#polygons-layer
- ICP Dashboard (layout inspiration only): https://dashboard.internetcomputer.org
- Current staging: https://staging.realmsgos.org
