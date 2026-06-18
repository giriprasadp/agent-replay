(() => {
  const collector = 'http://127.0.0.1:8787';
  const sessionKey = 'agentReplaySessionId:' + location.hostname + location.pathname;
  const seen = new Set();
  let sessionId = sessionStorage.getItem(sessionKey);
  if (!sessionId) {
    sessionId = 'web_' + crypto.randomUUID().replaceAll('-', '').slice(0, 12);
    sessionStorage.setItem(sessionKey, sessionId);
  }
  const rootId = sessionId + '_root';
  const startedAt = Date.now();

  function appName() {
    if (location.hostname.includes('claude')) return 'Claude web chat';
    if (location.hostname.includes('openai') || location.hostname.includes('chatgpt')) return 'ChatGPT/Codex web chat';
    return 'AI web chat';
  }

  async function postSpan(span) {
    try {
      await fetch(collector + '/api/spans', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ span })
      });
    } catch (_) {}
  }

  async function postEvent(role, content, element) {
    const normalized = content.replace(/\s+/g, ' ').trim();
    if (normalized.length < 2) return;
    const key = role + ':' + normalized;
    if (seen.has(key)) return;
    seen.add(key);
    try {
      await fetch(collector + '/api/chat-events', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          event: {
            session_id: sessionId,
            parent_id: rootId,
            role,
            content: normalized,
            turn_index: seen.size,
            metadata: {
              url: location.href,
              selector_hint: element.tagName.toLowerCase(),
              source: 'browser-extension'
            },
            start_ms: Date.now() - startedAt,
            created_at: Date.now()
          }
        })
      });
    } catch (_) {}
  }

  postSpan({
    id: rootId,
    session_id: sessionId,
    parent_id: null,
    type: 'agent',
    title: appName(),
    subtitle: 'Recorded third-party web chat page',
    status: 'ok',
    start_ms: 0,
    duration_ms: 0,
    input: { url: location.href, source: 'browser-extension' },
    output: {},
    created_at: startedAt
  });

  function inferRole(element) {
    const text = [element.getAttribute('data-message-author-role'), element.getAttribute('data-testid'), element.className, element.closest('[data-message-author-role]')?.getAttribute('data-message-author-role')]
      .filter(Boolean).join(' ').toLowerCase();
    if (text.includes('user') || text.includes('human')) return 'user';
    if (text.includes('assistant') || text.includes('claude') || text.includes('bot')) return 'assistant';
    const editable = element.closest('[contenteditable="true"], textarea');
    if (editable) return 'user';
    return 'assistant';
  }

  function scan() {
    const candidates = Array.from(document.querySelectorAll([
      '[data-message-author-role]',
      '[data-testid*="conversation-turn"]',
      '[class*="message"]',
      '[class*="Message"]',
      'article',
      'main p'
    ].join(',')));
    for (const el of candidates) {
      const text = el.innerText || el.textContent || '';
      if (text.length < 2 || text.length > 12000) continue;
      if (text.includes('Agent Replay Recorder')) continue;
      postEvent(inferRole(el), text, el);
    }
  }

  const observer = new MutationObserver(() => {
    clearTimeout(window.__agentReplayScanTimer);
    window.__agentReplayScanTimer = setTimeout(scan, 500);
  });
  observer.observe(document.documentElement, { subtree: true, childList: true, characterData: true });
  setTimeout(scan, 1000);
})();
