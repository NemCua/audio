(() => {
  'use strict';
  if (!window.desktopApp?.isElectron) return;

  let accessToken = null;
  let currentUser = null;
  window.authAccessToken = null;

  const style = document.createElement('style');
  style.textContent = `
    .desktop-auth-gate{position:fixed;inset:0;z-index:100000;background:radial-gradient(circle at 50% 10%,#24164b,#090b13 58%);display:flex;align-items:center;justify-content:center;padding:24px;font-family:inherit}
    .desktop-auth-card{width:min(420px,100%);background:#12152a;border:1px solid #292e50;border-radius:18px;padding:28px;box-shadow:0 28px 90px #000a;color:#e2e8f0}
    .desktop-auth-card h1{font-size:23px;color:#a78bfa;margin:0 0 7px}.desktop-auth-sub{font-size:12px;color:#94a3b8;line-height:1.5;margin-bottom:20px}
    .desktop-auth-tabs{display:flex;gap:8px;margin-bottom:16px}.desktop-auth-tabs button{flex:1;border:1px solid #303555;background:#171a2d;color:#94a3b8;border-radius:8px;padding:9px;cursor:pointer}.desktop-auth-tabs button.active{background:#332266;border-color:#7c3aed;color:#fff}
    .desktop-auth-card label{display:block;font-size:11px;color:#9ca3af;font-weight:700;margin:12px 0 6px}.desktop-auth-card input{width:100%;background:#090b13;border:1px solid #303555;border-radius:8px;color:#fff;padding:11px;outline:none}.desktop-auth-card input:focus{border-color:#7c3aed}
    .desktop-auth-submit{width:100%;margin-top:18px;border:0;border-radius:9px;padding:12px;background:linear-gradient(135deg,#7c3aed,#5b21b6);color:#fff;font-weight:800;cursor:pointer}.desktop-auth-submit:disabled{opacity:.5}
    .desktop-auth-error{min-height:18px;color:#f87171;font-size:12px;margin-top:10px}.desktop-account{margin-left:auto;display:flex;align-items:center;gap:8px;font-size:11px;color:#94a3b8}.desktop-account button{border:1px solid #303555;background:#171a2d;color:#cbd5e1;border-radius:7px;padding:6px 9px;cursor:pointer}
  `;
  document.head.appendChild(style);

  const gate = document.createElement('div');
  gate.className = 'desktop-auth-gate';
  gate.innerHTML = `<form class="desktop-auth-card" id="desktop-auth-form">
    <h1>Threads Video Maker</h1><div class="desktop-auth-sub">Đăng nhập để sử dụng CapCut TTS, credit và xuất video.</div>
    <div class="desktop-auth-tabs"><button type="button" data-mode="login" class="active">Đăng nhập</button><button type="button" data-mode="register">Đăng ký</button></div>
    <div id="desktop-name-wrap" style="display:none"><label>Tên hiển thị</label><input id="desktop-auth-name" autocomplete="name"/></div>
    <label>Email</label><input id="desktop-auth-email" type="email" autocomplete="email" required/>
    <label>Mật khẩu</label><input id="desktop-auth-password" type="password" autocomplete="current-password" minlength="8" required/>
    <button class="desktop-auth-submit" id="desktop-auth-submit">Đăng nhập</button><div class="desktop-auth-error" id="desktop-auth-error"></div>
  </form>`;
  document.body.appendChild(gate);
  let mode = 'login';

  function apiBase() {
    return (document.getElementById('tts-server-url')?.value || 'https://threads-api-s13z.onrender.com').replace(/\/$/, '');
  }
  async function request(path, options = {}, retry = true) {
    if (window.ensureTtsServerAwake) await window.ensureTtsServerAwake();
    const headers = new Headers(options.headers || {});
    if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`);
    const response = await fetch(apiBase() + path, { ...options, headers });
    if (response.status === 401 && retry && await refreshSession()) return request(path, options, false);
    return response;
  }
  window.authFetch = request;
  window.authHeaders = () => accessToken ? { Authorization: `Bearer ${accessToken}` } : {};

  async function jsonRequest(path, options = {}) {
    const response = await request(path, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.detail || `HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function setCurrentUser(user) {
    currentUser = user;
    renderAccount();
    window.dispatchEvent(new CustomEvent('credit-balance-changed', { detail: { balance: Number(user?.balance || 0) } }));
  }

  async function refreshUser() {
    const user = await jsonRequest('/auth/me');
    setCurrentUser(user);
    return user;
  }

  window.creditClient = Object.freeze({
    getPricing: () => jsonRequest('/auth/pricing'),
    quoteExport: input => jsonRequest('/auth/exports/quote', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(input) }),
    reserveExport: input => jsonRequest('/auth/exports/reserve', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(input) }).then(async data => { await refreshUser(); return data; }),
    consumeExport: reservationId => jsonRequest('/auth/exports/consume', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({reservation_id:reservationId}) }).then(async data => { await refreshUser(); return data; }),
    refundExport: reservationId => jsonRequest('/auth/exports/refund', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({reservation_id:reservationId}) }).then(async data => { await refreshUser(); return data; }),
    refresh: refreshUser,
    balance: () => Number(currentUser?.balance || 0),
  });

  async function acceptSession(data) {
    accessToken = data.access_token || data.token;
    window.authAccessToken = accessToken;
    currentUser = data.user;
    if (data.refresh_token) await window.desktopApp.saveRefreshToken(data.refresh_token);
    gate.style.display = 'none';
    setCurrentUser(currentUser);
  }
  async function refreshSession() {
    const refreshToken = await window.desktopApp.loadRefreshToken();
    if (!refreshToken) return false;
    try {
      const response = await fetch(apiBase() + '/auth/refresh', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({refresh_token:refreshToken}) });
      if (!response.ok) throw new Error();
      await acceptSession(await response.json()); return true;
    } catch (_error) { await window.desktopApp.clearAuth(); return false; }
  }
  function renderAccount() {
    let box = document.querySelector('.desktop-account');
    if (!box) { box=document.createElement('div');box.className='desktop-account';document.querySelector('header')?.appendChild(box); }
    box.innerHTML = `<span>${escapeHtml(currentUser?.email || '')} · ${Number(currentUser?.balance || 0)} credit</span><button id="desktop-logout">Đăng xuất</button>`;
    box.querySelector('button').onclick = logout;
  }
  function escapeHtml(value) { const el=document.createElement('span');el.textContent=String(value);return el.innerHTML; }
  async function logout() {
    try { await request('/auth/logout',{method:'POST'},false); } catch (_error) {}
    accessToken=null;window.authAccessToken=null;currentUser=null;await window.desktopApp.clearAuth();
    document.querySelector('.desktop-account')?.remove();gate.style.display='flex';
  }

  gate.querySelectorAll('[data-mode]').forEach(button => button.onclick=()=>{
    mode=button.dataset.mode;gate.querySelectorAll('[data-mode]').forEach(x=>x.classList.toggle('active',x===button));
    document.getElementById('desktop-name-wrap').style.display=mode==='register'?'block':'none';
    document.getElementById('desktop-auth-submit').textContent=mode==='register'?'Tạo tài khoản':'Đăng nhập';
    document.getElementById('desktop-auth-password').autocomplete=mode==='register'?'new-password':'current-password';
    document.getElementById('desktop-auth-error').textContent='';
  });
  document.getElementById('desktop-auth-form').onsubmit=async event=>{
    event.preventDefault();const submit=document.getElementById('desktop-auth-submit'),error=document.getElementById('desktop-auth-error');submit.disabled=true;error.textContent='';
    try {
      const body={email:document.getElementById('desktop-auth-email').value.trim(),password:document.getElementById('desktop-auth-password').value,device_name:navigator.userAgent.includes('Mac')?'macOS':'Windows'};
      if(mode==='register')body.display_name=document.getElementById('desktop-auth-name').value.trim();
      const response=await fetch(apiBase()+`/auth/${mode}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(data.detail||`HTTP ${response.status}`);await acceptSession(data);
    } catch(e){error.textContent=e.message==='Failed to fetch'?'Không kết nối được auth server. Kiểm tra server cổng 8007.':e.message;}
    finally{submit.disabled=false;}
  };

  refreshSession().then(ok=>{if(!ok)gate.style.display='flex';});
})();
