# Plan: Unify chat message and composer widths

## Problem
- Chat messages are constrained to `max-width: 700px` in `.chat-messages`.
- The composer (text input), model selector, and error message also use `max-width: 700px`.
- When a message contains a `/report` study card (`m.ui.kind === 'samples_report'`), the message area switches to `max-width: 100%` via `.chat-messages-wide`, while the composer stays at 700px.
- Result: chats with study reports look full-width, while the input box remains narrow, and the answer section is wider than the text box. No other code path widens `.chat-messages`.

## Goal
- Widen the text input (composer).
- Ensure the answering section (message list) is exactly as wide as the composer.
- Keep the layout consistent for project chats, global chats, and the browse view's pinned-study composer.

## Files to Change
1. `qiita_explore/frontend/style.css` — update `.chat-messages`, `.composer`, `.composer-model`, `.composer-error`, `.composer-pins`, `.slash-menu`, `.model-picker-card`, and `.chat-messages-wide` to share a single width token; add responsive breakpoint.
2. `qiita_explore/frontend/js/app_render.js` — compute `isWide` once and apply the same width class to `.chat-messages`, `.composer`, `.composer-model`, `.composer-error`, `.composer-pins`, `.slash-menu`, and `.model-picker-card`.

## Approach
1. Introduce a CSS custom property `--chat-max-w: 860px` to align with the browse (`860px`) and merge (`860px`) panels.
2. Replace the `700px` widths in `.chat-messages`, `.composer`, `.composer-model`, `.composer-error` with `var(--chat-max-w)`. Keep `.composer-pins` and `.slash-menu` at `var(--chat-max-w)` too (intentional unification, not a regression from 720px; the 20px offset was incidental).
3. Leave `.model-picker-card` at its existing `max-width: 800px`; it is a popup/card and should not track the chat column width. Remove it from the shared-width target list.
4. Reuse the existing `.chat-messages-wide` class on `.chat-messages`. Introduce `.composer-wide` for composer-related elements. Compute `isWide` once in `app_render.js` and use it for both.
5. Add a responsive media query: below `1040px` viewport width, `.chat-messages`, `.composer`, `.composer-model`, `.composer-error`, `.composer-pins`, and `.slash-menu` switch to `max-width: 100%` so the column never overflows.
6. Leave `.chat-empty` at 520px, `.sources-bar` full-width, and browse/merge layouts untouched. The sources bar remains full-width intentionally; constraining it is out of scope.
7. Browse view uses the same `.composer` element (line 534); it will widen to 860px along with chat, which is intentional and consistent with the acceptance criteria.

## Acceptance Criteria
- [ ] Composer text box is visibly wider than before (≈860px default) in both project and global chats.
- [ ] Message bubbles/answer section aligns to the same width as the composer.
- [ ] When a `/report` study card is present, both messages and composer expand to full width together.
- [ ] When no wide content is present, messages and composer remain the same width together.
- [ ] Browse view composer widens to match the chat composer.
- [ ] No horizontal overflow at viewport widths ≥ 1040px; at smaller widths the column fills the viewport.
- [ ] No change to sidebar, top bar, browse grid, merge panels, modal, or model-picker card width.

## Verification
- Start backend: `bash qiita_explore/start_barnacle.sh` (dev port 5002).
- Open browser and test at 1280px, 1440px, and <1040px viewport widths:
  - Global chat: send a regular message → composer and messages same width (860px above 1040px, full-width below).
  - Global chat: send `/report 104` → composer and messages both expand to full width.
  - Project chat: same behavior.
  - Browse view: pin/unpin studies; composer is the same width as the chat composer.
  - Confirm no horizontal overflow.

## Risks / Notes
- Widening `.chat-messages` also widens `.msg-bubble` (max-width 82% relative to the column); this is intended.
- `.chat-empty` remains 520px, so the empty state will look narrower than populated chats; this is pre-existing and acceptable.
- `.sources-bar` remains full-width; this is pre-existing and out of scope.
- Only two files touched; changes are surgical.
- No backend changes.
- Existing 500-line file cap not impacted.
