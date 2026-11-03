let eventSource = null;
let currentStatus = {};

function getClientId() {
  let id = localStorage.getItem('spoofer_client_id');
  if (!id) {
    id = 'c_' + Math.random().toString(36).substring(2, 11) + '_' + Date.now();
    localStorage.setItem('spoofer_client_id', id);
  }
  return id;
}

const CLIENT_ID = getClientId();

function getClientAuth() {
  return {
    token: localStorage.getItem('discord_token') || '',
    cookie: localStorage.getItem('discord_cookie') || '',
    fingerprint: localStorage.getItem('discord_fingerprint') || ''
  };
}

function maskValue(str) {
  if (!str) return '';
  if (str.length <= 8) return '***';
  return str.slice(0, 4) + '...' + str.slice(-4);
}

function updateAuthButtons() {
  const auth = getClientAuth();
  const tokenBtn = document.getElementById('btnConfigToken');
  const cookieBtn = document.getElementById('btnConfigCookie');
  if (tokenBtn) {
    tokenBtn.textContent = auth.token ? `TOKEN: ${maskValue(auth.token)}` : 'DISCORD TOKEN';
  }
  if (cookieBtn) {
    cookieBtn.textContent = auth.cookie ? `COOKIE: ${maskValue(auth.cookie)}` : 'COOKIE DISCORD';
  }
}

function initApp() {
  updateAuthButtons();
  connectSSE();
  setupEventListeners();
  loadGamesList();
}

function connectSSE() {
  if (eventSource) {
    eventSource.close();
  }
  eventSource = new EventSource('/api/events?clientId=' + encodeURIComponent(CLIENT_ID));

  eventSource.onmessage = function (e) {
    try {
      const payload = JSON.parse(e.data);
      if (payload.type === 'init') {
        updateStatus(payload.status);
        renderLogs(payload.logs);
      } else if (payload.type === 'status') {
        updateStatus(payload.data);
      } else if (payload.type === 'log') {
        appendLog(payload.data);
      }
    } catch (err) {
      console.error('SSE JSON parse error:', err);
    }
  };

  eventSource.onerror = function () {
    console.warn('SSE connection interrupted, retrying in 3s...');
    eventSource.close();
    setTimeout(connectSSE, 3000);
  };
}

const elRunBtn = document.getElementById('btnRunBot');
const elIndicatorDot = document.getElementById('indicatorDot');
const elIndicatorText = document.getElementById('indicatorText');
const elSelectMode = document.getElementById('selectMode');
const elLogWindow = document.getElementById('terminalLogs');

function updateStatus(status) {
  currentStatus = status;

  elIndicatorText.textContent = status.status_text;
  if (status.is_running) {
    elIndicatorDot.className = 'indicator-dot running';
    elRunBtn.textContent = 'STOP BOT';
    elRunBtn.classList.add('btn-stop');
  } else {
    elIndicatorDot.className = 'indicator-dot' + (status.status_text.includes('error') ? ' stopped' : '');
    elRunBtn.textContent = 'RUN BOT';
    elRunBtn.classList.remove('btn-stop');
  }

  document.getElementById('statGamesCount').textContent = `${status.games_loaded} games`;
  document.getElementById('statClaimedHours').textContent = `${status.total_hours_claimed}h claimed`;
  document.getElementById('statTotalRuns').textContent = `${status.total_games_claimed} runs`;

  const prog = status.progress || {};
  if (prog.total > 0 && status.is_running) {
    document.getElementById('progressContainer').style.display = 'flex';
    const percent = Math.min(100, Math.round((prog.current / prog.total) * 100));
    document.getElementById('progressBarInner').style.width = percent + '%';
    document.getElementById('progressRatio').textContent = `${prog.current} / ${prog.total} games`;
    document.getElementById('progressPercent').textContent = `${percent}% (${prog.claimed_hours}h)`;
  } else if (!status.is_running && prog.current > 0) {
    document.getElementById('progressContainer').style.display = 'flex';
    document.getElementById('progressBarInner').style.width = '100%';
    document.getElementById('progressRatio').textContent = `${prog.total} / ${prog.total} games`;
    document.getElementById('progressPercent').textContent = '100% Hoàn tất';
  } else {
    document.getElementById('progressContainer').style.display = 'none';
  }

  updateAuthButtons();
  const userEl = document.getElementById('headerUserLabel');
  if (userEl && status.user_id) {
    userEl.textContent = `UID: ${status.user_id}`;
  }
}

function renderLogs(logs) {
  elLogWindow.innerHTML = '';
  logs.forEach(appendLog);
}

function appendLog(log) {
  const row = document.createElement('div');
  row.className = 'log-entry';

  const levelClass = `log-level-${log.level || 'info'}`;
  const tagClass = `tag-${(log.tag || 'SYSTEM').toLowerCase()}`;

  row.innerHTML = `
    <span class="log-time">[${log.timestamp}]</span>
    <span class="log-tag ${tagClass}">${log.tag}</span>
    <span class="log-msg ${levelClass}">${escapeHtml(log.message)}</span>
  `;

  elLogWindow.appendChild(row);
  elLogWindow.scrollTop = elLogWindow.scrollHeight;
}

function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function setupEventListeners() {
  const modeWrapper = document.getElementById('modeSelectWrapper');
  const modeTrigger = document.getElementById('modeSelectTrigger');
  const modeMenu = document.getElementById('modeSelectMenu');
  const triggerBadge = document.getElementById('triggerBadge');
  const triggerLabel = document.getElementById('triggerLabel');
  const hiddenModeInput = document.getElementById('selectMode');

  if (modeTrigger && modeWrapper && modeMenu) {
    modeTrigger.addEventListener('click', (e) => {
      e.stopPropagation();
      const isOpen = modeWrapper.classList.toggle('open');
      modeTrigger.setAttribute('aria-expanded', isOpen);
    });

    modeMenu.querySelectorAll('.custom-select-option').forEach(option => {
      option.addEventListener('click', (e) => {
        e.stopPropagation();
        const val = option.dataset.value;
        const badgeText = option.dataset.badge;
        const badgeClass = option.dataset.badgeClass;
        const labelText = option.dataset.label;

        modeMenu.querySelectorAll('.custom-select-option').forEach(opt => {
          opt.classList.remove('selected');
          opt.setAttribute('aria-selected', 'false');
        });
        option.classList.add('selected');
        option.setAttribute('aria-selected', 'true');

        if (triggerBadge) {
          triggerBadge.textContent = badgeText;
          triggerBadge.className = `mode-badge ${badgeClass}`;
        }
        if (triggerLabel) {
          triggerLabel.textContent = labelText;
        }
        if (hiddenModeInput) {
          hiddenModeInput.value = val;
        }

        modeWrapper.classList.remove('open');
        modeTrigger.setAttribute('aria-expanded', 'false');
      });
    });

    document.addEventListener('click', (e) => {
      if (!modeWrapper.contains(e.target)) {
        modeWrapper.classList.remove('open');
        modeTrigger.setAttribute('aria-expanded', 'false');
      }
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && modeWrapper.classList.contains('open')) {
        modeWrapper.classList.remove('open');
        modeTrigger.setAttribute('aria-expanded', 'false');
      }
    });
  }

  elRunBtn.addEventListener('click', async () => {
    if (currentStatus.is_running) {
      await fetch('/api/stop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ clientId: CLIENT_ID })
      });
    } else {
      const auth = getClientAuth();
      if (!auth.token.trim()) {
        const input = document.getElementById('inputToken');
        if (input) input.value = '';
        openModal('modalToken');
        if (input) setTimeout(() => input.focus(), 50);
        return;
      }

      if (!auth.cookie.trim()) {
        const input = document.getElementById('inputCookie');
        if (input) input.value = '';
        openModal('modalCookie');
        if (input) setTimeout(() => input.focus(), 50);
        return;
      }

      const hours = 10;
      const count = 'all';
      const mode = (hiddenModeInput && hiddenModeInput.value) || 'both';

      const res = await fetch('/api/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          clientId: CLIENT_ID,
          token: auth.token.trim(),
          cookie: auth.cookie.trim(),
          fingerprint: auth.fingerprint.trim(),
          hours,
          count,
          mode
        })
      });
      const data = await res.json();
      if (!data.success) {
        if (data.missing === 'token') {
          openModal('modalToken');
          const input = document.getElementById('inputToken');
          if (input) setTimeout(() => input.focus(), 50);
        } else if (data.missing === 'cookie') {
          openModal('modalCookie');
          const input = document.getElementById('inputCookie');
          if (input) setTimeout(() => input.focus(), 50);
        } else {
          alert(data.message);
        }
      }
    }
  });

  const btnClear = document.getElementById('btnClearLogs');
  if (btnClear) {
    btnClear.addEventListener('click', async () => {
      await fetch('/api/logs/clear', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ clientId: CLIENT_ID })
      });
      elLogWindow.innerHTML = '';
    });
  }

  const btnRefresh = document.getElementById('btnRefreshAuth');
  if (btnRefresh) {
    btnRefresh.addEventListener('click', async () => {
      const auth = getClientAuth();
      if (!auth.token.trim()) {
        openModal('modalToken');
        return;
      }
      const res = await fetch('/api/refresh_auth', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ clientId: CLIENT_ID, token: auth.token.trim(), cookie: auth.cookie.trim() })
      });
      const data = await res.json();
      if (data.success) {
        alert('Làm mới Analytics Token thành công!');
      } else {
        alert('Thất bại: ' + data.message);
      }
    });
  }

  const btnQuick = document.getElementById('btnQuickStart');
  if (btnQuick) {
    btnQuick.addEventListener('click', () => {
      openModal('quickStartModal');
    });
  }

  const btnTok = document.getElementById('btnConfigToken');
  if (btnTok) {
    btnTok.addEventListener('click', () => {
      const auth = getClientAuth();
      const input = document.getElementById('inputToken');
      if (input) input.value = auth.token;
      openModal('modalToken');
      if (input) setTimeout(() => input.focus(), 50);
    });
  }

  const btnCook = document.getElementById('btnConfigCookie');
  if (btnCook) {
    btnCook.addEventListener('click', () => {
      const auth = getClientAuth();
      const input = document.getElementById('inputCookie');
      if (input) input.value = auth.cookie;
      openModal('modalCookie');
      if (input) setTimeout(() => input.focus(), 50);
    });
  }

  const btnFp = document.getElementById('btnConfigFingerprint');
  if (btnFp) {
    btnFp.addEventListener('click', () => {
      const auth = getClientAuth();
      const input = document.getElementById('inputFingerprint');
      if (input) input.value = auth.fingerprint;
      openModal('modalFingerprint');
      if (input) setTimeout(() => input.focus(), 50);
    });
  }

  const btnSaveToken = document.getElementById('btnSaveToken');
  if (btnSaveToken) {
    btnSaveToken.addEventListener('click', () => {
      const token = document.getElementById('inputToken').value.trim();
      localStorage.setItem('discord_token', token);
      updateAuthButtons();
      closeAllModals();
    });
  }

  const btnSaveCookie = document.getElementById('btnSaveCookie');
  if (btnSaveCookie) {
    btnSaveCookie.addEventListener('click', () => {
      const cookie = document.getElementById('inputCookie').value.trim();
      localStorage.setItem('discord_cookie', cookie);
      updateAuthButtons();
      closeAllModals();
    });
  }

  const btnSaveFingerprint = document.getElementById('btnSaveFingerprint');
  if (btnSaveFingerprint) {
    btnSaveFingerprint.addEventListener('click', () => {
      const fingerprint = document.getElementById('inputFingerprint').value.trim();
      localStorage.setItem('discord_fingerprint', fingerprint);
      closeAllModals();
    });
  }

  const btnTheme = document.getElementById('btnThemeToggle');
  if (btnTheme) {
    btnTheme.addEventListener('click', () => {
      const isDark = document.body.getAttribute('data-theme') === 'dark';
      document.body.setAttribute('data-theme', isDark ? 'light' : 'dark');
    });
  }

  document.querySelectorAll('.modal-close, .modal-close-btn, #btnUnderstandGuide').forEach(btn => {
    btn.addEventListener('click', closeAllModals);
  });

  document.querySelectorAll('.modal-overlay').forEach(overlay => {
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) closeAllModals();
    });
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeAllModals();
  });
}

function closeAllModals() {
  document.querySelectorAll('.modal-overlay.active').forEach(m => m.classList.remove('active'));
}

function openModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.add('active');
}

async function loadGamesList() {
  try {
    const res = await fetch('/api/games');
    const data = await res.json();
    if (data.total) {
      const el = document.getElementById('statGamesCount');
      if (el) el.textContent = `${data.total} games`;
    }
  } catch (e) {
    console.error('Failed to load games list preview', e);
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initApp);
} else {
  initApp();
}
