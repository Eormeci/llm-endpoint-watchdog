# Draggable Cards & Fixed Footer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the LLM Endpoint Watchdog dashboard cards reorderable via native HTML5 drag-and-drop (persisted server-side) and pin the footer to the viewport so it no longer shifts as cards are added/removed.

**Architecture:** Single-file Python script (`llm-endpoint-watchdog.py`) with an embedded HTML/CSS/JS dashboard. Backend gains one new `PATCH /api/endpoints/reorder` endpoint that reorders the shared `ENDPOINTS` list and persists it via the existing `save_endpoints()`. Frontend gains a drag handle per card, a small state machine that pauses the 5s auto-refresh during a drag, and an optimistic local reorder followed by a `PATCH` call. Footer becomes `position: fixed` with compensating `body` padding. No external dependencies introduced — project's "harici paketi gerektirmez" philosophy is preserved.

**Tech Stack:** Python 3.9+ standard library only (`http.server`, `json`); vanilla HTML5 Drag and Drop API; vanilla CSS.

## Global Constraints

- **No new dependencies.** Neither Python packages nor frontend CDNs/JS libraries. Native HTML5 DnD only.
- **Single source file.** All changes go into `llm-endpoint-watchdog.py` (embedded HTML/CSS/JS inside the `DASHBOARD_HTML` string, plus Python handler methods). Do not split the file.
- **No test suite exists.** The project has no `tests/` dir and no test runner. Verification is manual: run the server, open the dashboard, perform the listed checks. (Spec §10 explicitly endorses manual verification.) Do NOT introduce a test framework — that is out of scope.
- **On-disk JSON shape must stay unchanged:** `{"endpoints": [{"endpoint": "...", "name": "..."}, ...]}`. Only the order of list elements changes.
- **Endpoint string (`"host:port"`) is the stable identity key** for reordering (already used as the dedup key at line 75).
- **Turkish UI copy.** Error messages and button titles match the existing Turkish style (see lines 488, 528, 555 for examples). Keep it concise and lowercase-friendly like the rest.
- **Commit message style:** lowercase, e.g. `add fixed footer`, `add reorder endpoint`. Match the repo's existing terse style (see `git log --oneline`).

---

### Task 1: Pin the footer to the viewport

The footer currently sits in normal flow and shifts as cards are added/removed. This task makes it `position: fixed` and adds compensating `body` padding so the last card row is never hidden under it.

**Files:**
- Modify: `llm-endpoint-watchdog.py:193-199` (the `body` CSS rule)
- Modify: `llm-endpoint-watchdog.py:320-325` (the `.footer` CSS rule)

**Interfaces:**
- Consumes: none.
- Produces: a fixed footer. Later tasks assume the footer no longer occupies flow space — do not add margin to push content above it; `body { padding-bottom }` handles the offset.

- [ ] **Step 1: Update the `body` rule to add bottom padding**

In `llm-endpoint-watchdog.py`, the `body` rule (lines 193–199) currently is:

```css
body {
    font-family: 'SF Mono', 'Fira Code', 'JetBrains Mono', monospace;
    background: #0a0a0f;
    color: #e0e0e0;
    min-height: 100vh;
    padding: 20px;
}
```

Change the `padding` line to `padding: 20px 20px 60px 20px;` (top right bottom left — 60px bottom reserves room for the fixed footer plus breathing space). Leave all other lines untouched.

- [ ] **Step 2: Update the `.footer` rule to be fixed**

The `.footer` rule (lines 320–325) currently is:

```css
.footer {
    text-align: center;
    color: #444;
    font-size: 0.75em;
    margin-top: 30px;
}
```

Replace the entire rule with:

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
    background: #0a0a0f;
    border-top: 1px solid #1a1a1a;
    z-index: 50;
}
```

Notes: `background: #0a0a0f` matches the body so cards scrolling under the footer are cleanly obscured. `z-index: 50` sits below the modal (z-index 200, line 391) and the add-port button (z-index 100, line 366) but above cards. Remove the old `margin-top: 30px` (no longer needed since the footer is out of flow).

- [ ] **Step 3: Run the server and verify the footer is pinned**

Run: `python3 llm-endpoint-watchdog.py`
Open `http://localhost:9090`.

Expected:
- The footer line `LLM Endpoint Watchdog v1.0 · Dashboard port 9090 · Auto-refresh 5s` is glued to the bottom of the viewport.
- Scrolling or resizing the window keeps it at the bottom.
- The last card row is NOT hidden under the footer (the 60px body bottom padding reserves space).
- Add 2–3 endpoints via the `+ Endpoint Ekle` button and confirm the footer does not move when cards appear/disappear.

Stop the server with `Ctrl+C` when done.

- [ ] **Step 4: Commit**

```bash
git add llm-endpoint-watchdog.py
git commit -m "pin dashboard footer to viewport"
```

---

### Task 2: Add the `PATCH /api/endpoints/reorder` backend endpoint

Add the server-side reorder capability. This is independent of the frontend and can be verified with `curl`. The frontend in Task 4 will call this endpoint.

**Files:**
- Modify: `llm-endpoint-watchdog.py:739-744` (the `do_OPTIONS` method — add `PATCH` to allowed methods)
- Create (new method): insert a `do_PATCH` method on `DashboardHandler`, placed immediately after `do_DELETE` (after line 737, before `do_OPTIONS` at line 739).

**Interfaces:**
- Consumes: the module-level `ENDPOINTS` list, `save_endpoints()` (line 83), `parse_endpoint()` is NOT needed here (no new endpoint parsing — we only reorder existing strings).
- Produces: `PATCH /api/endpoints/reorder` accepting `{"order": ["ep1", "ep2", ...]}`, returning `{"ok": true, "endpoints": [...]}` on success or `{"ok": false, "error": "..."}` on validation failure. The response shape matches the existing `POST`/`DELETE` responses (lines 710, 732) so the frontend's `ENDPOINTS.push(...data.endpoints)` sync pattern works unchanged.

- [ ] **Step 1: Add `PATCH` to the CORS allowed methods**

In `llm-endpoint-watchdog.py`, the `do_OPTIONS` method (lines 739–744) currently sets:

```python
self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
```

Change it to:

```python
self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, PATCH, OPTIONS')
```

- [ ] **Step 2: Add the `do_PATCH` method**

Insert this method on the `DashboardHandler` class, immediately after the `do_DELETE` method ends (after line 737, before `do_OPTIONS`). It mirrors the structure of `do_POST`/`do_DELETE` (lines 682–737).

```python
    def do_PATCH(self):
        if self.path == '/api/endpoints/reorder':
            content_len = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_len).decode()
            try:
                data = json.loads(body)
                order = data.get('order')
                if not isinstance(order, list) or not order:
                    self._json_response(400, {"ok": False, "error": "order listesi gerekli"})
                    return
                current_set = {e["endpoint"] for e in ENDPOINTS}
                order_set = set(order)
                if order_set != current_set or len(order) != len(ENDPOINTS):
                    self._json_response(400, {"ok": False, "error": "Order listesi mevcut endpoint'lerle uyumsuz"})
                    return
                # Rebuild ENDPOINTS in-place, preserving each item's name
                item_by_ep = {e["endpoint"]: e for e in ENDPOINTS}
                new_list = [item_by_ep[ep] for ep in order]
                ENDPOINTS[:] = new_list
                save_endpoints(ENDPOINTS)
                print(f"   ↕ Endpoint sıralaması güncellendi: {' -> '.join(order)}")
                self._json_response(200, {"ok": True, "endpoints": ENDPOINTS})
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                self._json_response(400, {"ok": False, "error": str(e)})
        else:
            self.send_response(404)
            self.end_headers()
```

Notes on the validation logic:
- `order_set != current_set` catches missing endpoints, unknown endpoints, and (combined with the length check) duplicates. If `order` has a duplicate, `len(order) != len(ENDPOINTS)` while sets could still match, so the length check is what rejects duplicates explicitly.
- `ENDPOINTS[:] = new_list` mutates the list in place so any other references to `ENDPOINTS` see the new order (the JS-side `ENDPOINTS` is a separate copy, refreshed via API responses).
- The `print` line uses `↕` (up-down arrow) as a reorder cue in the server log, mirroring the existing `+`/`-` cues at lines 709 and 731.
- The method does NOT touch `statuses` (the per-endpoint status dict) — status is keyed by endpoint string and is order-independent. No need to reorder it.

- [ ] **Step 3: Run the server and verify reorder via curl**

Run: `python3 llm-endpoint-watchdog.py` (in a terminal; leave it running).

In a second terminal, run these checks:

**Check 1 — list current endpoints:**
```bash
curl -s http://localhost:9090/api/endpoints
```
Note the current order of the `endpoints` array. Example: `[{"endpoint":"127.0.0.1:8000","name":"Local"}]`.

**Check 2 — reorder with a valid order (reverse the current order):**
```bash
curl -s -X PATCH http://localhost:9090/api/endpoints/reorder \
  -H 'Content-Type: application/json' \
  -d "$(curl -s http://localhost:9090/api/endpoints | python3 -c 'import sys,json; print(json.dumps({"order": [e["endpoint"] for e in reversed(json.load(sys.stdin)["endpoints"])]}))')"
```
Expected: `{"ok": true, "endpoints": [...]}` with the reversed order. The server terminal should print `↕ Endpoint sıralaması güncellendi: ...`.

**Check 3 — verify persistence on disk:**
```bash
cat watchdog_endpoints.json
```
Expected: the `endpoints` array is in the new (reversed) order. Shape unchanged: `{"endpoints": [{"endpoint": "...", "name": "..."}, ...]}`.

**Check 4 — reject an invalid order (missing one endpoint):**
First get the current order, then drop the first endpoint from the list and PATCH:
```bash
curl -s -X PATCH http://localhost:9090/api/endpoints/reorder \
  -H 'Content-Type: application/json' \
  -d "$(curl -s http://localhost:9090/api/endpoints | python3 -c 'import sys,json; eps=[e["endpoint"] for e in json.load(sys.stdin)["endpoints"]]; print(json.dumps({"order": eps[1:]}))')"
```
Expected: `{"ok": false, "error": "Order listesi mevcut endpoint'lerle uyumsuz"}` and HTTP 400. (If there is only one endpoint, this check is a no-op — add a second fake endpoint like `10.0.0.99:8000` via the `+ Endpoint Ekle` UI first, then re-run.)

**Check 5 — reject an unknown endpoint in the order:**
```bash
curl -s -X PATCH http://localhost:9090/api/endpoints/reorder \
  -H 'Content-Type: application/json' \
  -d '{"order": ["9.9.9.9:9999"]}'
```
Expected: same 400 error as Check 4.

**Check 6 — reject a malformed body:**
```bash
curl -s -X PATCH http://localhost:9090/api/endpoints/reorder \
  -H 'Content-Type: application/json' \
  -d 'not json'
```
Expected: `{"ok": false, "error": "..."}` and HTTP 400 (caught by the `json.JSONDecodeError` branch).

Stop the server with `Ctrl+C` when done. Restore the original endpoint order via the UI or by editing `watchdog_endpoints.json` if you want a clean slate.

- [ ] **Step 4: Commit**

```bash
git add llm-endpoint-watchdog.py
git commit -m "add PATCH /api/endpoints/reorder endpoint"
```

---

### Task 3: Add the drag handle markup and CSS

Add a small grip handle to each card (top-left, symmetric to the existing top-right `.card-remove` button). This task adds only the visual element — no behavior yet. Task 4 wires up the drag events.

**Files:**
- Modify: `llm-endpoint-watchdog.py:457-480` (after the `.card-remove` CSS block — add `.card-drag-handle` and drag-state classes)
- Modify: `llm-endpoint-watchdog.py:597-604` (inside `render()`, the card template — insert the handle `<span>` as the first child of `.card`)

**Interfaces:**
- Consumes: none.
- Produces: a `.card-drag-handle` element per card with `draggable="true"` and an `ondragstart` attribute referencing `onDragStart(event, '<endpoint>')`. Task 4 defines `onDragStart` (and the other handlers). Until Task 4 lands, clicking/pressing the handle will throw a `ReferenceError: onDragStart is not defined` in the console — that is expected and harmless until the handlers are added. The card otherwise renders and updates normally.

- [ ] **Step 1: Add the drag handle and drag-state CSS rules**

In `llm-endpoint-watchdog.py`, locate the `.card-remove:hover` rule (ends around line 480). Immediately after it (before the closing `</style>` at line 481), insert:

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
.card.dragging { opacity: 0.4; border-style: dashed; }
.card.drag-over { border-color: #76b900; }
.card.drag-over::after {
    content: '';
    position: absolute;
    left: 0; right: 0;
    height: 3px;
    background: #76b900;
    box-shadow: 0 0 10px #76b900;
}
```

Notes:
- `position: absolute` on `.card-drag-handle` works because `.card` already has `position: relative` (line 226).
- `.card.dragging` and `.card.drag-over` are toggle classes added/removed by the JS in Task 4.
- `.card.drag-over::after` renders a highlighted insertion line on the drop-target card. The existing `.card::before` (line 233) is the top status bar and is unaffected — `::after` is a separate pseudo-element.

- [ ] **Step 2: Insert the handle `<span>` into the card template**

In `llm-endpoint-watchdog.py`, inside `render()`, the card template starts at line 597:

```javascript
        html += `
        <div class="card ${cls}">
            <button class="card-remove" onclick="removeEndpoint('${escapeAttr(ep)}')" title="Kaldir">&#10005;</button>
            <div class="port-label">
```

Insert the handle `<span>` as the first child of `.card` (before the `.card-remove` button):

```javascript
        html += `
        <div class="card ${cls}" ondragover="onDragOver(event, '${escapeAttr(ep)}')" ondrop="onDrop(event, '${escapeAttr(ep)}')">
            <span class="card-drag-handle" title="Sürükleyerek taşı" draggable="true" ondragstart="onDragStart(event, '${escapeAttr(ep)}')" ondragend="onDragEnd(event)">&#8942;&#8942;</span>
            <button class="card-remove" onclick="removeEndpoint('${escapeAttr(ep)}')" title="Kaldir">&#10005;</button>
            <div class="port-label">
```

Notes:
- `&#8942;` is the HTML entity for `⋮` (U+22EE vertical ellipsis). Doubled, it reads as a grip.
- `ondragover` and `ondrop` are placed on the card `<div>` (not the handle) so a drop anywhere on the card counts — this matches typical list-reorder UX. `ondragstart` and `ondragend` are on the handle (the draggable element).
- The endpoint string is passed single-quoted inside the already-double-... no, wait: the template uses backticks, and the attributes use single quotes. `escapeAttr(ep)` escapes `'` to `&#39;` (line 518), so the single-quoted attribute is safe. This mirrors the existing `onclick="removeEndpoint('${escapeAttr(ep)}')"` pattern at line 599.
- Because the card `<div>` now carries inline `ondragover`/`ondrop`, the `class="${cls}"` attribute moves to the same opening tag — keep the existing `${cls}` interpolation (`online`/`offline`/`error`).

- [ ] **Step 3: Run the server and verify the handle renders (expect a console ReferenceError)**

Run: `python3 llm-endpoint-watchdog.py`
Open `http://localhost:9090` and open the browser dev console (F12).

Expected:
- Each card shows a `⋮⋮` grip in the top-left corner, symmetric to the `✕` button in the top-right.
- Hovering the handle turns it green with a faint green background; the cursor becomes a grab hand.
- The dashboard still auto-refreshes every 5s and cards still update (latency, uptime, etc.).
- The browser console shows `ReferenceError: onDragStart is not defined` if you actually try to drag the handle. This is expected — Task 4 defines the handlers. Do NOT attempt to drag yet.

Stop the server with `Ctrl+C`.

- [ ] **Step 4: Commit**

```bash
git add llm-endpoint-watchdog.py
git commit -m "add drag handle to endpoint cards"
```

---

### Task 4: Wire up drag-and-drop with refresh-pause and server reorder

Implement the JS state machine: pause auto-refresh for the duration of a drag, reorder `ENDPOINTS` locally on drop for immediate feedback, then `PATCH /api/endpoints/reorder` to persist. On success, sync from the response; on failure, fall back to a fresh `GET /api/endpoints`.

**Files:**
- Modify: `llm-endpoint-watchdog.py:505` (the `const ENDPOINTS = __ENDPOINTS__;` line — add module-level state variables next to it)
- Modify: `llm-endpoint-watchdog.py:631-642` (the `refresh()` function and the trailing `setInterval`/call — add refresh-pause guard, replace the bare `setInterval` with a managed timer, cache `lastStatusData`)
- Create (new JS): insert the drag handlers (`onDragStart`, `onDragOver`, `onDrop`, `onDragEnd`) and the timer helpers (`startRefreshTimer`, `stopRefreshTimer`) into the `<script>` block, before the final `refresh();` / `setInterval` calls.

**Interfaces:**
- Consumes: `ENDPOINTS` (client-side array, line 505), `render(data)` (line 573), the `PATCH /api/endpoints/reorder` endpoint from Task 2.
- Produces: working drag-to-reorder. After this task, dragging a card by its handle moves it, persists the order to `watchdog_endpoints.json`, and survives page reload.

- [ ] **Step 1: Add module-level state variables**

In `llm-endpoint-watchdog.py`, the JS block starts at line 505 with:

```javascript
const ENDPOINTS = __ENDPOINTS__;
```

Immediately after that line, add:

```javascript
let refreshTimer = null;
let isDragging = false;
let draggedEp = null;
let dropHandled = false;
let lastStatusData = {};
```

Purpose of each:
- `refreshTimer` — holds the interval id so it can be cleared/restarted.
- `isDragging` — true during a drag (and during the in-flight PATCH that follows). `refresh()` early-returns when true so the 5s tick never clobbers an in-progress reorder.
- `draggedEp` — the endpoint string being dragged (source).
- `dropHandled` — set true in `onDrop` so `onDragEnd` (which fires after `drop`) knows whether the drop path will handle cleanup, or whether this is a cancel (dropped outside).
- `lastStatusData` — cache of the last `/api/status` payload, so `onDrop` can call `render(lastStatusData)` for immediate visual feedback without a network round-trip.

- [ ] **Step 2: Replace the bare `setInterval` with managed timer helpers, guard `refresh()`, and cache status data**

The end of the JS block (lines 631–642) currently is:

```javascript
async function refresh() {
    try {
        const resp = await fetch('/api/status');
        const data = await resp.json();
        render(data);
    } catch(e) {
        console.error('Fetch error:', e);
    }
}

refresh();
setInterval(refresh, 5000);
```

Replace the entire block (the `refresh` function definition AND the two trailing calls) with:

```javascript
async function refresh() {
    if (isDragging) return;
    try {
        const resp = await fetch('/api/status');
        const data = await resp.json();
        lastStatusData = data;
        render(data);
    } catch(e) {
        console.error('Fetch error:', e);
    }
}

function startRefreshTimer() {
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(refresh, 5000);
}

function stopRefreshTimer() {
    if (refreshTimer) {
        clearInterval(refreshTimer);
        refreshTimer = null;
    }
}

function onDragStart(event, ep) {
    isDragging = true;
    draggedEp = ep;
    dropHandled = false;
    stopRefreshTimer();
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', ep);
    const card = event.target.closest('.card');
    if (card) card.classList.add('dragging');
}

function onDragOver(event, targetEp) {
    if (!isDragging) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    document.querySelectorAll('.card.drag-over').forEach(c => c.classList.remove('drag-over'));
    if (targetEp !== draggedEp) {
        event.currentTarget.classList.add('drag-over');
    }
}

function onDrop(event, targetEp) {
    event.preventDefault();
    if (!isDragging || !draggedEp) return;
    dropHandled = true;
    const srcIdx = ENDPOINTS.findIndex(e => e.endpoint === draggedEp);
    const tgtIdx = ENDPOINTS.findIndex(e => e.endpoint === targetEp);
    document.querySelectorAll('.card.dragging, .card.drag-over').forEach(c => c.classList.remove('dragging', 'drag-over'));
    if (srcIdx === -1 || tgtIdx === -1 || srcIdx === tgtIdx) {
        finishDrag();
        return;
    }
    const [moved] = ENDPOINTS.splice(srcIdx, 1);
    ENDPOINTS.splice(tgtIdx, 0, moved);
    render(lastStatusData);
    fetch('/api/endpoints/reorder', {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({order: ENDPOINTS.map(e => e.endpoint)})
    })
        .then(resp => resp.json())
        .then(data => {
            if (data.ok) {
                ENDPOINTS.length = 0;
                ENDPOINTS.push(...data.endpoints);
            } else {
                console.warn('Reorder failed, syncing from server:', data.error);
                return fetch('/api/endpoints').then(r => r.json()).then(d => {
                    ENDPOINTS.length = 0;
                    ENDPOINTS.push(...d.endpoints);
                });
            }
        })
        .catch(e => {
            console.warn('Reorder network error, syncing from server:', e);
            return fetch('/api/endpoints').then(r => r.json()).then(d => {
                ENDPOINTS.length = 0;
                ENDPOINTS.push(...d.endpoints);
            });
        })
        .finally(() => {
            finishDrag();
            refresh();
        });
}

function onDragEnd(event) {
    if (dropHandled) return;
    document.querySelectorAll('.card.dragging, .card.drag-over').forEach(c => c.classList.remove('dragging', 'drag-over'));
    finishDrag();
    refresh();
}

function finishDrag() {
    isDragging = false;
    draggedEp = null;
    startRefreshTimer();
}

refresh();
startRefreshTimer();
```

Notes on the logic:

1. **`refresh()` guard:** `if (isDragging) return;` is the belt-and-suspenders safety net. Even though `stopRefreshTimer()` clears the interval on drag start, the guard ensures a refresh already in flight (rare race) cannot overwrite the DOM mid-drag.

2. **`lastStatusData` cache:** every successful `refresh()` updates it before `render()`. `onDrop` reuses it for the optimistic post-reorder render — no flicker, no extra request.

3. **Event ordering:** HTML5 DnD fires `drop` BEFORE `dragend` on the source element. `onDrop` sets `dropHandled = true`, so the subsequent `onDragEnd` does nothing (the PATCH's `.finally` handles cleanup). If the user drops outside any card, `drop` never fires, `dropHandled` stays false, and `onDragEnd` cleans up + triggers a refresh to restore server-true order.

4. **`onDragOver` uses `event.currentTarget`** (the card the listener is attached to), not `event.target`, because the actual target may be a child element (model tag, text). `preventDefault()` is required on `dragover` for `drop` to fire — this is the #1 DnD gotcha.

5. **`onDragOver` clears all `.drag-over` then sets the current** — this gives a clean single-target highlight as the cursor moves between cards. The `if (targetEp !== draggedEp)` guard skips highlighting the source card (it already has `.dragging`).

6. **Optimistic reorder:** `ENDPOINTS.splice(srcIdx, 1)` then `splice(tgtIdx, 0, moved)` is the standard move-element-in-array idiom. `render(lastStatusData)` immediately reflects the new order.

7. **PATCH sync:** on success, overwrite `ENDPOINTS` from the server response (single source of truth). On failure or network error, fall back to `GET /api/endpoints` to restore the authoritative order. Both paths end in `.finally(() => { finishDrag(); refresh(); })` — restart the timer and pull fresh status.

8. **`finishDrag()` is the single exit point:** sets `isDragging=false`, clears `draggedEp`, restarts the timer. Called from both the success/cancel paths. Never inline this logic — DRY.

9. **Final two lines** (`refresh(); startRefreshTimer();`) replace the old `refresh(); setInterval(refresh, 5000);`. The initial `refresh()` runs once on load; `startRefreshTimer()` kicks off the repeating 5s tick.

- [ ] **Step 3: Run the server and verify drag-to-reorder works end-to-end**

Run: `python3 llm-endpoint-watchdog.py`.
Open `http://localhost:9090`. Add at least 3 endpoints via the `+ Endpoint Ekle` button (real or fake `host:port` — they will show as offline but still render as cards).

**Check 1 — basic reorder:**
- Grab the `⋮⋮` handle of one card and drag it onto another card.
- Expected: on drop, the dragged card visually moves to the new position immediately (no spinner, no flicker).
- The browser console shows NO errors.

**Check 2 — persistence on disk:**
```bash
cat watchdog_endpoints.json
```
Expected: the `endpoints` array is in the new order. The server terminal printed `↕ Endpoint sıralaması güncellendi: <new order>`.

**Check 3 — survives page reload:**
- Press F5 / reload the dashboard tab.
- Expected: cards appear in the reordered positions.

**Check 4 — refresh pauses during drag:**
- Open the dev console and run `setInterval(()=>console.log('tick', new Date().toLocaleTimeString()), 1000)` to have a visible clock.
- Start dragging a card and HOLD it (don't drop) for 6+ seconds.
- Expected: no cards update during the hold (the latency/last-check values stay frozen). The console clock keeps ticking (proves the page isn't frozen, just our refresh is paused).
- Drop the card. Expected: refresh resumes, cards update again within ~5s.

**Check 5 — drop outside a card cancels cleanly:**
- Drag a card and release it over the header area or outside the browser window.
- Expected: no reorder happens, no console error, refresh resumes. (The `dragend` path restores state.)

**Check 6 — drop a card onto itself:**
- Drag a card and drop it onto the same card.
- Expected: no reorder, no PATCH sent (the `srcIdx === tgtIdx` early return), refresh resumes.

**Check 7 — failure recovery:**
- Stop the server (`Ctrl+C`) while the dashboard is open.
- Drag a card to a new position and drop it.
- Expected: the optimistic reorder shows briefly, then the `PATCH` fails (network error), the catch handler fetches `/api/endpoints` (also fails), the console shows `Reorder network error, syncing from server: ...`. No crash, no stuck state. Restart the server with `python3 llm-endpoint-watchdog.py` — the dashboard should resume refreshing and show the original (pre-drag) order, since the PATCH never landed.

Stop the server with `Ctrl+C` when done.

- [ ] **Step 4: Commit**

```bash
git add llm-endpoint-watchdog.py
git commit -m "add drag-to-reorder for endpoint cards"
```

---

## Final Verification Checklist

Run through these after all four tasks are complete. This is the manual acceptance test — every item should pass.

- [ ] Footer is pinned to the bottom of the viewport and does not move when cards are added/removed/resized.
- [ ] Last card row is never hidden under the footer (body padding reserves space).
- [ ] Each card shows a `⋮⋮` grip in the top-left, symmetric to the `✕` in the top-right.
- [ ] Dragging a card by its grip moves it to a new position with immediate visual feedback.
- [ ] The 5s auto-refresh is paused for the entire duration of a drag (including the in-flight PATCH).
- [ ] After a drop, the new order is saved to `watchdog_endpoints.json` (shape unchanged: `{"endpoints": [{"endpoint","name"}, ...]}`).
- [ ] Reloading the page preserves the new order.
- [ ] A second browser tab shows the same order (server-shared).
- [ ] Dropping outside any card cancels cleanly (no reorder, refresh resumes).
- [ ] Dropping a card onto itself is a no-op (no PATCH sent).
- [ ] If the server is down during a drop, the dashboard does not crash; it recovers when the server returns.
- [ ] `git log --oneline` shows four commits matching the tasks (footer pin, reorder endpoint, drag handle, drag-to-reorder).
- [ ] `git diff` of the final state touches only `llm-endpoint-watchdog.py` (and the spec/plan docs).
