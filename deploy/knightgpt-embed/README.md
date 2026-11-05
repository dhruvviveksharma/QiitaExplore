# KnightGPT embed: QiitaExplore colours inside the frame

QiitaExplore's KnightGPT tab (`qiita_explore/frontend/js/knightgpt.js`) frames
kl-remote's shared Open WebUI, `https://chat.knight-lab-dev.org/?models=knightgpt-rag`.
These two files recolour that Open WebUI, but **only inside QiitaExplore's frame**:

- `loader.js` adds `qe-embed` to `<html>` when the page's parent is
  `https://qiita-explore.knight-lab-dev.org`, and never in a normal tab.
- `custom.css` scopes every rule to `html.qe-embed`. It swaps Open WebUI's gray
  scale for QiitaExplore's warm neutrals (both light and dark mode) and puts
  QiitaExplore's accent on the send button, links and selection.

Open WebUI's HTML already loads `/static/custom.css` and `/static/loader.js`.
We serve ours at those two paths for `chat.knight-lab-dev.org`. Nothing else
about the shared instance changes, and its own settings and data are untouched.

## On kl-remote (kl-user)

### 1. Check the shared Open WebUI first

```bash
docker ps --format '{{.Names}}  {{.Image}}' | grep -i webui      # its container name + version
C=<container name>
docker exec $C sh -c 'grep -o "/static/[a-z]*\.\(css\|js\)" /app/build/index.html'
docker exec $C sh -c 'find / -path /proc -prune -o \( -name custom.css -o -name loader.js \) -print 2>/dev/null'
docker exec $C sh -c 'cat $(find / -path /proc -prune -o -name custom.css -print 2>/dev/null | head -1)'
docker exec $C sh -c "grep -oh -- '--color-gray-[0-9]*' /app/build/_app/immutable/assets/*.css | sort -u"
docker exec $C env | grep -i -E 'xframe|content_security|admin_chat'
```

Expect four things:
- **Links:** `index.html` links both `/static/custom.css` and `/static/loader.js`.
- **Existing files:** the current `custom.css` / `loader.js` are empty. Otherwise
  append their content to ours, so whatever they do keeps working.
- **Colour names:** the `--color-gray-50` … `--color-gray-950` variables exist.
  If they don't, the CSS can't take effect: this Open WebUI version names its
  colours differently, so `custom.css` needs those names instead.
- **Environment:** `XFRAME_OPTIONS` and `CONTENT_SECURITY_POLICY` are unset; a
  value there would block or limit framing. `ENABLE_ADMIN_CHAT_ACCESS` is unset
  or `true`, which is what lets admins read users' chats.

### 2. Serve the two files from Caddy

Use the block in `~/Caddyfile` that serves `chat.knight-lab-dev.org`. Put the
two `handle`s **before** the existing proxy, and wrap that proxy in a final
`handle { … }` so ordering can't matter:

```caddyfile
	# QiitaExplore embed colours (qiita-web deploy/knightgpt-embed/)
	handle /static/custom.css {
		header Cache-Control "no-cache"
		root * /home/kl-user/QittaExplore/deploy/knightgpt-embed
		rewrite * /custom.css
		file_server
	}
	handle /static/loader.js {
		header Cache-Control "no-cache"
		root * /home/kl-user/QittaExplore/deploy/knightgpt-embed
		rewrite * /loader.js
		file_server
	}
	handle {
		# … the existing reverse_proxy for chat.knight-lab-dev.org, unchanged …
	}
```

Then run:

```bash
./caddy validate --config ~/Caddyfile && pkill -f "caddy run" && \
  nohup ./caddy run --config ~/Caddyfile > /tmp/caddy.log 2>&1 &
```

The files are read straight from the QiitaExplore checkout, so a `git pull`
there updates them. They're served before oauth2-proxy, so they're public; they
hold no secrets. `no-cache` stops Cloudflare and browsers from keeping an old
copy. If you changed them before adding that header, purge both URLs in
Cloudflare once.

**If `chat.knight-lab-dev.org` doesn't go through this Caddy**, bind-mount the
two files over the paths step 1 found instead (`:ro` volumes in the Open WebUI
compose file). Then check after a restart that the served files are ours.
Some Open WebUI versions copy their static files at startup.

### 3. Check it

1. In a normal tab, open `https://chat.knight-lab-dev.org/`. Its colours must be
   unchanged.
2. Open `https://qiita-explore.knight-lab-dev.org/#/knightgpt`. The frame should
   show warm neutrals and the terracotta send button, in both of Open WebUI's
   light and dark themes.
3. In the frame's devtools, `document.documentElement.classList.contains('qe-embed')`
   should be `true`. In the normal tab it should be `false`.

### Rollback

Remove the two `handle` blocks, or the mounts, and restart Caddy or the container.
