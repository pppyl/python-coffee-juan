(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const state = { accounts: [], results: new Map(), rendered: new Map(), ready: false, busy: false, loading: false, exporting: false, smsBusy: false, cooldown: 0, csrf: '', timer: null };
  const nativeCalls = new Map();
  window.receiveNativeResult = (id, serialized) => {
    const call = nativeCalls.get(id); if (!call) return;
    nativeCalls.delete(id); clearTimeout(call.timer);
    try { const result = JSON.parse(serialized); if (!result.ok) { const error = new Error(result.message || '操作失败'); error.result = result; call.reject(error); } else call.resolve(result); }
    catch (error) { call.reject(error); }
  };
  function nativeApi(path, options = {}) {
    return new Promise((resolve, reject) => {
      const id = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
      const timer = setTimeout(() => { nativeCalls.delete(id); reject(new Error('请求超时，请检查网络后重试。')); }, 300000);
      nativeCalls.set(id, { resolve, reject, timer });
      window.NativeApp.request(id, path, options.body || '{}');
    });
  }
  const text = (value, fallback = '') => value == null || value === '' ? fallback : String(value);
  function element(tag, className, content) {
    const result = document.createElement(tag);
    result.className = className;
    if (content != null) result.textContent = String(content);
    return result;
  }
  function message(id, content, kind = '') {
    const target = $(id);
    target.textContent = text(content);
    target.className = `message${kind ? ` ${kind}` : ''}`;
    target.hidden = !content;
  }
  async function api(path, options = {}) {
    if (window.NativeApp) return nativeApi(path, options);
    const headers = { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(state.csrf ? { 'X-CSRFToken': state.csrf } : {}) };
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 300000);
    try {
      const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store', ...options, headers: { ...headers, ...options.headers }, signal: controller.signal });
      let result;
      try { result = await response.json(); } catch { throw new Error('服务返回了无法读取的内容。'); }
      if (result.csrf_token) state.csrf = result.csrf_token;
      if (!response.ok || result.ok === false) {
        const error = new Error(text(result.message, `请求失败（${response.status}）`));
        error.result = result;
        throw error;
      }
      return result;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('请求超时，请检查网络后重试。');
      if (error instanceof TypeError) throw new Error('无法连接本地服务，请确认应用正在运行。');
      throw error;
    } finally { clearTimeout(timeout); }
  }
  function controls() {
    const locked = state.busy || state.smsBusy || state.loading || state.exporting;
    $('send-sms').disabled = !state.ready || locked || state.cooldown > 0;
    $('send-sms').textContent = state.smsBusy ? '正在发送…' : state.cooldown > 0 ? `${state.cooldown}s 后重试` : '获取验证码';
    $('login-button').disabled = !state.ready || locked;
    $('login-button').firstElementChild.textContent = state.busy ? '正在登录…' : '添加账号';
    $('phone').disabled = locked;
    $('code').disabled = locked;
    $('logout-button').disabled = locked;
    $('logout-button').hidden = !state.accounts.length;
    $('refresh-coupons').disabled = !state.accounts.length || !state.ready || locked;
    $('refresh-label').textContent = state.loading ? '正在获取…' : '一键获取全部账号';
    $('refresh-icon').classList.toggle('spinning', state.loading);
    document.querySelectorAll('[data-account-action]').forEach(button => { button.disabled = locked; });
    document.querySelectorAll('[data-export]').forEach(button => {
      button.disabled = locked || !Array.from(state.results.values()).some(result => result.ok && result.coupons.some(coupon => coupon.qr_url));
    });
  }
  function applyAccounts(accounts) {
    state.accounts = Array.isArray(accounts) ? accounts : [];
    const ids = new Set(state.accounts.map(account => account.id));
    for (const id of state.results.keys()) if (!ids.has(id)) state.results.delete(id);
    $('account-count').textContent = state.accounts.length;
    $('account-list').replaceChildren(...state.accounts.map(account => {
      const row = element('div', 'account-row');
      row.append(element('strong', '', account.phone), element('p', '', account.name));
      row.append(element('p', account.authenticated ? '' : 'account-expired', account.authenticated ? '已登录' : '登录已失效，请重新验证此号码'));
      const actions = element('div', 'account-actions');
      const fetchButton = element('button', '', account.authenticated ? '获取好礼券' : '重新登录');
      fetchButton.type = 'button'; fetchButton.dataset.accountAction = 'fetch';
      fetchButton.addEventListener('click', () => account.authenticated ? refreshAccount(account.id) : $('phone').focus());
      const remove = element('button', 'remove-account', '退出');
      remove.type = 'button'; remove.dataset.accountAction = 'remove';
      remove.addEventListener('click', () => removeAccount(account.id));
      actions.append(fetchButton, remove); row.append(actions);
      return row;
    }));
    renderGroups(); controls();
  }
  async function refreshStatus() {
    const result = await api('/api/status');
    state.ready = Boolean(result.provider?.ready);
    $('connection-pill').classList.toggle('ready', state.ready);
    $('connection-pill').classList.toggle('error', !state.ready);
    $('connection-text').textContent = state.ready ? '服务已连接' : '服务待就绪';
    $('provider-message').textContent = text(result.provider?.message);
    applyAccounts(result.accounts);
  }
  function phoneValue() {
    const phone = $('phone').value.replace(/\s+/g, '');
    if (!/^1[3-9]\d{9}$/.test(phone)) { $('phone').focus(); throw new Error('请输入正确的 11 位中国大陆手机号码。'); }
    return phone;
  }
  function cooldown(seconds) {
    clearInterval(state.timer);
    state.cooldown = Math.min(3600, Math.max(0, Number(seconds) || 60));
    controls();
    state.timer = setInterval(() => { state.cooldown = Math.max(0, state.cooldown - 1); controls(); if (!state.cooldown) clearInterval(state.timer); }, 1000);
  }
  $('send-sms').addEventListener('click', async () => {
    try {
      const phone = phoneValue(); state.smsBusy = true; controls(); message('login-message', '');
      const result = await api('/api/auth/sms', { method: 'POST', body: JSON.stringify({ phone }) });
      cooldown(result.retry_after ?? 60); message('login-message', result.message); $('code').focus();
    } catch (error) { if (error.result?.retry_after) cooldown(error.result.retry_after); message('login-message', error.message, 'error'); }
    finally { state.smsBusy = false; controls(); }
  });
  $('login-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (state.busy || !state.ready) return;
    try {
      const phone = phoneValue(), code = $('code').value.trim();
      if (!/^\d{4}$/.test(code)) throw new Error('请输入短信中的 4 位数字验证码。');
      state.busy = true; controls(); message('login-message', '');
      const result = await api('/api/auth/login', { method: 'POST', body: JSON.stringify({ phone, code }) });
      $('code').value = ''; $('phone').value = '';
      applyAccounts(result.accounts); message('login-message', result.message);
    } catch (error) { message('login-message', error.message, 'error'); }
    finally { state.busy = false; controls(); }
  });
  async function removeAccount(id) {
    state.busy = true; controls();
    try { const result = await api(`/api/accounts/${encodeURIComponent(id)}/logout`, { method: 'POST', body: '{}' }); applyAccounts(result.accounts); message('login-message', result.message); }
    catch (error) { message('login-message', error.message, 'error'); }
    finally { state.busy = false; controls(); }
  }
  $('logout-button').addEventListener('click', async () => {
    state.busy = true; controls();
    try { const result = await api('/api/auth/logout', { method: 'POST', body: '{}' }); state.results.clear(); applyAccounts(result.accounts); message('login-message', result.message); message('coupon-message', ''); }
    catch (error) { message('login-message', error.message, 'error'); }
    finally { state.busy = false; controls(); }
  });
  function couponCard(coupon, accountId) {
    const card = element('article', 'coupon-card'), top = element('div', 'coupon-card-top');
    const status = text(coupon.status, '状态待确认'), labels = { AVAILABLE: '可用', EXPIRED: '已过期', USED: '已使用' };
    const badge = element('span', 'coupon-status', labels[status] || status);
    if (/过期|已用|不可用|失效|expired|used|unavailable/i.test(status)) badge.classList.add('unavailable');
    top.append(badge, element('h3', 'coupon-title', text(coupon.title, '好礼券')),
      element('p', 'coupon-date', coupon.expires_at ? `有效期至 ${coupon.expires_at}` : '有效期请查看券面规则'));
    const bottom = element('div', 'coupon-card-bottom');
    let qrUrl = '';
    try {
      if (window.NativeApp && /^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(coupon.qr_url || '')) qrUrl = coupon.qr_url;
      else { const url = new URL(coupon.qr_url, location.origin); if (coupon.qr_url && url.origin === location.origin && url.pathname.startsWith('/api/accounts/')) qrUrl = url.href; }
    } catch { /* no QR available */ }
    if (qrUrl) {
      const qr = element('img', 'coupon-qr'), caption = element('p', 'qr-caption', '好礼券二维码');
      qr.src = qrUrl; qr.alt = `${text(coupon.title, '好礼券')}的二维码`; qr.loading = 'eager'; qr.referrerPolicy = 'no-referrer';
      qr.addEventListener('error', () => { qr.remove(); caption.className = 'coupon-no-qr'; caption.textContent = '二维码加载失败，请重新获取该账号。'; }, { once: true });
      bottom.append(qr, caption);
    } else bottom.append(element('p', 'coupon-no-qr', '此券暂未提供可展示的二维码。'));
    if (coupon.description) {
      const description = String(coupon.description).replace(/<br\s*\/?\s*>|<\/p\s*>/gi, '\n').replace(/<[^>]*>/g, '')
        .replace(/&nbsp;/gi, ' ').replace(/&amp;/gi, '&').replace(/&lt;/gi, '<').replace(/&gt;/gi, '>')
        .replace(/&quot;/gi, '"').replace(/&#39;/gi, "'").replace(/[ \t]+/g, ' ').replace(/\n\s*\n/g, '\n').trim();
      bottom.append(element('p', 'coupon-description', description));
    }
    if (qrUrl) {
      const actions = element('div', 'coupon-actions');
      for (const [kind, label] of [['copy', '复制券码'], ['download', '保存图片']]) {
        const button = element('button', '', label);
        button.type = 'button'; button.dataset.export = kind;
        button.addEventListener('click', () => exportCoupons(kind, 'all', accountId, String(coupon.id)));
        actions.append(button);
      }
      bottom.append(actions);
    }
    card.append(top, bottom); return card;
  }
  function renderGroups() {
    const groups = [], existing = new Set(state.accounts.map(account => account.id));
    let total = 0;
    for (const id of state.rendered.keys()) if (!state.results.has(id) || !existing.has(id)) state.rendered.delete(id);
    for (const [id, result] of state.results) {
      if (!existing.has(id)) continue;
      total += result.ok ? result.coupons.length : 0;
      const cached = state.rendered.get(id);
      if (cached?.result === result) { groups.push(cached.section); continue; }
      const section = element('section', 'account-group'), heading = element('div', 'account-group-heading');
      const info = element('div', ''); info.append(element('h3', '', result.account.phone), element('p', '', `${result.account.name} · ${result.ok ? result.coupons.length + ' 张好礼券' : '获取失败'}`));
      const retry = element('button', '', '刷新此账号'); retry.type = 'button'; retry.dataset.accountAction = 'fetch'; retry.addEventListener('click', () => refreshAccount(id));
      heading.append(info, retry); section.append(heading);
      if (!result.ok) section.append(element('p', 'message error', result.message));
      else if (!result.coupons.length) section.append(element('p', 'account-group-empty', '此账号暂无好礼券。'));
      else { const grid = element('div', 'coupon-grid'); grid.append(...result.coupons.map(coupon => couponCard(coupon, id))); section.append(grid); }
      state.rendered.set(id, { result, section });
      groups.push(section);
    }
    $('coupon-groups').replaceChildren(...groups);
    $('coupon-count').textContent = total; $('coupon-count').hidden = !groups.length;
    $('coupon-empty').hidden = Boolean(groups.length) || state.loading;
    $('coupon-footnote').hidden = !total;
    $('empty-title').textContent = state.accounts.length ? '账号已添加，好礼待查看' : '你的好礼，即将出现';
    $('empty-description').textContent = state.accounts.length ? '点击“一键获取全部账号”，或单独获取某个账号的好礼券。' : '分别添加账号后，可一键获取全部账号的好礼券。';
  }
  async function refreshAccount(id) {
    if (state.loading || state.busy) return;
    state.loading = true; controls(); message('coupon-message', '正在获取该账号…');
    try { const result = await api(`/api/coupons?account_id=${encodeURIComponent(id)}`); state.results.set(id, { ok: true, account: result.account, coupons: result.coupons }); message('coupon-message', result.message); }
    catch (error) { const account = state.accounts.find(item => item.id === id); if (account) state.results.set(id, { ok: false, account, coupons: [], message: error.message }); message('coupon-message', error.message, 'error'); }
    finally { state.loading = false; try { await refreshStatus(); } catch { renderGroups(); } controls(); }
  }
  $('refresh-coupons').addEventListener('click', async () => {
    if (state.loading || state.busy || !state.accounts.length) return;
    state.loading = true; controls(); $('coupon-loading').hidden = false; $('coupon-empty').hidden = true;
    state.results.clear(); renderGroups(); message('coupon-message', `正在获取 ${state.accounts.length} 个账号，请稍候…`);
    try {
      const result = await api('/api/accounts/coupons');
      for (const accountResult of result.results) state.results.set(accountResult.account.id, accountResult);
      applyAccounts(result.accounts); message('coupon-message', `${result.message} 共 ${result.total_coupons} 张券${result.failed ? `，${result.failed} 个账号失败，请查看各账号提示。` : '。'}`, result.failed ? 'info' : '');
    } catch (error) { message('coupon-message', error.message, 'error'); }
    finally { state.loading = false; $('coupon-loading').hidden = true; renderGroups(); controls(); }
  });
  async function exportCoupons(kind, category, accountId, couponId) {
    if (state.exporting) return;
    const accountIds = accountId ? [accountId] : Array.from(state.results).filter(([, result]) => result.ok).map(([id]) => id);
    const payload = { category, account_ids: accountIds, ...(couponId ? { coupon_id: couponId } : {}) };
    state.exporting = true; controls();
    try {
      if (kind === 'copy') {
        const result = await api('/api/exports/copy', { method: 'POST', body: JSON.stringify(payload) });
        try {
          if (window.NativeApp) { if (!window.NativeApp.copy(result.text)) throw new Error('无法写入剪贴板'); }
          else await navigator.clipboard.writeText(result.text);
        }
        catch {
          const area = document.createElement('textarea'); area.value = result.text; area.setAttribute('readonly', '');
          area.style.cssText = 'position:fixed;top:0;left:0;opacity:0'; document.body.append(area); area.select();
          const copied = document.execCommand('copy'); area.remove();
          if (!copied) throw new Error('系统拒绝写入剪贴板，请检查浏览器权限。');
        }
        message('coupon-message', `已复制 ${result.count} 张券的名称与二维码原文。`);
      } else {
        if (window.NativeApp) {
          const result = await nativeApi('/api/exports/download', { body: JSON.stringify(payload) });
          message('coupon-message', result.message); return;
        }
        const response = await fetch('/api/exports/download', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': state.csrf }, body: JSON.stringify(payload) });
        if (!response.ok) { const error = await response.json(); throw new Error(error.message || '下载失败'); }
        const blob = await response.blob(), url = URL.createObjectURL(blob);
        const disposition = response.headers.get('Content-Disposition') || '';
        const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i);
        const fallback = `好礼券_${category === 'all' ? '全部' : category + '元'}.${blob.type.includes('zip') ? 'zip' : 'png'}`;
        const link = document.createElement('a'); link.href = url; link.download = encoded ? decodeURIComponent(encoded[1]) : fallback;
        document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 60000);
        message('coupon-message', '已下载带券名的二维码图片，多张券将保存为 ZIP。');
      }
    } catch (error) { message('coupon-message', error.message, 'error'); }
    finally { state.exporting = false; controls(); }
  }
  for (const category of ['8', '10', '15', '20', '30', 'all']) {
    for (const kind of ['copy', 'download']) {
      const button = element('button', '', category === 'all' ? '全部' : category + '元');
      button.type = 'button'; button.dataset.export = kind; button.disabled = true;
      button.setAttribute('aria-label', `${kind === 'copy' ? '复制' : '下载'}${category === 'all' ? '全部' : category + '元'}优惠券`);
      button.addEventListener('click', () => exportCoupons(kind, category));
      $(kind + '-categories').append(button);
    }
  }
  refreshStatus().catch(error => { $('connection-text').textContent = '服务连接失败'; message('login-message', error.message, 'error'); });
})();
