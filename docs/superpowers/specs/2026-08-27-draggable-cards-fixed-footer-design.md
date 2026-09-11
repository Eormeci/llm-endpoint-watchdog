# Draggable Cards & Fixed Footer

**Date:** 2026-08-27
**Project:** LLM Endpoint Watchdog
**Scope:** Single-file Python dashboard (`llm-endpoint-watchdog.py`), embedded HTML/CSS/JS

## Summary

Add two behaviors to the dashboard:

1. **Draggable, reorderable endpoint cards** — the user grabs a card by a handle and drops it in a new position; the new order is persisted on the server (shared across all browsers/devices, consistent with how endpoints themselves are stored).
2. **Fixed footer** — the `LLM Endpoint Watchdog v1.0 · Dashboard port 9090 · Auto-refresh 5s` line stays pinned to the bottom of the viewport and no longer shifts as cards are added/removed.

The project's "no external Python packages" philosophy extends to the dashboard: drag-and-drop is implemented with the native HTML5 Drag and Drop API. No CDN or third-party JS library is introduced.

## Background

Current state (llm-endpoint-watchdog.py):

- `ENDPOINTS` is an ordered server-side list of `{endpoint, name}` dicts (line 22, 98).
- `render()` rebuilds `grid.innerHTML` from `ENDPOINTS` on every 5s `refresh()` (line 580, 624, 641–642).
- The footer is a normal-flow `<div class="footer">` (line 503, CSS line 320) — it moves as content grows.
- Cards already have a `.card-remove` button at top-right (line 599, CSS line 458). A drag handle at top-left is the natural symmetric counterpart.
- Endpoints are added/removed via `POST`/`DELETE /api/endpoints` and persisted to `watchdog_endpoints.json` via `save_endpoints()` (line 83). The new reorder operation follows the same pattern.

## Design

### 1. Data Model & Persistence

- `ENDPOINTS` already encodes order as list position. **No schema change** — order is implicit in the list.
- Persistence: `save_endpoints(ENDPOINTS)` after every reorder (existing function, line 83). The on-disk JSON shape is unchanged.
- The endpoint string (`"host:port"`) is the stable identity key for reordering (already used as the dedup key in `load_endpoints`, line 75).

### 2. API

New endpoint: `PATCH /api/endpoints/reorder`

- **Request body:** `{"order": ["10.0.0.5:8000", "127.0.0.1:8000", ...]}` — array of endpoint strings in the desired new order.
- **Validation:**
  - The set of strings in `order` must exactly equal the set of `endpoint` values currently in `ENDPOINTS` (no missing, no extra, no duplicates, no unknown strings).
  - `order` length must equal `ENDPOINTS` length.
  - On mismatch: `400 {"ok": false, "error": "Order listesi mevcut endpoint'lerle uyumsuz"}`.
- **Action on success:**
  - Rebuild `ENDPOINTS` in-place, preserving each endpoint's `name` (looked up from the current list by endpoint string).
  - Call `save_endpoints(ENDPOINTS)`.
  - Return `200 {"ok": true, "endpoints": ENDPOINTS}` (the reordered list, mirroring `POST`/`DELETE` responses so the client can sync).
- Implemented as a new `do_PATCH` method on `DashboardHandler`, mirroring the structure of `do_POST`/`do_DELETE` (line 682, 717).
- CORS: `do_OPTIONS` already allows `PATCH`? — **No, it currently lists only `GET, POST, DELETE, OPTIONS` (line 742). Add `PATCH`** to `Access-Control-Allow-Methods`. (`PATCH` is chosen over `PUT`/`POST` because it more accurately describes a partial-order update; the existing `POST`/`DELETE` semantics for the collection are unchanged.)

### 3. Frontend — Drag Handle

- New element per card, top-left, symmetric to `.card-remove` (which is top-right):
  ```html
  <span class="card-drag-handle" title="Sürükleyerek taşı" draggable="true"
        ondragstart="onDragStart(event, '<ep>')">&#8942;&#8942;</span>
  ```
  (`⋮⋮` = U+22EE U+22EE, two vertical ellipses — grip cue, font-independent.)
- CSS:
  ```css
  .card-drag-handle {
      position: absolute;
      top: 10px;
      left: 12px;
      color: #555;
      cursor: grab;
      font-size: 1.1em;
      line-height: 1;
      user-select: none;
      padding: 2px 4px;
      border-radius: 6px;
      transition: color 0.2s, background 0.2s;
  }
  .card-drag-handle:hover { color: #76b900; background: #76b90022; }
  .card-drag-handle:active { cursor: grabbing; }
  ```
- The handle is the only draggable element. The card root and its text remain non-draggable so that normal text selection, clicks on the remove button, and model-tag interaction are not disrupted.
- During an active drag, the dragged card gets a `.dragging` class (reduced opacity, dashed border) and the drop target gets a `.drag-over` class (a highlighted insertion line via `::after`). Both classes are cleared on `dragend`.

### 4. Drag-and-Drop State Machine

The 5s `setInterval(refresh, 5000)` (line 642) calls `render()`, which does `grid.innerHTML = html` (line 624). If a refresh fires mid-drag, the dragged DOM node is destroyed and the browser cancels the drag. To avoid this, **refresh is paused for the duration of a drag**:

```
States: idle | dragging | saving

idle
  └─ dragstart (on handle) ─> dragging
       • isDragging = true
       • store source endpoint string in draggedEp + dataTransfer
       • pause: clearInterval(refreshTimer); refreshTimer = null
       • add .dragging to the source card

dragging
  ├─ dragover (on a card) ─> preventDefault(); mark drop target via .drag-over
  ├─ drop (on a card) ─> saving
  │     • reorder ENDPOINTS array in place (move source to target position)
  │     • render() for immediate visual feedback
  │     • PATCH /api/endpoints/reorder
  │       ├─ 200 ok ─> sync ENDPOINTS from response; isDragging=false; resume timer; refresh()
  │       └─ error  ─> fetch /api/endpoints to restore true order; resume timer; refresh()
  └─ dragend (no drop, e.g. dropped outside) ─> idle
        • isDragging = false; resume timer; refresh()
```

- A module-level `isDragging` flag is consulted by `refresh()`:
  ```js
  async function refresh() {
      if (isDragging) return;          // safety net even if timer not cleared
      // ...existing fetch+render...
  }
  ```
  This belt-and-suspenders approach guards against the rare case where the timer fires between `dragstart` and `clearInterval` landing.
- `setInterval` is assigned to a named variable (`refreshTimer`) so it can be cleared and re-created:
  ```js
  let refreshTimer = null;
  function startRefreshTimer() {
      if (refreshTimer) clearInterval(refreshTimer);
      refreshTimer = setInterval(refresh, 5000);
  }
  function stopRefreshTimer() {
      if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null; }
  }
  ```
  Initial `setInterval(refresh, 5000)` at line 642 is replaced with `startRefreshTimer()`.

### 5. Local Reorder (in `drop`)

- `ENDPOINTS` is a client-side mirror (line 505, `const ENDPOINTS = __ENDPOINTS__`).
- `drop` handler:
  1. Find source index (`draggedEp`) and target index (the card under the cursor).
  2. `ENDPOINTS.splice(srcIdx, 1); ENDPOINTS.splice(tgtIdx, 0, movedItem);`
  3. `render(lastStatusData)` — `render` reads `ENDPOINTS` for order and `data[ep]` for status. A new module-level variable `lastStatusData` caches the payload of every successful `refresh()`; `refresh()` updates it before calling `render()`. The drop handler reuses it for the immediate post-drop render, then `PATCH`.
  4. After `PATCH` returns, `ENDPOINTS` is overwritten with the server response (single source of truth).
- Edge case: if the user drops a card onto itself (`srcIdx === tgtIdx`), skip the `PATCH` (no-op), just resume the timer.

### 6. Footer Fix

CSS change only — no markup change. The footer element (line 503) stays.

```css
.footer {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    text-align: center;
    color: #444;
    font-size: 0.75em;
    padding: 10px 0;
    background: #0a0a0f;        /* matches body, so it covers cards scrolling under it */
    border-top: 1px solid #1a1a1a;   /* subtle separator */
    z-index: 50;
}
body {
    padding-bottom: 50px;       /* footer height + breathing room; keeps last row from being hidden */
}
```

- `background: #0a0a0f` matches the body so a card scrolling under the footer is cleanly obscured (no see-through).
- `padding-bottom` on body ensures the last grid row is never trapped under the footer on short viewports.
- `z-index: 50` sits below the modal (z-index 200, line 391) and the add-port button (z-index 100, line 366) but above cards.

### 7. Error Paths & Edge Cases

| Scenario | Handling |
|---|---|
| `PATCH` returns non-200 or network error | Fetch `/api/endpoints` to get the authoritative order, overwrite `ENDPOINTS`, `render()`. Show a brief `console.warn` (no alert — this is a passive monitor). |
| `order` array missing endpoints or has unknown ones | Server returns 400; client falls back to `/api/endpoints` fetch (same path as above). |
| Endpoint added (by another browser) mid-drag | The next `refresh()` after the drag ends will include it at the server's position (end of list, per existing `POST` behavior at line 704). The current drag completes against the pre-addition list; no corruption. |
| Endpoint removed (by another browser) mid-drag | If the dragged endpoint was removed server-side, `PATCH` returns 400 (set mismatch). Client refreshes from `/api/endpoints`; the gone card disappears. Acceptable — rare race. |
| Drag ends outside any card (dragend, no drop) | `isDragging=false`, timer resumed, one `refresh()` to restore server-true state (the local splice, if any was speculatively applied, is overwritten). |
| Browser doesn't support HTML5 DnD (very old / some mobile) | Handle does nothing on `dragstart`; cards remain in server order. No error. Acceptable degradation. |
| Touch devices | Native HTML5 DnD does not fire on touch. Drag-reorder is desktop-only by design. Cards still render and update; only reorder is unavailable on touch. (Matches project scope: a monitoring dashboard.) |

### 8. Files Changed

Single file: `llm-endpoint-watchdog.py`. All changes are within the embedded `DASHBOARD_HTML` string plus one new `do_PATCH` method and one line in `do_OPTIONS`.

Specific touch points:

- **CSS block** (between lines ~320 and ~480): update `.footer` rule; add `body { padding-bottom: 50px; }` (or extend existing `body` rule at line 193); add `.card-drag-handle`, `.card.dragging`, `.card.drag-over` rules.
- **Card template** (inside `render()`, ~line 597): insert the `<span class="card-drag-handle">` as the first child of `.card`.
- **JS block** (lines 504–643): add `isDragging`, `refreshTimer`, `startRefreshTimer`, `stopRefreshTimer`; modify `refresh()` to early-return when `isDragging`; replace the bare `setInterval` call with `startRefreshTimer()`; add `onDragStart`, `onDragOver`, `onDrop`, `onDragEnd` handlers; reorder `ENDPOINTS` in `onDrop`; call `PATCH /api/endpoints/reorder`.
- **`do_PATCH` method** (new, near line 717): parse `{"order": [...]}`, validate, reorder `ENDPOINTS`, `save_endpoints()`, respond.
- **`do_OPTIONS`** (line 742): add `PATCH` to `Access-Control-Allow-Methods`.

### 9. Out of Scope

- Touch/mobile drag support (would require a polyfill or custom pointer-event logic — not worth the complexity for this dashboard).
- Drag-and-drop animation polish (SortableJS-style smooth sliding). Native DnD gives a passable ghost; further polish is a separate concern.
- Persistence of order per-user (the chosen server-shared model means all browsers see the same order, by design decision).
- Footer collapse on small screens (the fixed footer is a single short line; it fits comfortably on phone-width viewports).

### 10. Testing Approach

Manual verification (project has no test suite):

1. Start the server: `python3 llm-endpoint-watchdog.py`.
2. Open `http://localhost:9090`. Confirm footer is pinned at the bottom and does not move when adding/removing cards.
3. Add 3+ endpoints (real or fake `host:port`). Confirm all appear.
4. Drag a card by its grip handle to a new position. Confirm:
   - The card visually moves on drop.
   - The 5s auto-refresh does not interrupt the drag.
   - `watchdog_endpoints.json` shows the new order.
5. Refresh the page. Confirm the new order is preserved.
6. Open a second browser tab. Confirm it shows the same order (server-shared).
7. Drag a card and drop it outside any card (e.g. on the header). Confirm no order change, refresh resumes.
8. Stop the server (`Ctrl+C`), confirm no traceback related to the new code.
9. Inspect `watchdog_endpoints.json` — confirm shape is unchanged (still `{"endpoints": [{endpoint, name}, ...]}`).
