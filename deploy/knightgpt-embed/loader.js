// QiitaExplore embed (see README.md here). Served on kl-remote as the shared
// Open WebUI's /static/loader.js. It does one thing: when that Open WebUI is
// framed by QiitaExplore, it adds class "qe-embed" to <html>, which custom.css
// keys on. A visit outside QiitaExplore's frame is left exactly as it was.
(function () {
  var QE_ORIGIN = 'https://qiita-explore.knight-lab-dev.org';
  try {
    if (window.top === window.self) return;            // a normal tab: never themed
    var anc = location.ancestorOrigins, on;
    if (anc && anc.length) {
      on = anc[0] === QE_ORIGIN;                         // Chrome, Safari: the parent's origin
    } else {                                             // Firefox has no ancestorOrigins:
      if ((document.referrer || '').indexOf(QE_ORIGIN + '/') === 0)
        sessionStorage.setItem('qe-embed', '1');         // the frame's first load names its parent,
      on = sessionStorage.getItem('qe-embed') === '1';   // later in-frame navigations don't
    }
    if (!on) return;
    var root = document.documentElement;
    var keep = function () { if (!root.classList.contains('qe-embed')) root.classList.add('qe-embed'); };
    keep();
    // Open WebUI rewrites <html>'s classes when its theme changes; put ours back.
    new MutationObserver(keep).observe(root, { attributes: true, attributeFilter: ['class'] });
  } catch (e) { /* storage blocked: stay unthemed */ }
})();
