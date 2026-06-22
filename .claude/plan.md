# Plan: Redesign Frontend per Reference Images + Integrate Logo

## Goal
Update the Qiita Explorer web UI to match the two reference images and integrate the supplied logo into the sidebar, top bar, and app chrome. Keep all existing functionality intact.

## Reference Design Summary
- **Palette:** near-white page (`#F8F9F9`), light-gray sidebar (`#F8F8F8`), teal primary (`#447C8A`), dark-teal user bubbles (`#1B2F38`), muted text (`#5A5A5A`), borders (`#E8E8E8`).
- **Typography:** all sans-serif (system/Inter), clean hierarchy, no serif titles.
- **Shell:** top bar (white, ~56 px) + left sidebar (248 px, light gray) + main content area.
- **Sidebar:** logo at top, primary nav links (Home / Browse / Chat / Merges / Global Chats), Workspaces section with collapsible projects, active item highlighted with teal left border.
- **Browse:** white hero card containing title, search bar, filter chips; below it a grid of study cards with teal left accent bar, metadata chips, and action buttons.
- **Chat:** thread rail on the left inside main (or full-width conversation), user messages as dark rounded bubbles, assistant messages as plain prose with embedded tool-result cards, starter chip row, bottom composer.
- **Logo:** abstract geometric mark with pale cyan-teal background, dark-teal shapes, lime/medium greens. Use as sidebar/app icon; derive accent colors from it.

## Files to Change
1. `ezredbiom/frontend/style.css` — new tokens and restyled components.
2. `ezredbiom/frontend/js/app_render.js` — restructure sidebar, top bar, browse, chat layout.
3. `ezredbiom/frontend/index.html` — cache-bust CSS/JS, ensure logo file reference.
4. `ezredbiom/frontend/logo.png` — replace with supplied logo (already present as new file).

## Approach
1. Replace CSS tokens with the new palette and spacing.
2. Rebuild the sidebar into a nav rail with logo, primary links, workspaces list, global chats, and a footer.
3. Simplify top bar: page title/context centered/left, merge toggle, theme toggle, user avatar placeholder.
4. Restyle browse: hero card with search and chips, grid cards with left accent bar and cleaner metadata.
5. Restyle chat: dark user bubbles, clean assistant prose, tool cards with light borders, starter chips.
6. Integrate logo image as `logo.png` and reference it from sidebar + top bar.
7. Keep dark-mode support by deriving dark variants of new tokens.
8. Verify file sizes stay under 500-line cap; if `style.css` grows too much, consider splitting later (ticket TKT-011 already tracks oversized files).

## Verification
- Start backend: `bash ezredbiom/start_barnacle.sh` (currently dev port 5002, do not revert as part of this task).
- Open browser, test:
  - Sidebar navigation and active states
  - Browse search + study cards
  - Project/global chat list, new chat, send message
  - Dark/light toggle
  - Logo visible and crisp
  - Merge panel opens/closes

## Risks / Notes
- `app_render.js` and `style.css` will grow; this is a UI redesign so a substantial diff is expected. Existing TKT-011 already covers splitting oversized files later.
- No backend changes needed.
- Keep `api-base` at current port; do not touch port TODOs.
