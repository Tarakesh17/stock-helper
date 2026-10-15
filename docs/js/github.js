/* Saving watchlist.json to the GitHub repo with the user's own token.
   The token is only ever sent to api.github.com and only kept in this browser. */
(function () {
  "use strict";
  const SH = window.SH;
  const TOKEN_KEY = "stock-helper.github-token";
  const API = "https://api.github.com";

  SH.token = {
    get() { try { return localStorage.getItem(TOKEN_KEY) || ""; } catch (e) { return ""; } },
    set(value) { localStorage.setItem(TOKEN_KEY, value); },
    forget() { localStorage.removeItem(TOKEN_KEY); },
    looksValid: (value) => /^(github_pat_|ghp_)[A-Za-z0-9_]{20,}$/.test(value),
  };

  function base64(text) { // UTF-8 safe
    let binary = "";
    new TextEncoder().encode(text).forEach((byte) => { binary += String.fromCharCode(byte); });
    return btoa(binary);
  }

  async function call(url, token, method, body) {
    const response = await fetch(url, {
      method,
      cache: "no-store",
      headers: {
        Authorization: "Bearer " + token,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    let data = null;
    try { data = await response.json(); } catch (e) { data = null; }
    return { status: response.status, ok: response.ok, data };
  }

  function explain(status) {
    if (status === 401) return "GitHub rejected the token: it is wrong or has expired. Create a new one (steps below).";
    if (status === 403 || status === 404) {
      return "This token can't write to the repo. It needs access to the stock-helper repo with Contents: Read and write.";
    }
    return "GitHub returned an error (" + status + "). Try again in a minute.";
  }

  // Resolves to {ok: true} or {ok: false, message}.
  SH.saveWatchlist = async function (repo, symbols, token) {
    if (!/^[\w.-]+\/[\w.-]+$/.test(repo)) return { ok: false, message: "Unknown repository name." };
    const url = API + "/repos/" + repo + "/contents/watchlist.json";
    const content = base64(JSON.stringify({ symbols }, null, 2) + "\n");
    const message = ("watchlist: " + symbols.map(SH.short).join(", ")).slice(0, 72);
    for (let attempt = 0; attempt < 2; attempt++) {
      const current = await call(url, token, "GET");
      if (!current.ok) return { ok: false, message: explain(current.status) };
      const saved = await call(url, token, "PUT", { message, content, sha: current.data && current.data.sha });
      if (saved.ok) return { ok: true };
      if (saved.status !== 409 && saved.status !== 422) return { ok: false, message: explain(saved.status) };
      // 409/422: the file changed since we read it; read the new sha and try once more
    }
    return { ok: false, message: "The file changed while saving. Reload the page and try again." };
  };
})();
