(function(){
  "use strict";
  window.officerLoginPage=function(){document.body.classList.remove("public-mode");
    return `<section style="min-height:100vh;background:linear-gradient(135deg,#071a36,#173a79);display:grid;place-items:center;padding:24px">
      <main class="card" style="width:min(470px,100%);padding:34px;box-shadow:0 28px 70px rgba(0,0,0,.28)">
        <div style="display:flex;align-items:center;gap:11px;justify-content:center">
          <div class="brandmark" style="background:#294ea6;color:#fff">✓</div>
          <div class="brandtext"><strong>VeriMetrix Officer Access</strong><small>AUTHORISED LMO WORKSPACE</small></div>
        </div>
        <div style="margin-top:26px;text-align:center">
          <span class="pill">SECURE OFFICER SIGN-IN</span>
          <h1 style="margin:11px 0 6px">Authorised LMO Officer Portal</h1>
          <p class="small muted">Only provisioned Legal Metrology Officer accounts can enter. No self-registration is available.</p>
        </div>
        <form id="officerLoginForm" style="margin-top:24px">
          <label><span class="label">Officer email</span><input id="officerLoginEmail" class="input" type="email" autocomplete="username" required placeholder="officer@example.gov.in"></label>
          <label style="display:block;margin-top:14px"><span class="label">Password</span><input id="officerLoginPassword" class="input" type="password" autocomplete="current-password" required placeholder="Issued by the system administrator"></label>
          <div id="officerLoginMsg" class="small" style="min-height:20px;margin-top:13px"></div>
          <button id="officerLoginBtn" class="btn primary" style="width:100%;margin-top:8px">Sign in securely</button>
        </form>
        <div class="alert notice" style="margin-top:18px"><b>Access boundary</b><div class="small" style="margin-top:5px">Officer access is jurisdiction-scoped. An authenticated officer can review and approve applications assigned to their state and district. Public certificate status is updated only after a successful backend approval.</div></div>
        <div style="display:flex;justify-content:space-between;gap:10px;margin-top:18px"><button type="button" class="btn outline" onclick="go('home')">← Public portal</button><span class="small muted" style="align-self:center">8-hour max · 30-minute inactivity timeout</span></div>
      </main>
    </section>`;
  };
  window.bindOfficerLogin=function(){
    const form=document.getElementById("officerLoginForm");
    if(form)form.addEventListener("submit",e=>{e.preventDefault();window.submitOfficerLogin()});
  };
  window.submitOfficerLogin=async function(){
    const email=document.getElementById("officerLoginEmail")?.value.trim().toLowerCase();
    const password=document.getElementById("officerLoginPassword")?.value||"";
    const btn=document.getElementById("officerLoginBtn"),msg=document.getElementById("officerLoginMsg");
    if(!email||!password)return;
    btn.disabled=true;msg.textContent="Authenticating…";msg.style.color="";
    try{
      const r=await fetch(API+"/officer/login",{method:"POST",headers:{"content-type":"application/json","X-Vmx-CSRF":state?.officerCsrf||""},body:JSON.stringify({email,password})});
      const j=await r.json();
      if(!r.ok)throw Error(j.error||"Officer sign-in failed");
      state.officerCsrf=String(j.csrf_token||"");
      state.officerSessionExpiresAt=j.session_expires_at||null;
      if(!state.officerCsrf)throw Error("Officer session security token was not issued. Please sign in again.");
      msg.textContent="Authentication successful. Opening secure workspace…";msg.style.color="var(--green)";
      setTimeout(()=>go("officer"),120);
    }catch(e){
      msg.textContent=e.message||"Officer sign-in failed.";
      msg.style.color="var(--red)";
    }finally{btn.disabled=false}
  };
  window.officerLogout=async function(){
    try{await fetch(API+"/officer/logout",{method:"POST"})}catch(_){}
    if(state.officerLiveTimer){clearInterval(state.officerLiveTimer);state.officerLiveTimer=null}
    state.officer=null;
    go("officer-login");
  };
})();