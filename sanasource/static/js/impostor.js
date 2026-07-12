// ══════════════════════════════════════════════════════════════════
// IMPOSTEUR DES ÉMOTIONS — client (polling toutes les 2s, même pattern
// que les autres jeux multijoueurs de SANA — pas de WebSocket).
// ══════════════════════════════════════════════════════════════════

const impState = {
  code: null,
  messages: [],
  lastMsgId: 0,
  pollTimer: null,
  lastStatus: null,
};

document.addEventListener('DOMContentLoaded', function(){
  const stage = document.getElementById('impStage');
  if(!stage) return;
  impState.code = stage.dataset.roomCode;
  impPoll();
  impState.pollTimer = setInterval(impPoll, 2000);
});

async function impPoll(){
  if(!impState.code) return;
  try{
    const res = await fetch('/api/jeux/imposteur/' + impState.code + '/etat/?since=' + impState.lastMsgId, {credentials:'same-origin'});
    if(!res.ok){
      if(res.status === 403 || res.status === 404) location.href = '/dashboard/';
      return;
    }
    const data = await res.json();
    if(data.messages && data.messages.length){
      impState.messages = impState.messages.concat(data.messages);
      impState.lastMsgId = data.messages[data.messages.length - 1].id;
    }
    impState.lastStatus = data.status;
    impRender(data);
  }catch(e){
    console.error('❌ Impostor poll failed', e);
  }
}

function impPlayersStrip(data){
  return '<div class="imp-players">' + data.players.map(p => {
    const cls = ['imp-player-chip'];
    if(p.is_you) cls.push('is-you');
    let roleTag = '';
    if(p.is_impostor !== null && p.is_impostor !== undefined){
      roleTag = ' <span class="role-tag">' + (p.is_impostor ? '🎭' : '💬') + '</span>';
    }
    return '<div class="' + cls.join(' ') + '">' + escHtml(p.name) + (p.is_you ? ' (toi)' : '') + roleTag + '</div>';
  }).join('') + '</div>';
}

function impRender(data){
  const stage = document.getElementById('impStage');
  if(!stage) return;
  if(data.status === 'waiting') return impRenderWaiting(stage, data);
  if(data.status === 'discussion') return impRenderDiscussion(stage, data);
  if(data.status === 'vote') return impRenderVote(stage, data);
  if(data.status === 'finished') return impRenderFinished(stage, data);
}

function impRenderWaiting(stage, data){
  stage.innerHTML = `
    <div class="imp-card">
      <div class="imp-eyebrow">Salon en attente</div>
      <div class="imp-title">Rassemblez vos joueur·euses</div>
      <p class="imp-sub">Partage le code ci-dessous — il faut au moins 3 joueur·euses pour commencer. Un·e Imposteur·euse sera tiré·e au sort en secret, iel ne connaîtra pas l'émotion.</p>
      <div class="imp-code-box"><div class="room-code">${escHtml(impState.code)}</div></div>
      ${impPlayersStrip(data)}
      <div class="imp-actions">
        ${data.is_host
          ? `<button class="btn-imp btn-imp-primary" ${data.players.length < 3 ? 'disabled' : ''} onclick="impStartGame()">Démarrer la partie${data.players.length < 3 ? ' (min. 3)' : ''}</button>`
          : `<p class="imp-sub" style="margin:0;">En attente que l'hôte démarre la partie…</p>`}
      </div>
    </div>`;
}

async function impStartGame(){
  try{
    const res = await fetch('/api/jeux/imposteur/' + impState.code + '/demarrer/', {method:'POST', headers:{'X-CSRFToken':getCsrf()}});
    const data = await res.json().catch(()=>({}));
    if(!res.ok){ sanaToast(data.error || 'Impossible de démarrer la partie.', 'error'); return; }
    impPoll();
  }catch(e){
    console.error('❌ Start impostor game failed', e);
  }
}

function impRenderDiscussion(stage, data){
  const spotlightInner = data.is_impostor ? `
      <div class="imp-mask-icon">🎭</div>
      <div class="imp-spot-title">Tu es l'Imposteur·euse</div>
      <p class="imp-spot-text">Tu ne connais pas l'émotion secrète. Écoute les indices des autres et bluffe pour te fondre dans la discussion, sans te faire démasquer.</p>
    ` : `
      <div class="imp-mask-icon">💬</div>
      <div class="imp-spot-title">Émotion secrète</div>
      <div class="imp-secret-word">${escHtml(data.secret_emotion || '')}</div>
      <p class="imp-spot-text">Décris-la sans jamais la nommer — un·e Imposteur·euse se cache parmi vous et essaie de deviner qui vous êtes.</p>
    `;
  stage.innerHTML = `
    <div class="imp-spotlight">${spotlightInner}</div>
    <div class="imp-card">
      ${impPlayersStrip(data)}
      <div class="imp-chat" id="impChat"></div>
      <div class="imp-input-row">
        <input type="text" id="impMsgInput" placeholder="Partage un indice ou ton avis…" maxlength="200" onkeydown="if(event.key==='Enter'){event.preventDefault();impSendMessage()}">
        <button class="btn-imp btn-imp-primary" onclick="impSendMessage()">Envoyer</button>
      </div>
      ${data.is_host ? `<div class="imp-actions"><button class="btn-imp btn-imp-outline" onclick="impStartVote()">Lancer le vote</button></div>` : ''}
    </div>`;
  impRenderMessages();
}

async function impSendMessage(){
  const input = document.getElementById('impMsgInput');
  if(!input) return;
  const content = input.value.trim();
  if(!content) return;
  input.value = '';
  try{
    const res = await fetch('/api/jeux/imposteur/' + impState.code + '/message/', {
      method:'POST',
      headers:{'Content-Type':'application/json','X-CSRFToken':getCsrf()},
      body: JSON.stringify({content}),
    });
    if(!res.ok){
      const data = await res.json().catch(()=>({}));
      if(data.error) sanaToast(data.error, 'error');
    }
    impPoll();
  }catch(e){
    console.error('❌ Send impostor message failed', e);
  }
}

async function impStartVote(){
  try{
    const res = await fetch('/api/jeux/imposteur/' + impState.code + '/vote/lancer/', {method:'POST', headers:{'X-CSRFToken':getCsrf()}});
    const data = await res.json().catch(()=>({}));
    if(!res.ok){ sanaToast(data.error || 'Impossible de lancer le vote.', 'error'); return; }
    impPoll();
  }catch(e){
    console.error('❌ Start impostor vote failed', e);
  }
}

function impRenderVote(stage, data){
  const candidates = data.players.filter(p => !p.is_you);
  stage.innerHTML = `
    <div class="imp-card">
      <div class="imp-eyebrow">Vote</div>
      <div class="imp-title">Qui est l'Imposteur·euse ?</div>
      <p class="imp-vote-progress">${data.votes_cast} / ${data.player_count} ont voté</p>
      <div class="imp-vote-grid">
        ${candidates.map(p => `<div class="imp-vote-card ${data.my_vote && String(data.my_vote) === String(p.id) ? 'selected' : ''}" onclick="impCastVote(this)" data-player-id="${p.id}">${escHtml(p.name)}</div>`).join('')}
      </div>
      ${impPlayersStrip(data)}
    </div>`;
  impRenderMessages(true);
}

async function impCastVote(el){
  const targetPlayerId = el.dataset.playerId;
  if(!targetPlayerId) return;
  document.querySelectorAll('.imp-vote-card').forEach(c => c.classList.remove('selected'));
  el.classList.add('selected');
  try{
    const res = await fetch('/api/jeux/imposteur/' + impState.code + '/vote/', {
      method:'POST',
      headers:{'Content-Type':'application/json','X-CSRFToken':getCsrf()},
      body: JSON.stringify({target_player_id: targetPlayerId}),
    });
    const data = await res.json().catch(()=>({}));
    if(!res.ok){ sanaToast(data.error || 'Vote impossible.', 'error'); return; }
    impPoll();
  }catch(e){
    console.error('❌ Cast impostor vote failed', e);
  }
}

function impRenderFinished(stage, data){
  if(impState.pollTimer) clearInterval(impState.pollTimer);
  const iWon = (data.result === 'group_win' && !data.is_impostor) || (data.result === 'impostor_win' && data.is_impostor);
  const resultTitle = data.result === 'group_win' ? "Le groupe a démasqué l'Imposteur !" : "L'Imposteur s'en est sorti…";
  const resultSub = data.result === 'group_win'
    ? "Bravo, vous avez repéré qui bluffait."
    : "L'Imposteur·euse s'est fondu·e dans la discussion jusqu'au bout.";
  const sparkles = iWon ? ['✨','🌟','💫','⭐'].map((s,i) => `<span class="imp-sparkle" style="top:${10+i*18}%;left:${8+i*22}%;animation-delay:${i*0.4}s;">${s}</span>`).join('') : '';

  stage.innerHTML = `
    <div class="imp-result ${iWon ? 'win' : 'lose'}">
      ${sparkles}
      <div class="imp-result-icon">${data.result === 'group_win' ? '🔦' : '🎭'}</div>
      <div class="imp-result-title">${resultTitle}</div>
      <p class="imp-result-sub">${resultSub}</p>
      <div class="imp-result-emotion">L'émotion secrète était : ${escHtml(data.secret_emotion || '')}</div>
    </div>
    <div class="imp-coach-box">
      <div class="imp-coach-label">🌸 Coach IA</div>
      <div class="imp-coach-text ${data.ai_feedback ? '' : 'loading'}">${data.ai_feedback ? escHtml(data.ai_feedback) : 'Analyse de la partie…'}</div>
    </div>
    <div class="imp-card">
      <div class="imp-eyebrow">Qui était qui</div>
      <div class="imp-reveal-list">
        ${data.players.map(p => `<div class="imp-reveal-row ${p.is_impostor ? 'is-impostor' : ''}">
          <span>${escHtml(p.name)}${p.is_you ? ' (toi)' : ''}</span>
          <span>${p.is_impostor ? "🎭 Imposteur·euse" : '💬 Dans la confidence'}</span>
        </div>`).join('')}
      </div>
      <div class="imp-actions">
        <button class="btn-imp btn-imp-primary" onclick="location.href='/dashboard/'">Retour au tableau de bord</button>
      </div>
    </div>`;
}

function impRenderMessages(){
  const chat = document.getElementById('impChat');
  if(!chat) return;
  chat.innerHTML = impState.messages.map(m => {
    const cls = m.is_system ? 'system' : '';
    return '<div class="imp-msg ' + cls + (m.is_you ? ' is-you' : '') + '">' +
      (m.is_system ? escHtml(m.content) : '<span class="imp-msg-author">' + escHtml(m.author) + ' :</span> ' + escHtml(m.content)) +
      '</div>';
  }).join('');
  chat.scrollTop = chat.scrollHeight;
}
