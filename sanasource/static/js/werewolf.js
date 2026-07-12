// ══════════════════════════════════════════════════════════════════
// L'OMBRE PARMI LES LUMIÈRES — client (polling toutes les 2s, comme le
// reste des jeux multijoueurs de SANA — pas de WebSocket).
// ══════════════════════════════════════════════════════════════════

const wwState = {
  code: null,
  messages: [],
  lastMsgId: 0,
  pollTimer: null,
  selectedTarget: null,
  lastStatus: null,
};

document.addEventListener('DOMContentLoaded', function(){
  const stage = document.getElementById('wwStage');
  if(!stage) return;
  wwState.code = stage.dataset.roomCode;
  wwPoll();
  wwState.pollTimer = setInterval(wwPoll, 2000);
});

async function wwPoll(){
  if(!wwState.code) return;
  try{
    const res = await fetch('/api/jeux/loup/' + wwState.code + '/etat/?since=' + wwState.lastMsgId, {credentials:'same-origin'});
    if(!res.ok){
      if(res.status === 403 || res.status === 404) location.href = '/dashboard/';
      return;
    }
    const data = await res.json();
    if(data.messages && data.messages.length){
      wwState.messages = wwState.messages.concat(data.messages);
      wwState.lastMsgId = data.messages[data.messages.length - 1].id;
    }
    if(data.status !== wwState.lastStatus){
      wwState.selectedTarget = null;
      wwState.lastStatus = data.status;
    }
    wwRender(data);
  }catch(e){
    console.error('❌ Werewolf poll failed', e);
  }
}

function wwPlayersStrip(data, opts){
  opts = opts || {};
  return '<div class="ww-players">' + data.players.map(p => {
    const cls = ['ww-player-chip'];
    if(p.is_you) cls.push('is-you');
    if(!p.is_alive) cls.push('is-dead');
    let roleTag = '';
    if(p.role){
      roleTag = ' <span class="role-tag">' + (p.role === 'sombre' ? '🌑' : '💡') + '</span>';
    }
    return '<div class="' + cls.join(' ') + '">' + escHtml(p.name) + (p.is_you ? ' (toi)' : '') + roleTag + '</div>';
  }).join('') + '</div>';
}

function wwRender(data){
  const stage = document.getElementById('wwStage');
  if(!stage) return;
  if(data.status === 'waiting') return wwRenderWaiting(stage, data);
  if(data.status === 'night') return wwRenderNight(stage, data);
  if(data.status === 'day_discussion') return wwRenderDay(stage, data);
  if(data.status === 'day_vote') return wwRenderVote(stage, data);
  if(data.status === 'finished') return wwRenderFinished(stage, data);
}

function wwRenderWaiting(stage, data){
  stage.innerHTML = `
    <div class="ww-card">
      <div class="ww-eyebrow">Salon en attente</div>
      <div class="ww-title">Rassemblez vos Pensées Lumineuses</div>
      <p class="ww-sub">Partage le code ci-dessous — il faut au moins 4 joueur·euses pour commencer. Une Pensée Sombre sera tirée au sort en secret.</p>
      <div class="ww-code-box"><div class="room-code">${escHtml(wwState.code)}</div></div>
      ${wwPlayersStrip(data)}
      <div class="ww-actions">
        ${data.is_host
          ? `<button class="btn-ww btn-ww-primary" ${data.players.length < 4 ? 'disabled' : ''} onclick="wwStartGame()">Démarrer la partie${data.players.length < 4 ? ' (min. 4)' : ''}</button>`
          : `<p class="ww-sub" style="margin:0;">En attente que l'hôte démarre la partie…</p>`}
      </div>
    </div>`;
}

async function wwStartGame(){
  try{
    const res = await fetch('/api/jeux/loup/' + wwState.code + '/demarrer/', {method:'POST', headers:{'X-CSRFToken':getCsrf()}});
    const data = await res.json().catch(()=>({}));
    if(!res.ok){ sanaToast(data.error || 'Impossible de démarrer la partie.', 'error'); return; }
    wwPoll();
  }catch(e){
    console.error('❌ Start werewolf game failed', e);
  }
}

function wwStars(){
  let html = '<div class="ww-stars">';
  for(let i = 0; i < 24; i++){
    const top = Math.random() * 70;
    const left = Math.random() * 100;
    const delay = (Math.random() * 3).toFixed(2);
    html += `<span class="ww-star" style="top:${top}%;left:${left}%;animation-delay:${delay}s;"></span>`;
  }
  return html + '</div>';
}

function wwRenderNight(stage, data){
  const alivePlayers = data.players.filter(p => p.is_alive && !p.is_you);
  let actionHtml = '';
  if(data.is_night_actor){
    actionHtml = `
      <div class="ww-night-title">C'est ton tour, Pensée Sombre</div>
      <p class="ww-night-text">Choisis une lumière à éteindre en secret. Personne ne saura que c'est toi.</p>
      <div class="ww-target-grid">
        ${alivePlayers.map(p => `<div class="ww-target-card" onclick="wwSelectTarget(this)" data-player-id="${p.id}">${escHtml(p.name)}</div>`).join('')}
      </div>
      <div class="ww-actions" style="margin-top:20px;">
        <button class="btn-ww btn-ww-primary" id="wwNightConfirm" disabled onclick="wwSubmitNightAction()">Éteindre cette lumière</button>
      </div>`;
  } else {
    actionHtml = `
      <div class="ww-night-title">La nuit est tombée</div>
      <p class="ww-night-text">Les Pensées Lumineuses dorment… la Pensée Sombre choisit en secret qui s'éteindra cette nuit.</p>`;
  }
  stage.innerHTML = `
    <div class="ww-night">
      ${wwStars()}
      <div class="ww-moon"></div>
      ${actionHtml}
    </div>
    <div class="ww-card" style="margin-top:16px;">
      ${wwPlayersStrip(data)}
    </div>`;
  wwRenderMessages();
}

function wwSelectTarget(el){
  document.querySelectorAll('.ww-target-card').forEach(c => c.classList.remove('selected'));
  el.classList.add('selected');
  const btn = document.getElementById('wwNightConfirm');
  if(btn) btn.disabled = false;
}

async function wwSubmitNightAction(){
  const selected = document.querySelector('.ww-target-card.selected');
  if(!selected) return;
  const targetPlayerId = selected.dataset.playerId;
  try{
    const res = await fetch('/api/jeux/loup/' + wwState.code + '/nuit/', {
      method:'POST',
      headers:{'Content-Type':'application/json','X-CSRFToken':getCsrf()},
      body: JSON.stringify({target_player_id: targetPlayerId}),
    });
    const data = await res.json().catch(()=>({}));
    if(!res.ok){ sanaToast(data.error || 'Action impossible.', 'error'); return; }
    wwPoll();
  }catch(e){
    console.error('❌ Night action failed', e);
  }
}

function wwRenderDay(stage, data){
  stage.innerHTML = `
    <div class="ww-day">
      <div class="ww-sun-banner">
        <div class="ww-prompt-label">☀️ Discussion — manche ${data.round_number}</div>
        <div class="ww-prompt-text">${escHtml(data.current_prompt || '')}</div>
      </div>
      <div class="ww-card" style="margin-bottom:16px;">
        ${wwPlayersStrip(data)}
        <div class="ww-chat" id="wwChat"></div>
        <div class="ww-input-row">
          <input type="text" id="wwMsgInput" placeholder="Partage ta réponse ou ton avis…" maxlength="200" onkeydown="if(event.key==='Enter'){event.preventDefault();wwSendMessage()}">
          <button class="btn-ww btn-ww-primary" onclick="wwSendMessage()">Envoyer</button>
        </div>
        ${data.is_host ? `<div class="ww-actions"><button class="btn-ww btn-ww-outline" onclick="wwStartVote()">Lancer le vote</button></div>` : ''}
      </div>
    </div>`;
  wwRenderMessages();
}

async function wwSendMessage(){
  const input = document.getElementById('wwMsgInput');
  if(!input) return;
  const content = input.value.trim();
  if(!content) return;
  input.value = '';
  try{
    const res = await fetch('/api/jeux/loup/' + wwState.code + '/message/', {
      method:'POST',
      headers:{'Content-Type':'application/json','X-CSRFToken':getCsrf()},
      body: JSON.stringify({content}),
    });
    if(!res.ok){
      const data = await res.json().catch(()=>({}));
      if(data.error) sanaToast(data.error, 'error');
    }
    wwPoll();
  }catch(e){
    console.error('❌ Send werewolf message failed', e);
  }
}

async function wwStartVote(){
  try{
    const res = await fetch('/api/jeux/loup/' + wwState.code + '/vote/lancer/', {method:'POST', headers:{'X-CSRFToken':getCsrf()}});
    const data = await res.json().catch(()=>({}));
    if(!res.ok){ sanaToast(data.error || 'Impossible de lancer le vote.', 'error'); return; }
    wwPoll();
  }catch(e){
    console.error('❌ Start werewolf vote failed', e);
  }
}

function wwRenderVote(stage, data){
  const candidates = data.players.filter(p => p.is_alive && !p.is_you);
  const myAlive = data.players.find(p => p.is_you);
  stage.innerHTML = `
    <div class="ww-card">
      <div class="ww-eyebrow">Vote — manche ${data.round_number}</div>
      <div class="ww-title">Qui est la Pensée Sombre ?</div>
      <p class="ww-vote-progress">${data.votes_cast} / ${data.alive_count} ont voté</p>
      ${myAlive && myAlive.is_alive ? `
        <div class="ww-vote-grid">
          ${candidates.map(p => `<div class="ww-vote-card ${data.my_vote && String(data.my_vote) === String(p.id) ? 'selected' : ''}" onclick="wwCastVote(this)" data-player-id="${p.id}">${escHtml(p.name)}</div>`).join('')}
        </div>` : `<p class="ww-sub">Ta lumière s'est éteinte — tu observes le vote en silence.</p>`}
      ${wwPlayersStrip(data)}
    </div>`;
  wwRenderMessages(true);
}

async function wwCastVote(el){
  const targetPlayerId = el.dataset.playerId;
  if(!targetPlayerId) return;
  document.querySelectorAll('.ww-vote-card').forEach(c => c.classList.remove('selected'));
  el.classList.add('selected');
  try{
    const res = await fetch('/api/jeux/loup/' + wwState.code + '/vote/', {
      method:'POST',
      headers:{'Content-Type':'application/json','X-CSRFToken':getCsrf()},
      body: JSON.stringify({target_player_id: targetPlayerId}),
    });
    const data = await res.json().catch(()=>({}));
    if(!res.ok){ sanaToast(data.error || 'Vote impossible.', 'error'); return; }
    wwPoll();
  }catch(e){
    console.error('❌ Cast werewolf vote failed', e);
  }
}

function wwRenderFinished(stage, data){
  if(wwState.pollTimer) clearInterval(wwState.pollTimer);
  const myPlayer = data.players.find(p => p.is_you);
  const iWon = myPlayer && (
    (data.result === 'lumieres_win' && myPlayer.role === 'lumiere') ||
    (data.result === 'sombre_win' && myPlayer.role === 'sombre')
  );
  const resultTitle = data.result === 'lumieres_win' ? 'Les Lumières ont gagné !' : 'La Pensée Sombre a gagné…';
  const resultSub = data.result === 'lumieres_win'
    ? 'La Pensée Sombre a été démasquée par le groupe.'
    : "La Pensée Sombre s'est fondue jusqu'au bout parmi les Lumières.";
  const sparkles = iWon ? ['✨','🌟','💫','⭐'].map((s,i) => `<span class="ww-sparkle" style="top:${10+i*18}%;left:${8+i*22}%;animation-delay:${i*0.4}s;">${s}</span>`).join('') : '';

  stage.innerHTML = `
    <div class="ww-result ${iWon ? 'win' : 'lose'}">
      ${sparkles}
      <div class="ww-result-icon">${data.result === 'lumieres_win' ? '💡' : '🌑'}</div>
      <div class="ww-result-title">${resultTitle}</div>
      <p class="ww-result-sub">${resultSub}</p>
    </div>
    <div class="ww-coach-box">
      <div class="ww-coach-label">🌸 Coach IA</div>
      <div class="ww-coach-text ${data.ai_feedback ? '' : 'loading'}">${data.ai_feedback ? escHtml(data.ai_feedback) : 'Analyse de la partie…'}</div>
    </div>
    <div class="ww-card">
      <div class="ww-eyebrow">Qui était qui</div>
      <div class="ww-reveal-list">
        ${data.players.map(p => `<div class="ww-reveal-row ${p.role === 'sombre' ? 'is-sombre' : ''}">
          <span>${escHtml(p.name)}${p.is_you ? ' (toi)' : ''}</span>
          <span>${p.role === 'sombre' ? '🌑 Pensée Sombre' : '💡 Pensée Lumineuse'}</span>
        </div>`).join('')}
      </div>
      <div class="ww-actions">
        <button class="btn-ww btn-ww-primary" onclick="location.href='/dashboard/'">Retour au tableau de bord</button>
      </div>
    </div>`;
}

function wwRenderMessages(compact){
  const chat = document.getElementById('wwChat');
  if(!chat) return;
  chat.innerHTML = wwState.messages.map(m => {
    const cls = m.is_system ? 'system' : '';
    return '<div class="ww-msg ' + cls + (m.is_you ? ' is-you' : '') + '">' +
      (m.is_system ? escHtml(m.content) : '<span class="ww-msg-author">' + escHtml(m.author) + ' :</span> ' + escHtml(m.content)) +
      '</div>';
  }).join('');
  chat.scrollTop = chat.scrollHeight;
}
