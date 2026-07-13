// ── SECTION NAV ──
const sectionMap = {
  accueil:'Accueil', chat:'Chat SANA', humeur:'Mon Humeur',
  communaute:'Communauté', groupes:'Groupes', avis:'Avis', messages:'Messages Privés',
  ressources:'Blog & Ressources', pro:'Mon Professionnel', badges:'Mes Badges',
  profil:'Mon Profil', parcours:'Mon Parcours', sensibilisation:'Sensibilisation'
};

function showSection(id, trigger){
  document.querySelectorAll('.section').forEach(s=>s.classList.remove('active'));
  document.getElementById('sec-'+id).classList.add('active');

  document.querySelectorAll('.nav-item').forEach(n=>n.classList.remove('active'));
  document.querySelectorAll('.mn-item').forEach(n=>n.classList.remove('active'));

  if(trigger){
    trigger.classList.add('active');
    // Sync sidebar + mobile nav
    const idx = [...document.querySelectorAll('.nav-item')].indexOf(trigger);
    const mIdx = [...document.querySelectorAll('.mn-item')].indexOf(trigger);
    if(idx>=0) document.querySelectorAll('.mn-item')[Math.min(idx,document.querySelectorAll('.mn-item').length-1)]?.classList.add('active');
    if(mIdx>=0) document.querySelectorAll('.nav-item')[Math.min(mIdx,document.querySelectorAll('.nav-item').length-1)]?.classList.add('active');
  }

  document.getElementById('topbarTitle').textContent = sectionMap[id] || '';
  if(id==='chat') initDashChat();
  if(id==='messages') loadDMConversationsSection();
  closeSidebar();
}

// ── SIDEBAR MOBILE ──
function toggleSidebar(){
  document.getElementById('sidebar').classList.toggle('open');
  document.getElementById('sidOverlay').classList.toggle('open');
}
function closeSidebar(){
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('sidOverlay').classList.remove('open');
}

// ── DATE ──
(function(){
  const d=new Date();
  const days=['Dimanche','Lundi','Mardi','Mercredi','Jeudi','Vendredi','Samedi'];
  const months=['Janvier','Février','Mars','Avril','Mai','Juin','Juillet','Août','Septembre','Octobre','Novembre','Décembre'];
  document.getElementById('wcDate').textContent=d.getDate();
  document.getElementById('wcDay').textContent=days[d.getDay()]+' · '+months[d.getMonth()]+' '+d.getFullYear();
})();

// ── MOOD HOME (overridden below by saveMoodNow) ──
function selectMoodHome(btn, moodVal){
  btn.closest('div').querySelectorAll('.mood-emoji-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  dashSelectedMood = moodVal;
}
function selectMoodChat(btn, emoji){
  btn.parentElement.querySelectorAll('button').forEach(b=>b.style.opacity='0.5');
  btn.style.opacity='1';btn.style.transform='scale(1.3)';
  setTimeout(()=>{btn.parentElement.querySelectorAll('button').forEach(b=>{b.style.opacity='';b.style.transform='';});}, 800);
}

// ── AVIS ──
let reviewRating = 5;
(function initReviewStars(){
  document.querySelectorAll('.review-star').forEach(s=>{
    if(parseInt(s.dataset.value)<=reviewRating) s.classList.add('active');
  });
})();
function setReviewRating(value){
  reviewRating = value;
  document.querySelectorAll('.review-star').forEach(s=>{
    s.classList.toggle('active', parseInt(s.dataset.value)<=value);
  });
}
async function submitReview(){
  const textarea = document.getElementById('reviewContent');
  const msg = document.getElementById('reviewMsg');
  const content = textarea.value.trim();
  if(content.length<10){ msg.textContent = 'Ton avis est un peu court, dis-en un peu plus 🌸'; return; }
  msg.textContent = 'Envoi…';
  try{
    const res = await fetch('/api/avis/', {
      method:'POST',
      headers:{'Content-Type':'application/json','X-CSRFToken':getCsrf()},
      body: JSON.stringify({content, rating: reviewRating}),
    });
    const data = await res.json().catch(()=>({}));
    msg.textContent = data.message || data.error || 'Une erreur est survenue.';
    if(res.ok) textarea.value = '';
  }catch(e){
    console.error('❌ Review submit failed', e);
    msg.textContent = 'Une erreur est survenue, réessaie plus tard.';
  }
}

// ── LIKE (legacy, kept for compatibility) ──
function toggleLike(btn){
  btn.classList.toggle('liked');
  const sp=btn.querySelector('span');
  sp.textContent=btn.classList.contains('liked')?parseInt(sp.textContent)+1:parseInt(sp.textContent)-1;
}

// ── CHATBOT DASHBOARD (multi-conversation) ──
const CHAT_URL='/api/chat/';
const CONVERSATIONS_URL='/api/conversations/';
let conversations=[];
let activeConversationId=null;
let dashReady=false;
let dashSending=false;

function getCsrf(){
  const meta = document.querySelector('meta[name="csrf-token"]');
  if(meta) return meta.content;
  const inp=document.querySelector('[name=csrfmiddlewaretoken]');
  if(inp) return inp.value;
  const v=document.cookie.match('(^|;)\\s*csrftoken\\s*=\\s*([^;]+)');
  return v?v.pop():'';
}

async function chatApi(url, options={}){
  const res = await fetch(url, {
    method: options.method || 'GET',
    headers: {'Content-Type':'application/json','X-CSRFToken':getCsrf()},
    body: options.body,
  });
  const data = await res.json().catch(()=>({}));
  if(!res.ok) throw Object.assign(new Error(data.error || 'Erreur serveur'), {status:res.status});
  return data;
}

function escapeHtml(str){
  const d=document.createElement('div');
  d.textContent = str==null ? '' : String(str);
  return d.innerHTML;
}

async function initDashChat(){
  if(dashReady) return;
  dashReady=true;
  try{
    const data = await chatApi(CONVERSATIONS_URL);
    conversations = data.conversations || [];
    renderConvList();
    if(conversations.length) await selectConversation(conversations[0].id);
  }catch(e){
    console.error('❌ Failed to load conversations', e);
    document.getElementById('chatConvList').innerHTML = '<div class="chat-sidebar-empty">Impossible de charger les conversations.</div>';
  }
}

function renderConvList(){
  const list = document.getElementById('chatConvList');
  if(!list) return;
  if(!conversations.length){
    list.innerHTML = '<div class="chat-sidebar-empty">Aucune conversation</div>';
    return;
  }
  list.innerHTML = conversations.map(c => `
    <div class="conv-item ${c.id===activeConversationId?'active':''}" onclick="selectConversation(${c.id})">
      <span class="conv-title">${escapeHtml(c.title)}</span>
      <div class="conv-actions">
        <button class="conv-action-btn" title="Renommer" onclick="event.stopPropagation();renameConversation(${c.id})">✎</button>
        <button class="conv-action-btn" title="Supprimer" onclick="event.stopPropagation();deleteConversation(${c.id})">🗑</button>
      </div>
    </div>
  `).join('');
}

async function selectConversation(id){
  if(activeConversationId===id) { closeChatSidebar(); return; }
  activeConversationId = id;
  renderConvList();
  closeChatSidebar();
  const container=document.getElementById('dashChatMessages');
  container.innerHTML = '<div class="chat-sidebar-empty">Chargement…</div>';
  try{
    const data = await chatApi(`${CONVERSATIONS_URL}${id}/`);
    container.innerHTML = '';
    (data.messages || []).forEach(m => {
      const role = m.role==='assistant'?'bot':'user';
      if(m.image_url || m.voice_note_url){
        appendDashAttachmentMsg(role, {imageUrl:m.image_url, voiceUrl:m.voice_note_url});
      } else {
        appendDashMsg(role, m.content);
      }
    });
  }catch(e){
    console.error('❌ Failed to load conversation messages', e);
    container.innerHTML = '';
    appendDashMsg('bot', 'Impossible de charger cette conversation pour le moment.');
  }
}

async function createNewConversation(){
  try{
    const conv = await chatApi(CONVERSATIONS_URL, {method:'POST'});
    conversations.unshift(conv);
    renderConvList();
    await selectConversation(conv.id);
  }catch(e){
    console.error('❌ Failed to create conversation', e);
  }
}

async function renameConversation(id){
  const conv = conversations.find(c=>c.id===id);
  const nextTitle = prompt('Renommer la conversation :', conv ? conv.title : '');
  if(nextTitle===null) return;
  const title = nextTitle.trim();
  if(!title) return;
  try{
    const updated = await chatApi(`${CONVERSATIONS_URL}${id}/`, {method:'PATCH', body:JSON.stringify({title})});
    const idx = conversations.findIndex(c=>c.id===id);
    if(idx>=0) conversations[idx] = updated;
    renderConvList();
  }catch(e){
    console.error('❌ Failed to rename conversation', e);
  }
}

async function deleteConversation(id){
  if(!confirm('Supprimer définitivement cette conversation ?')) return;
  try{
    await chatApi(`${CONVERSATIONS_URL}${id}/`, {method:'DELETE'});
    conversations = conversations.filter(c=>c.id!==id);
    if(activeConversationId===id){
      activeConversationId=null;
      if(conversations.length){
        await selectConversation(conversations[0].id);
      }else{
        const conv = await chatApi(CONVERSATIONS_URL, {method:'POST'});
        conversations.unshift(conv);
        await selectConversation(conv.id);
      }
    }
    renderConvList();
  }catch(e){
    console.error('❌ Failed to delete conversation', e);
  }
}

async function dashSendMsg(){
  if(dashSending || !activeConversationId) return;
  const inp=document.getElementById('dashChatInput');
  const txt=inp.value.trim();
  if(!txt) return;
  inp.value='';inp.focus();dashSending=true;

  appendDashMsg('user',txt);

  const typing=document.getElementById('dashTyping');
  typing.classList.add('show');
  scrollDashChat();

  try{
    const data = await chatApi(CHAT_URL, {
      method:'POST',
      body: JSON.stringify({conversation_id: activeConversationId, message: txt}),
    });
    typing.classList.remove('show');
    if(data.reply){
      appendDashMsg('bot',data.reply);
      const idx = conversations.findIndex(c=>c.id===activeConversationId);
      if(idx>=0){
        conversations[idx] = {...conversations[idx], title: data.conversation_title, updated_at: data.updated_at};
        const [conv] = conversations.splice(idx,1);
        conversations.unshift(conv);
        renderConvList();
      }
    } else {
      appendDashMsg('bot', data.error || 'Le modèle n’a pas pu répondre pour l’instant.');
    }
  }catch(e){
    console.error('❌ Dashboard chat request failed', e);
    typing.classList.remove('show');
    appendDashMsg('bot',"Je suis là, même si la connexion a vacillé.");
  }
  dashSending=false;
}

function appendDashMsg(role,text){
  const w=document.createElement('div');
  w.className='cmsg '+role;
  const html=escapeHtml(text).replace(/\n\n/g,'<br><br>').replace(/\n/g,'<br>');
  if(role==='bot'){
    w.innerHTML=`<div class="cmsg-av">🌸</div><div class="cmsg-bubble">${html}</div>`;
  }else{
    w.innerHTML=`<div class="cmsg-bubble">${html}</div>`;
  }
  document.getElementById('dashChatMessages').appendChild(w);
  scrollDashChat();
}

function scrollDashChat(){
  const c=document.getElementById('dashChatMessages');
  requestAnimationFrame(()=>{c.scrollTop=c.scrollHeight;});
}

function appendDashAttachmentMsg(role, {imageUrl, voiceUrl}){
  const w=document.createElement('div');
  w.className='cmsg '+role;
  let inner='';
  if(imageUrl) inner += `<img class="cmsg-img" src="${imageUrl}" alt="Photo envoyée">`;
  if(voiceUrl) inner += `<audio class="cmsg-audio" controls src="${voiceUrl}"></audio>`;
  if(role==='bot'){
    w.innerHTML=`<div class="cmsg-av">🌸</div><div class="cmsg-bubble">${inner}</div>`;
  }else{
    w.innerHTML=`<div class="cmsg-bubble">${inner}</div>`;
  }
  document.getElementById('dashChatMessages').appendChild(w);
  scrollDashChat();
}

function fileToBase64(file){
  return new Promise((resolve, reject)=>{
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(',')[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

async function dashSendAttachment({image, audio, previewUrl}){
  if(dashSending || !activeConversationId) return;
  dashSending = true;

  appendDashAttachmentMsg('user', {imageUrl: image ? previewUrl : null, voiceUrl: audio ? previewUrl : null});

  const typing=document.getElementById('dashTyping');
  typing.classList.add('show');
  scrollDashChat();

  try{
    const payload = {conversation_id: activeConversationId, message: ''};
    if(image) payload.image = image;
    if(audio) payload.audio = audio;
    const data = await chatApi(CHAT_URL, {method:'POST', body: JSON.stringify(payload)});
    typing.classList.remove('show');
    if(data.reply){
      appendDashMsg('bot', data.reply);
      const idx = conversations.findIndex(c=>c.id===activeConversationId);
      if(idx>=0){
        conversations[idx] = {...conversations[idx], title: data.conversation_title, updated_at: data.updated_at};
        const [conv] = conversations.splice(idx,1);
        conversations.unshift(conv);
        renderConvList();
      }
    } else {
      appendDashMsg('bot', data.error || 'Le modèle n’a pas pu répondre pour l’instant.');
    }
  }catch(e){
    console.error('❌ Dashboard chat attachment request failed', e);
    typing.classList.remove('show');
    appendDashMsg('bot',"Je suis là, même si la connexion a vacillé.");
  }
  dashSending=false;
}

async function dashHandleImagePick(event){
  const file = event.target.files[0];
  event.target.value = '';
  if(!file || !activeConversationId) return;
  if(!file.type.startsWith('image/')){ sanaToast('Format d’image non supporté'); return; }
  if(file.size > 10*1024*1024){ sanaToast('Image trop lourde (max 10 Mo)'); return; }
  const data = await fileToBase64(file);
  const previewUrl = URL.createObjectURL(file);
  await dashSendAttachment({image:{data, mime:file.type}, previewUrl});
}

let dashMediaRecorder = null;
let dashRecordedChunks = [];

async function dashToggleVoiceRecording(){
  const btn = document.getElementById('dashVoiceBtn');
  if(dashMediaRecorder && dashMediaRecorder.state === 'recording'){
    dashMediaRecorder.stop();
    return;
  }
  if(!activeConversationId) return;
  try{
    const stream = await navigator.mediaDevices.getUserMedia({audio:true});
    dashRecordedChunks = [];
    const mimeType = (window.MediaRecorder && MediaRecorder.isTypeSupported('audio/webm')) ? 'audio/webm' : '';
    dashMediaRecorder = mimeType ? new MediaRecorder(stream, {mimeType}) : new MediaRecorder(stream);
    dashMediaRecorder.ondataavailable = e => { if(e.data.size > 0) dashRecordedChunks.push(e.data); };
    dashMediaRecorder.onstop = async () => {
      btn.classList.remove('recording');
      stream.getTracks().forEach(t => t.stop());
      const blob = new Blob(dashRecordedChunks, {type: dashMediaRecorder.mimeType || 'audio/webm'});
      if(blob.size === 0) return;
      const data = await fileToBase64(blob);
      const previewUrl = URL.createObjectURL(blob);
      await dashSendAttachment({audio:{data, mime: blob.type}, previewUrl});
    };
    dashMediaRecorder.start();
    btn.classList.add('recording');
  }catch(e){
    console.error('❌ Microphone access failed', e);
    sanaToast('Impossible d’accéder au micro');
  }
}

function toggleChatSidebar(){
  document.getElementById('chatSidebar').classList.toggle('open');
  document.getElementById('chatSidebarOverlay').classList.toggle('open');
}
function closeChatSidebar(){
  if(window.innerWidth>900) return;
  document.getElementById('chatSidebar').classList.remove('open');
  document.getElementById('chatSidebarOverlay').classList.remove('open');
}

// ── SOS MODAL ──
function openSOS(){
  const m=document.getElementById('sosModal');
  m.style.display='flex';
  document.body.style.overflow='hidden';
}
function closeSOS(){
  document.getElementById('sosModal').style.display='none';
  document.body.style.overflow='';
}
document.getElementById('sosModal').addEventListener('click',function(e){
  if(e.target===this) closeSOS();
});

// ── EDIT PROFILE MODAL ──
function openEditProfile(){
  const m=document.getElementById('editProfileModal');
  m.style.display='flex';
  document.body.style.overflow='hidden';
}
function closeEditProfile(){
  document.getElementById('editProfileModal').style.display='none';
  document.body.style.overflow='';
}
document.getElementById('editProfileModal').addEventListener('click',function(e){
  if(e.target===this) closeEditProfile();
});
async function submitEditProfile(){
  const msg = document.getElementById('editProfileMsg');
  const usernameAnon = document.getElementById('editUsernameAnon').value.trim();
  if(!usernameAnon){ msg.textContent = 'Le nom anonyme est requis.'; return; }
  msg.textContent = 'Enregistrement…';
  try{
    const res = await fetch('/api/profile/', {
      method:'POST',
      headers:{'Content-Type':'application/json','X-CSRFToken':getCsrf()},
      body: JSON.stringify({
        first_name: document.getElementById('editFirstName').value.trim(),
        last_name: document.getElementById('editLastName').value.trim(),
        username_anonyme: usernameAnon,
        age: document.getElementById('editAge').value.trim(),
        ville: document.getElementById('editVille').value.trim(),
        genre: document.getElementById('editGenre').value,
        situation: document.getElementById('editSituation').value,
        theme_couleur: document.getElementById('editTheme').value,
        objectif_principal: document.getElementById('editObjectif').value.trim(),
        payment_method: document.getElementById('editPaymentMethod').value,
        payment_info: document.getElementById('editPaymentInfo').value.trim(),
      }),
    });
    const data = await res.json().catch(()=>({}));
    if(res.ok){
      msg.textContent = data.message || 'Profil mis à jour !';
      setTimeout(() => window.location.reload(), 700);
    } else {
      msg.textContent = data.error || 'Une erreur est survenue.';
    }
  }catch(e){
    console.error('❌ Profile update failed', e);
    msg.textContent = 'Une erreur est survenue, réessaie plus tard.';
  }
}

// ── SUPPORT PAYMENT MODAL ──
function closeSupportPayment(){
  document.getElementById('supportPaymentModal').style.display='none';
  document.body.style.overflow='';
}
document.getElementById('supportPaymentModal').addEventListener('click',function(e){
  if(e.target===this) closeSupportPayment();
});
async function openSupportPayment(postId){
  const body = document.getElementById('supportPaymentBody');
  const modal = document.getElementById('supportPaymentModal');
  body.innerHTML = '<p style="font-size:.82rem;color:var(--txt-s);">Chargement…</p>';
  modal.style.display='flex';
  document.body.style.overflow='hidden';
  try{
    const res = await fetch('/api/communaute/' + postId + '/paiement/', {credentials:'same-origin'});
    const data = await res.json().catch(()=>({}));
    if(!res.ok){
      body.innerHTML = '<p style="font-size:.82rem;color:var(--txt-s);">' + escHtml(data.error || 'Moyen de paiement indisponible.') + '</p>';
      return;
    }
    body.innerHTML =
      '<p style="font-size:.78rem;color:var(--txt-s);line-height:1.6;margin-bottom:14px;">SANA ne collecte jamais cet argent — ces coordonnées appartiennent directement à <strong>' + escHtml(data.anon) + '</strong>. Donne uniquement si tu es en confiance, et signale ce post si quelque chose te semble suspect.</p>' +
      '<div style="background:var(--blush);border-radius:14px;padding:16px;text-align:center;">' +
        '<div style="font-size:.7rem;text-transform:uppercase;letter-spacing:1px;color:var(--txt-s);margin-bottom:4px;">' + escHtml(data.payment_method) + '</div>' +
        '<div style="font-size:1.1rem;font-weight:600;color:var(--p950);">' + escHtml(data.payment_info) + '</div>' +
      '</div>';
  }catch(e){
    console.error('❌ Payment info load failed', e);
    body.innerHTML = '<p style="font-size:.82rem;color:var(--txt-s);">Une erreur est survenue, réessaie plus tard.</p>';
  }
}

// ── REPORT POST MODAL (partagée entre Communauté et Blog) ──
let reportPostId = null;
let reportTargetType = 'communaute'; // 'communaute' | 'blog'
function closeReportPost(){
  document.getElementById('reportPostModal').style.display='none';
  document.body.style.overflow='';
}
document.getElementById('reportPostModal').addEventListener('click',function(e){
  if(e.target===this) closeReportPost();
});
function openReportPost(postId){
  reportTargetType = 'communaute';
  reportPostId = postId;
  document.getElementById('reportPostMsg').textContent = '';
  document.getElementById('reportPostDetails').value = '';
  document.getElementById('reportPostModal').style.display='flex';
  document.body.style.overflow='hidden';
}
function openReportBlogPost(postId){
  reportTargetType = 'blog';
  reportPostId = postId;
  document.getElementById('reportPostMsg').textContent = '';
  document.getElementById('reportPostDetails').value = '';
  document.getElementById('reportPostModal').style.display='flex';
  document.body.style.overflow='hidden';
}
async function submitReportPost(){
  const reason = document.getElementById('reportPostReason').value;
  const details = document.getElementById('reportPostDetails').value.trim();
  const msg = document.getElementById('reportPostMsg');
  msg.textContent = 'Envoi…';
  const endpoint = reportTargetType === 'blog'
    ? '/api/blog/' + reportPostId + '/signaler/'
    : '/api/communaute/' + reportPostId + '/signaler/';
  try{
    const res = await fetch(endpoint, {
      method:'POST',
      headers:{'Content-Type':'application/json','X-CSRFToken':getCsrf()},
      body: JSON.stringify({reason, details}),
    });
    const data = await res.json().catch(()=>({}));
    msg.textContent = data.message || data.error || 'Une erreur est survenue.';
    if(res.ok){
      const cardId = reportTargetType === 'blog' ? 'blog-post-' + reportPostId : 'post-' + reportPostId;
      const card = document.getElementById(cardId);
      if(card) setTimeout(() => card.remove(), 1200);
      setTimeout(closeReportPost, 1200);
    }
  }catch(e){
    console.error('❌ Report failed', e);
    msg.textContent = 'Une erreur est survenue, réessaie plus tard.';
  }
}

// ── SENSIBILISATION: SHARE ──
function shareSensib(text){
  if(navigator.share){
    navigator.share({title:'SANA', text, url:window.location.origin}).catch(()=>{});
  } else {
    const url = 'https://wa.me/?text=' + encodeURIComponent(text + ' ' + window.location.origin);
    window.open(url, '_blank');
  }
}
function buildShareRow(text){
  return '<div class="share-row">' +
    '<button class="share-btn" onclick="shareSensib(' + JSON.stringify(text).replace(/"/g,'&quot;') + ')">📤 Partager</button>' +
  '</div>';
}

// ── SENSIBILISATION: AUTO-ÉVALUATION (PHQ-9 / GAD-7) ──
const SQ_ANSWER_CHOICES = [
  [0,'Pas du tout'], [1,'Plusieurs jours'], [2,'Plus de la moitié des jours'], [3,'Presque tous les jours'],
];
let screeningData = null;
let screeningAnswers = [];
let screeningTool = null;

function closeScreening(){
  document.getElementById('screeningModal').style.display='none';
  document.body.style.overflow='';
}
document.getElementById('screeningModal').addEventListener('click',function(e){
  if(e.target===this) closeScreening();
});

function openScreening(tool){
  if(!screeningData){
    screeningData = JSON.parse(document.getElementById('screeningToolsData').textContent);
  }
  screeningTool = tool;
  const def = screeningData[tool];
  screeningAnswers = new Array(def.questions.length).fill(null);
  renderScreeningForm(def);
  document.getElementById('screeningModal').style.display='flex';
  document.body.style.overflow='hidden';
}

function renderScreeningForm(def){
  let html = '<div class="sq-title">' + escHtml(def.title) + '</div>';
  html += '<p class="sq-intro">' + escHtml(def.intro) + '</p>';
  def.questions.forEach((q, qi) => {
    html += '<div class="sq-block"><div class="sq-question">' + (qi+1) + '. ' + escHtml(q) + '</div><div class="sq-options">';
    SQ_ANSWER_CHOICES.forEach(([val, label]) => {
      html += '<div class="sq-option" data-q="' + qi + '" data-v="' + val + '" onclick="selectScreeningAnswer(' + qi + ',' + val + ',this)">' + escHtml(label) + '</div>';
    });
    html += '</div></div>';
  });
  html += '<p id="screeningMsg" style="font-size:.76rem;color:var(--txt-s);margin-bottom:10px;min-height:1em;"></p>';
  html += '<button class="btn-sm btn-primary-sm" style="width:100%;" onclick="submitScreening()">Voir mon résultat</button>';
  document.getElementById('screeningBody').innerHTML = html;
}

function selectScreeningAnswer(qi, val, el){
  screeningAnswers[qi] = val;
  document.querySelectorAll('.sq-option[data-q="' + qi + '"]').forEach(o => o.classList.remove('selected'));
  el.classList.add('selected');
}

async function submitScreening(){
  const msg = document.getElementById('screeningMsg');
  if(screeningAnswers.some(a => a === null)){
    msg.textContent = 'Réponds à toutes les questions pour voir ton résultat.';
    return;
  }
  msg.textContent = 'Calcul…';
  try{
    const res = await fetch('/api/sensibilisation/screening/', {
      method:'POST',
      headers:{'Content-Type':'application/json','X-CSRFToken':getCsrf()},
      body: JSON.stringify({tool: screeningTool, answers: screeningAnswers}),
    });
    const data = await res.json();
    if(!res.ok){ msg.textContent = data.error || 'Une erreur est survenue.'; return; }
    renderScreeningResult(data);
  }catch(e){
    console.error('❌ Screening submit failed', e);
    msg.textContent = 'Une erreur est survenue, réessaie plus tard.';
  }
}

function renderScreeningResult(data){
  let html = '<div class="sq-title">Ton résultat</div>';
  html += '<div class="sq-result-band"><div class="sq-result-score">' + data.score + '/' + data.max_score + '</div><div class="sq-result-label">' + escHtml(data.band) + '</div></div>';
  if(data.flagged){
    html += '<div class="sq-crisis-box">' +
      '<div style="font-size:.85rem;font-weight:600;color:#c0392b;margin-bottom:6px;">Une de tes réponses nous inquiète</div>' +
      '<p style="font-size:.78rem;color:#8a4a4a;line-height:1.6;margin-bottom:10px;">Si tu as des pensées de te faire du mal, tu n\'es pas seul·e — parle-en tout de suite.</p>' +
      '<a href="tel:185" style="display:inline-block;padding:10px 20px;background:#c0392b;color:white;border-radius:100px;text-decoration:none;font-size:.8rem;font-weight:600;">📞 Appeler le 185</a>' +
    '</div>';
  }
  html += '<p style="font-size:.74rem;color:var(--txt-s);line-height:1.6;margin-bottom:14px;">Ceci n\'est pas un diagnostic médical — seulement un outil d\'auto-évaluation. Si ce résultat te préoccupe, en parler à un professionnel est la meilleure prochaine étape.</p>';
  html += buildShareRow('J\'ai fait le test de sensibilisation santé mentale sur SANA — et toi ?');
  html += '<button class="btn-sm btn-outline-sm" style="width:100%;margin-top:8px;" onclick="closeScreening()">Fermer</button>';
  document.getElementById('screeningBody').innerHTML = html;
}

// ── SENSIBILISATION: QUIZ ──
let quizData = null;
let quizAnswers = [];

function closeQuiz(){
  document.getElementById('quizModal').style.display='none';
  document.body.style.overflow='';
}
document.getElementById('quizModal').addEventListener('click',function(e){
  if(e.target===this) closeQuiz();
});

function openQuiz(){
  if(!quizData){
    quizData = JSON.parse(document.getElementById('quizQuestionsData').textContent);
  }
  quizAnswers = new Array(quizData.length).fill(null);
  renderQuizForm();
  document.getElementById('quizModal').style.display='flex';
  document.body.style.overflow='hidden';
}

function renderQuizForm(){
  let html = '<div class="sq-title">Teste tes connaissances</div>';
  quizData.forEach((q, qi) => {
    html += '<div class="sq-block"><div class="sq-question">' + (qi+1) + '. ' + escHtml(q.question) + '</div><div class="sq-options">';
    q.choices.forEach((choice, ci) => {
      html += '<div class="sq-option" data-q="' + qi + '" data-v="' + ci + '" onclick="selectQuizAnswer(' + qi + ',' + ci + ',this)">' + escHtml(choice) + '</div>';
    });
    html += '</div></div>';
  });
  html += '<p id="quizMsg" style="font-size:.76rem;color:var(--txt-s);margin-bottom:10px;min-height:1em;"></p>';
  html += '<button class="btn-sm btn-primary-sm" style="width:100%;" onclick="submitQuizAnswers()">Voir mes résultats</button>';
  document.getElementById('quizBody').innerHTML = html;
}

function selectQuizAnswer(qi, ci, el){
  quizAnswers[qi] = ci;
  document.querySelectorAll('.sq-option[data-q="' + qi + '"]').forEach(o => o.classList.remove('selected'));
  el.classList.add('selected');
}

async function submitQuizAnswers(){
  const msg = document.getElementById('quizMsg');
  if(quizAnswers.some(a => a === null)){
    msg.textContent = 'Réponds à toutes les questions pour voir ton score.';
    return;
  }
  msg.textContent = 'Calcul…';
  try{
    const res = await fetch('/api/sensibilisation/quiz/', {
      method:'POST',
      headers:{'Content-Type':'application/json','X-CSRFToken':getCsrf()},
      body: JSON.stringify({answers: quizAnswers}),
    });
    const data = await res.json();
    if(!res.ok){ msg.textContent = data.error || 'Une erreur est survenue.'; return; }
    renderQuizResult(data);
  }catch(e){
    console.error('❌ Quiz submit failed', e);
    msg.textContent = 'Une erreur est survenue, réessaie plus tard.';
  }
}

function renderQuizResult(data){
  let html = '<div class="sq-title">Résultat : ' + data.score + '/' + data.total + '</div>';
  data.results.forEach((r, qi) => {
    const cls = r.is_correct ? 'sq-right' : 'sq-wrong';
    const prefix = r.is_correct ? '✓ ' : '✗ ';
    html += '<div class="sq-explanation ' + cls + '">' + prefix + (qi+1) + '. ' + escHtml(r.explanation) + '</div>';
  });
  html += buildShareRow('J\'ai fait ' + data.score + '/' + data.total + ' au quiz santé mentale de SANA — et toi ?');
  html += '<button class="btn-sm btn-outline-sm" style="width:100%;margin-top:8px;" onclick="closeQuiz()">Fermer</button>';
  document.getElementById('quizBody').innerHTML = html;
}

// ── DÉFI DU JOUR ──
function closeDailyChallenge(){
  document.getElementById('dailyChallengeModal').style.display='none';
  document.body.style.overflow='';
}
document.getElementById('dailyChallengeModal').addEventListener('click',function(e){
  if(e.target===this) closeDailyChallenge();
});
function openDailyChallenge(){
  document.getElementById('dailyChallengeBody').innerHTML =
    '<div class="sq-title">Comment tu t\'es senti·e ?</div>' +
    '<p class="sq-intro">Dis-en un peu sur ce défi — c\'est ce qui compte le plus, pas juste la case cochée.</p>' +
    '<textarea id="dailyChallengeReflection" class="review-textarea" rows="3" maxlength="500" placeholder="Ex: Ça m\'a fait du bien de marcher, je me sens plus léger·ère…"></textarea>' +
    '<p id="dailyChallengeMsg" style="font-size:.76rem;color:var(--txt-s);margin:8px 0;min-height:1em;"></p>' +
    '<button class="btn-sm btn-primary-sm" style="width:100%;" onclick="submitDailyChallenge()">Valider le défi</button>';
  document.getElementById('dailyChallengeModal').style.display='flex';
  document.body.style.overflow='hidden';
}
async function submitDailyChallenge(){
  const textarea = document.getElementById('dailyChallengeReflection');
  const msg = document.getElementById('dailyChallengeMsg');
  const reflection = textarea.value.trim();
  if(reflection.length < 5){ msg.textContent = 'Dis-en un peu plus sur comment tu t\'es senti·e 🌸'; return; }
  msg.textContent = 'Envoi…';
  try{
    const res = await fetch('/api/sensibilisation/defi-du-jour/', {
      method:'POST',
      headers:{'Content-Type':'application/json','X-CSRFToken':getCsrf()},
      body: JSON.stringify({reflection_text: reflection}),
    });
    const data = await res.json().catch(()=>({}));
    if(res.ok){
      msg.textContent = data.message || 'Bravo !';
      setTimeout(() => window.location.reload(), 800);
    } else {
      msg.textContent = data.error || 'Une erreur est survenue.';
    }
  }catch(e){
    console.error('❌ Daily challenge submit failed', e);
    msg.textContent = 'Une erreur est survenue, réessaie plus tard.';
  }
}

// ── SENSIBILISATION: MYTHES ──
async function submitMyth(){
  const input = document.getElementById('mythInput');
  const msg = document.getElementById('mythMsg');
  const text = input.value.trim();
  if(text.length < 10){ msg.textContent = 'Décris un peu plus le mythe que tu as entendu 🌸'; return; }
  msg.textContent = 'Envoi…';
  try{
    const res = await fetch('/api/sensibilisation/mythes/', {
      method:'POST',
      headers:{'Content-Type':'application/json','X-CSRFToken':getCsrf()},
      body: JSON.stringify({myth_text: text}),
    });
    const data = await res.json().catch(()=>({}));
    msg.textContent = data.message || data.error || 'Une erreur est survenue.';
    if(res.ok) input.value = '';
  }catch(e){
    console.error('❌ Myth submit failed', e);
    msg.textContent = 'Une erreur est survenue, réessaie plus tard.';
  }
}

// ── JEUX: helper to save a score ──
async function saveGameScore(game, score){
  try{
    await fetch('/api/jeux/score/', {
      method:'POST',
      headers:{'Content-Type':'application/json','X-CSRFToken':getCsrf()},
      body: JSON.stringify({game, score}),
    });
  }catch(e){
    console.error('❌ Game score save failed', e);
  }
}

// ── JEU: ATTRAPE LES PENSÉES POSITIVES ──
let catchGameState = null;

function closeCatchGame(){
  if(catchGameState) clearCatchGame();
  document.getElementById('catchGameModal').style.display='none';
  document.body.style.overflow='';
}
document.getElementById('catchGameModal').addEventListener('click',function(e){
  if(e.target===this) closeCatchGame();
});

function clearCatchGame(){
  if(!catchGameState) return;
  clearInterval(catchGameState.spawnInterval);
  clearInterval(catchGameState.timerInterval);
  catchGameState.thoughts.forEach(t => { if(t.el && t.el.parentNode) t.el.remove(); clearTimeout(t.timeout); });
  catchGameState = null;
}

function openCatchGame(){
  const positive = JSON.parse(document.getElementById('positiveThoughtsData').textContent);
  const negative = JSON.parse(document.getElementById('negativeThoughtsData').textContent);
  document.getElementById('catchGameBody').innerHTML =
    '<div class="sq-title">🎈 Attrape les pensées positives</div>' +
    '<p class="sq-intro">Clique sur les pensées <strong style="color:#2d5540;">positives (vertes)</strong>, laisse tomber les <strong style="color:#a13a3a;">négatives (roses)</strong> sans les toucher.</p>' +
    '<div class="catch-hud"><span>Score : <span id="catchScore">0</span></span><span id="catchTimer">45s</span></div>' +
    '<div class="catch-arena" id="catchArena"></div>' +
    '<button class="btn-sm btn-outline-sm" style="width:100%;" onclick="closeCatchGame()">Quitter</button>';
  document.getElementById('catchGameModal').style.display='flex';
  document.body.style.overflow='hidden';

  catchGameState = {score: 0, timeLeft: 45, thoughts: [], spawnInterval: null, timerInterval: null, positive, negative};

  catchGameState.spawnInterval = setInterval(() => spawnThought(), 750);
  catchGameState.timerInterval = setInterval(() => {
    catchGameState.timeLeft--;
    const timerEl = document.getElementById('catchTimer');
    if(timerEl) timerEl.textContent = catchGameState.timeLeft + 's';
    if(catchGameState.timeLeft <= 0) endCatchGame();
  }, 1000);
}

function spawnThought(){
  if(!catchGameState) return;
  const arena = document.getElementById('catchArena');
  if(!arena) return;
  const isPositive = Math.random() < 0.55;
  const pool = isPositive ? catchGameState.positive : catchGameState.negative;
  const text = pool[Math.floor(Math.random() * pool.length)];
  const el = document.createElement('div');
  el.className = 'catch-thought ' + (isPositive ? 'positive' : 'negative');
  el.textContent = text;
  const arenaWidth = arena.clientWidth || 300;
  el.style.left = Math.max(4, Math.random() * (arenaWidth - 140)) + 'px';
  el.style.top = '-40px';
  arena.appendChild(el);
  requestAnimationFrame(() => {
    el.style.transition = 'top 4.5s linear';
    el.style.top = (arena.clientHeight + 20) + 'px';
  });
  const entry = {el, isPositive};
  el.onclick = () => catchThought(entry);
  entry.timeout = setTimeout(() => {
    if(el.parentNode) el.remove();
    const idx = catchGameState.thoughts.indexOf(entry);
    if(idx >= 0) catchGameState.thoughts.splice(idx, 1);
  }, 4700);
  catchGameState.thoughts.push(entry);
}

function catchThought(entry){
  if(!catchGameState || !entry.el.parentNode) return;
  catchGameState.score += entry.isPositive ? 1 : -1;
  if(catchGameState.score < 0) catchGameState.score = 0;
  const scoreEl = document.getElementById('catchScore');
  if(scoreEl) scoreEl.textContent = catchGameState.score;
  entry.el.remove();
  clearTimeout(entry.timeout);
  const idx = catchGameState.thoughts.indexOf(entry);
  if(idx >= 0) catchGameState.thoughts.splice(idx, 1);
}

function endCatchGame(){
  if(!catchGameState) return;
  const finalScore = catchGameState.score;
  clearCatchGame();
  document.getElementById('catchGameBody').innerHTML =
    '<div class="sq-title">Terminé !</div>' +
    '<div class="sq-result-band"><div class="sq-result-score">' + finalScore + '</div><div class="sq-result-label">points</div></div>' +
    buildShareRow('J\'ai fait ' + finalScore + ' points au jeu Attrape les pensées positives sur SANA !') +
    '<button class="btn-sm btn-outline-sm" style="width:100%;margin-top:8px;" onclick="closeCatchGame()">Fermer</button>';
  saveGameScore('attrape_pensees', finalScore);
}

// ── JEU: RESPIRE AVEC MOI ──
let breatheGameState = null;

function closeBreatheGame(){
  if(breatheGameState){
    breatheGameState.timeouts.forEach(t => clearTimeout(t));
    document.removeEventListener('visibilitychange', breatheGameState.visHandler);
  }
  breatheGameState = null;
  document.getElementById('breatheGameModal').style.display='none';
  document.body.style.overflow='';
}
document.getElementById('breatheGameModal').addEventListener('click',function(e){
  if(e.target===this) closeBreatheGame();
});

const BREATHE_CYCLES = 6;
const BREATHE_PHASES = [
  {cls:'inhale', label:'Inspire…'},
  {cls:'hold', label:'Retiens…'},
  {cls:'exhale', label:'Expire…'},
];

function openBreatheGame(){
  document.getElementById('breatheGameBody').innerHTML =
    '<div class="sq-title">🧘 Respire avec moi</div>' +
    '<p class="sq-intro" id="breatheCycleLabel">Cycle 1/' + BREATHE_CYCLES + '</p>' +
    '<div class="breathe-circle-wrap"><div class="breathe-circle" id="breatheCircle">Prêt·e ?</div></div>' +
    '<button class="btn-sm btn-outline-sm" style="width:100%;" onclick="closeBreatheGame()">Arrêter</button>';
  document.getElementById('breatheGameModal').style.display='flex';
  document.body.style.overflow='hidden';

  breatheGameState = {left: false, timeouts: [], visHandler: null};
  breatheGameState.visHandler = () => { if(document.hidden) breatheGameState.left = true; };
  document.addEventListener('visibilitychange', breatheGameState.visHandler);

  runBreatheCycle(0);
}

function runBreatheCycle(cycleIndex){
  if(!breatheGameState || cycleIndex >= BREATHE_CYCLES){
    if(breatheGameState) finishBreatheGame();
    return;
  }
  const label = document.getElementById('breatheCycleLabel');
  if(label) label.textContent = 'Cycle ' + (cycleIndex + 1) + '/' + BREATHE_CYCLES;
  runBreathePhase(cycleIndex, 0);
}

function runBreathePhase(cycleIndex, phaseIndex){
  if(!breatheGameState) return;
  if(phaseIndex >= BREATHE_PHASES.length){
    runBreatheCycle(cycleIndex + 1);
    return;
  }
  const circle = document.getElementById('breatheCircle');
  const phase = BREATHE_PHASES[phaseIndex];
  if(circle){
    circle.className = 'breathe-circle ' + phase.cls;
    circle.textContent = phase.label;
  }
  const t = setTimeout(() => runBreathePhase(cycleIndex, phaseIndex + 1), 4000);
  breatheGameState.timeouts.push(t);
}

function finishBreatheGame(){
  const left = breatheGameState ? breatheGameState.left : true;
  const score = left ? 70 : 100;
  closeBreatheGame();
  document.getElementById('breatheGameBody').innerHTML =
    '<div class="sq-title">Bien joué 🌸</div>' +
    '<div class="sq-result-band"><div class="sq-result-score">' + score + '</div><div class="sq-result-label">points</div></div>' +
    '<p style="font-size:.78rem;color:var(--txt-s);line-height:1.6;margin-bottom:14px;">' + (left ? 'Tu as un peu quitté la page, mais tu es resté·e jusqu\'au bout — bravo !' : 'Tu as suivi les 6 cycles sans interruption, parfaite régularité.') + '</p>' +
    buildShareRow('Je viens de faire une session de respiration guidée sur SANA 🧘') +
    '<button class="btn-sm btn-outline-sm" style="width:100%;" onclick="document.getElementById(\'breatheGameModal\').style.display=\'none\';document.body.style.overflow=\'\';">Fermer</button>';
  saveGameScore('respire_avec_moi', score);
}

// ── JEU: CHASSE AUX PENSÉES (TCC) ──
let reframeState = null;

function closeReframeGame(){
  reframeState = null;
  document.getElementById('reframeGameModal').style.display='none';
  document.body.style.overflow='';
}
document.getElementById('reframeGameModal').addEventListener('click',function(e){
  if(e.target===this) closeReframeGame();
});

function shuffleArray(arr){
  const a = arr.slice();
  for(let i=a.length-1; i>0; i--){
    const j = Math.floor(Math.random()*(i+1));
    [a[i],a[j]] = [a[j],a[i]];
  }
  return a;
}

function openReframeGame(){
  const items = JSON.parse(document.getElementById('thoughtReframesData').textContent);
  reframeState = {items: shuffleArray(items), index: 0, score: 0};
  document.getElementById('reframeGameModal').style.display='flex';
  document.body.style.overflow='hidden';
  renderReframeRound();
}

function renderReframeRound(){
  const s = reframeState;
  if(!s) return;
  if(s.index >= s.items.length){ finishReframeGame(); return; }
  const item = s.items[s.index];
  const options = shuffleArray([{text:item.correct, correct:true}, {text:item.wrong, correct:false}]);
  let html = '<div class="sq-title">🌧️ Chasse aux pensées</div>';
  html += '<p class="reframe-progress">Pensée ' + (s.index+1) + '/' + s.items.length + '</p>';
  html += '<div class="reframe-thought-box">« ' + escHtml(item.thought) + ' »</div>';
  html += '<p style="font-size:.76rem;color:var(--txt-s);margin-bottom:10px;">Quelle est la pensée la plus réaliste ?</p>';
  html += '<div class="reframe-options" id="reframeOptions">';
  options.forEach((opt, i) => {
    html += '<div class="reframe-option" data-correct="' + opt.correct + '" onclick="pickReframe(this)">' + escHtml(opt.text) + '</div>';
  });
  html += '</div>';
  document.getElementById('reframeGameBody').innerHTML = html;
}

function pickReframe(el){
  const s = reframeState;
  if(!s) return;
  const options = document.querySelectorAll('#reframeOptions .reframe-option');
  options.forEach(o => o.classList.add('disabled'));
  const isCorrect = el.dataset.correct === 'true';
  el.classList.add(isCorrect ? 'correct' : 'wrong');
  if(!isCorrect){
    options.forEach(o => { if(o.dataset.correct === 'true') o.classList.add('correct'); });
  } else {
    s.score++;
  }
  setTimeout(() => { s.index++; renderReframeRound(); }, 1400);
}

function finishReframeGame(){
  const s = reframeState;
  const score = s ? s.score : 0;
  const total = s ? s.items.length : 8;
  document.getElementById('reframeGameBody').innerHTML =
    '<div class="sq-title">Terminé !</div>' +
    '<div class="sq-result-band"><div class="sq-result-score">' + score + '/' + total + '</div><div class="sq-result-label">pensées reformulées</div></div>' +
    buildShareRow('J\'ai reformulé ' + score + '/' + total + ' pensées au jeu Chasse aux pensées sur SANA !') +
    '<button class="btn-sm btn-outline-sm" style="width:100%;margin-top:8px;" onclick="closeReframeGame()">Fermer</button>';
  saveGameScore('chasse_pensees', score);
  reframeState = null;
}

// ── JEU: MEMORY DES ÉMOTIONS ──
let memoryState = null;

function closeMemoryGame(){
  memoryState = null;
  document.getElementById('memoryGameModal').style.display='none';
  document.body.style.overflow='';
}
document.getElementById('memoryGameModal').addEventListener('click',function(e){
  if(e.target===this) closeMemoryGame();
});

function openMemoryGame(){
  const emotions = JSON.parse(document.getElementById('emotionCardsData').textContent);
  const pairs = [];
  emotions.forEach((e, i) => { pairs.push({...e, pairId:i}); pairs.push({...e, pairId:i}); });
  const cards = shuffleArray(pairs).map((c, i) => ({...c, id:i, flipped:false, matched:false}));
  memoryState = {cards, moves: 0, matchedCount: 0, busy: false, totalPairs: emotions.length};
  document.getElementById('memoryGameModal').style.display='flex';
  document.body.style.overflow='hidden';
  renderMemoryGame();
}

function renderMemoryGame(defText){
  const s = memoryState;
  if(!s) return;
  let html = '<div class="sq-title">😊 Memory des émotions</div>';
  html += '<div class="memory-hud"><span>Paires : ' + s.matchedCount + '/' + s.totalPairs + '</span><span>Coups : ' + s.moves + '</span></div>';
  html += '<div class="memory-definition">' + (defText ? escHtml(defText) : 'Trouve les paires d\'émotions identiques.') + '</div>';
  html += '<div class="memory-grid">';
  s.cards.forEach(c => {
    const cls = c.matched ? 'matched' : (c.flipped ? 'flipped' : '');
    html += '<div class="memory-card ' + cls + '" onclick="flipMemoryCard(' + c.id + ')">' + ((c.flipped || c.matched) ? c.emoji : '🌸') + '</div>';
  });
  html += '</div>';
  html += '<button class="btn-sm btn-outline-sm" style="width:100%;" onclick="closeMemoryGame()">Quitter</button>';
  document.getElementById('memoryGameBody').innerHTML = html;
}

function flipMemoryCard(id){
  const s = memoryState;
  if(!s || s.busy) return;
  const card = s.cards.find(c => c.id === id);
  if(!card || card.flipped || card.matched) return;

  card.flipped = true;
  const flippedCards = s.cards.filter(c => c.flipped && !c.matched);

  if(flippedCards.length < 2){
    renderMemoryGame();
    return;
  }

  s.moves++;
  const [a, b] = flippedCards;
  if(a.pairId === b.pairId){
    a.matched = true; b.matched = true;
    s.matchedCount++;
    renderMemoryGame(a.name + ' : ' + a.definition);
    if(s.matchedCount >= s.totalPairs) setTimeout(finishMemoryGame, 900);
  } else {
    s.busy = true;
    renderMemoryGame();
    setTimeout(() => {
      a.flipped = false; b.flipped = false;
      s.busy = false;
      renderMemoryGame();
    }, 900);
  }
}

function finishMemoryGame(){
  const s = memoryState;
  if(!s) return;
  const perfect = s.totalPairs * 2;
  const score = Math.max(0, 100 - (s.moves - s.totalPairs) * 5);
  document.getElementById('memoryGameBody').innerHTML =
    '<div class="sq-title">Bravo, terminé !</div>' +
    '<div class="sq-result-band"><div class="sq-result-score">' + score + '</div><div class="sq-result-label">points · ' + s.moves + ' coups</div></div>' +
    buildShareRow('J\'ai fait ' + score + ' points au Memory des émotions sur SANA !') +
    '<button class="btn-sm btn-outline-sm" style="width:100%;margin-top:8px;" onclick="closeMemoryGame()">Fermer</button>';
  saveGameScore('memory_emotions', score);
  memoryState = null;
}

// ── JEU MULTIJOUEUR: DEVINE L'ÉMOTION ──
let roomState = {code: null, messages: [], lastMsgId: 0, pollTimer: null};

async function createGameRoom(){
  const msg = document.getElementById('roomJoinMsg');
  try{
    const res = await fetch('/api/jeux/salons/creer/', {method:'POST', headers:{'X-CSRFToken':getCsrf()}});
    const data = await res.json();
    if(!res.ok){ msg.textContent = data.error || 'Une erreur est survenue.'; return; }
    openGameRoom(data.code);
  }catch(e){
    console.error('❌ Create room failed', e);
    msg.textContent = 'Une erreur est survenue, réessaie plus tard.';
  }
}

async function joinGameRoom(){
  const input = document.getElementById('joinRoomCode');
  const msg = document.getElementById('roomJoinMsg');
  const code = input.value.trim().toUpperCase();
  if(!code){ msg.textContent = 'Entre un code de salon.'; return; }
  try{
    const res = await fetch('/api/jeux/salons/rejoindre/', {
      method:'POST',
      headers:{'Content-Type':'application/json','X-CSRFToken':getCsrf()},
      body: JSON.stringify({code}),
    });
    const data = await res.json();
    if(!res.ok){ msg.textContent = data.error || 'Une erreur est survenue.'; return; }
    msg.textContent = '';
    openGameRoom(data.code);
  }catch(e){
    console.error('❌ Join room failed', e);
    msg.textContent = 'Une erreur est survenue, réessaie plus tard.';
  }
}

function openGameRoom(code){
  roomState = {code, messages: [], lastMsgId: 0, pollTimer: null};
  document.getElementById('gameRoomModal').style.display = 'flex';
  document.body.style.overflow = 'hidden';
  pollGameRoom();
  roomState.pollTimer = setInterval(pollGameRoom, 2000);
}

function closeGameRoom(){
  if(roomState.pollTimer) clearInterval(roomState.pollTimer);
  roomState = {code: null, messages: [], lastMsgId: 0, pollTimer: null};
  document.getElementById('gameRoomModal').style.display = 'none';
  document.body.style.overflow = '';
}
document.getElementById('gameRoomModal').addEventListener('click', function(e){
  if(e.target === this) closeGameRoom();
});

async function pollGameRoom(){
  if(!roomState.code) return;
  try{
    const res = await fetch('/api/jeux/salons/' + roomState.code + '/etat/?since=' + roomState.lastMsgId, {credentials:'same-origin'});
    if(!res.ok) return;
    const data = await res.json();
    if(data.messages && data.messages.length){
      roomState.messages = roomState.messages.concat(data.messages);
      roomState.lastMsgId = data.messages[data.messages.length - 1].id;
    }
    renderGameRoom(data);
  }catch(e){
    console.error('❌ Room poll failed', e);
  }
}

function renderGameRoom(data){
  const wasScrolledDown = true; // keep chat pinned to bottom on every render (short list, fine)
  let html = '<div class="sq-title">🎭 Devine l\'émotion</div>';
  html += '<div class="room-code-box"><div style="font-size:.68rem;color:var(--txt-s);text-transform:uppercase;letter-spacing:1px;">Code du salon</div><div class="room-code">' + escHtml(roomState.code) + '</div></div>';

  html += '<div class="room-players">';
  data.players.forEach(p => {
    html += '<div class="room-player-chip' + (p.is_you ? ' is-you' : '') + '">' + escHtml(p.name) + (p.is_you ? ' (toi)' : '') + ' · ' + p.score + '</div>';
  });
  html += '</div>';

  if(data.status === 'waiting'){
    html += '<p style="font-size:.8rem;color:var(--txt-s);text-align:center;margin-bottom:12px;">En attente d\'autres joueur·euses… partage le code !</p>';
    if(data.is_host){
      html += '<button class="btn-sm btn-primary-sm" style="width:100%;margin-bottom:12px;" onclick="startGameRoomNow()">Démarrer la partie</button>';
    }
  } else if(data.status === 'playing'){
    if(data.is_giver){
      html += '<div class="room-secret-box"><div class="room-secret-label">C\'est ton tour — indice sans dire le mot !</div><div class="room-secret-word">' + escHtml(data.secret_emotion) + '</div></div>';
    } else {
      html += '<p style="font-size:.78rem;color:var(--txt-s);text-align:center;margin-bottom:10px;">🎭 ' + escHtml(data.giver_name) + ' donne des indices — devine l\'émotion !</p>';
    }
  } else if(data.status === 'finished'){
    html += '<p style="font-size:.85rem;color:var(--r500);text-align:center;font-weight:600;margin-bottom:12px;">🏁 Partie terminée !</p>';
    if(data.ai_feedback){
      html += '<div class="room-coach-box"><div class="room-coach-label">🌸 Coach IA</div><div class="room-coach-text">' + escHtml(data.ai_feedback) + '</div></div>';
    } else {
      html += '<div class="room-coach-box"><div class="room-coach-label">🌸 Coach IA</div><div class="room-coach-text room-coach-loading">Analyse de la partie…</div></div>';
    }
  }

  html += '<div class="room-chat" id="roomChat">';
  roomState.messages.forEach(m => {
    const cls = m.is_system ? 'system' : (m.is_correct_guess ? 'correct' : '') ;
    html += '<div class="room-msg ' + cls + (m.is_you ? ' is-you' : '') + '">' +
      (m.is_system ? escHtml(m.content) : '<span class="room-msg-author">' + escHtml(m.author) + ' :</span> ' + escHtml(m.content)) +
    '</div>';
  });
  html += '</div>';

  if(data.status === 'playing'){
    html += '<div class="room-input-row">' +
      '<input type="text" class="post-comment-input" id="roomMsgInput" placeholder="' + (data.is_giver ? 'Donne un indice…' : 'Propose ta réponse…') + '" style="flex:1;" onkeydown="if(event.key===\'Enter\'){event.preventDefault();sendRoomMessage()}">' +
      '<button class="post-comment-send" onclick="sendRoomMessage()">Envoyer</button>' +
    '</div>';
  }
  html += '<button class="btn-sm btn-outline-sm" style="width:100%;margin-top:10px;" onclick="closeGameRoom()">Quitter</button>';

  document.getElementById('gameRoomBody').innerHTML = html;
  const chatEl = document.getElementById('roomChat');
  if(chatEl) chatEl.scrollTop = chatEl.scrollHeight;
}

async function startGameRoomNow(){
  if(!roomState.code) return;
  try{
    const res = await fetch('/api/jeux/salons/' + roomState.code + '/demarrer/', {method:'POST', headers:{'X-CSRFToken':getCsrf()}});
    if(res.ok) pollGameRoom();
  }catch(e){
    console.error('❌ Start room failed', e);
  }
}

async function sendRoomMessage(){
  const input = document.getElementById('roomMsgInput');
  if(!input) return;
  const content = input.value.trim();
  if(!content || !roomState.code) return;
  input.value = '';
  try{
    const res = await fetch('/api/jeux/salons/' + roomState.code + '/message/', {
      method:'POST',
      headers:{'Content-Type':'application/json','X-CSRFToken':getCsrf()},
      body: JSON.stringify({content}),
    });
    if(!res.ok){
      const data = await res.json().catch(()=>({}));
      if(data.error) alert(data.error);
    }
    pollGameRoom();
  }catch(e){
    console.error('❌ Send room message failed', e);
  }
}

// ── MOOD DATA FROM DB ──
// MOOD_DATA is declared inline in dashboard.html (server-rendered value) before this file loads.
const MOOD_DAYS = ['Lun','Mar','Mer','Jeu','Ven','Sam','Dim'];
const MONTHS_FR = ['Janvier','Février','Mars','Avril','Mai','Juin','Juillet','Août','Septembre','Octobre','Novembre','Décembre'];

function renderMoodChart() {
  const chart = document.getElementById('barChart');
  if (!chart) return;
  // Build a map: weekday → last entry for that day
  const dayMap = {};
  MOOD_DATA.forEach(e => { dayMap[e.day] = e; });
  let html = '';
  for (let d = 0; d < 7; d++) {
    const entry = dayMap[d];
    const h = entry ? Math.max(8, entry.score) + '%' : '4%';
    const emoji = entry ? entry.emoji : '';
    const opacity = entry ? '1' : '0.25';
    html += `<div class="bar-col">
      <div class="bar" style="height:${h};opacity:${opacity}"></div>
      <div class="bar-day">${MOOD_DAYS[d]}</div>
      <div class="bar-val">${emoji}</div>
    </div>`;
  }
  chart.innerHTML = html;

  // Compute week label
  const today = new Date();
  const dayOfWeek = today.getDay(); // 0=Sun
  const monday = new Date(today);
  monday.setDate(today.getDate() - (dayOfWeek === 0 ? 6 : dayOfWeek - 1));
  const sunday = new Date(monday);
  sunday.setDate(monday.getDate() + 6);
  const fmt = d => `${d.getDate()} ${MONTHS_FR[d.getMonth()].slice(0,4)}`;
  const wl = document.getElementById('weekLabel');
  if (wl) wl.textContent = `Du ${fmt(monday)} au ${fmt(sunday)} ${sunday.getFullYear()}`;

  // Compute dominant mood
  const counts = {};
  MOOD_DATA.forEach(e => { counts[e.emoji] = (counts[e.emoji] || 0) + 1; });
  const dominant = Object.entries(counts).sort((a,b) => b[1]-a[1])[0];
  const sd = document.getElementById('statDominant');
  if (sd) sd.textContent = dominant ? dominant[0] : '—';
}
renderMoodChart();

// Quick mood save from accueil bar
function quickSaveMood(btn, moodVal) {
  btn.closest('div').querySelectorAll('.mood-emoji-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  fetch('/api/humeur/', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'Content-Type':'application/json','X-CSRFToken':getCsrf()},
    body: JSON.stringify({mood: moodVal, note: ''}),
  })
  .then(r => r.json())
  .then(data => {
    if (data.error) return;
    const saved = document.getElementById('moodSaved');
    if (saved) { saved.classList.add('show'); setTimeout(() => saved.classList.remove('show'), 2500); }
    const pyDay = new Date().getDay() === 0 ? 6 : new Date().getDay() - 1;
    MOOD_DATA.push({day: pyDay, score: data.score, emoji: data.emoji});
    renderMoodChart();
    const countEl = document.getElementById('statDays');
    if (countEl) countEl.textContent = MOOD_DATA.length + '/7';
  });
}

let dashSelectedMood = '';
function selectMoodHome(btn, moodVal) {
  btn.closest('div').querySelectorAll('.mood-emoji-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  dashSelectedMood = moodVal;
}

function saveMoodNow() {
  if (!dashSelectedMood) {
    alert('Sélectionne une humeur d\'abord.');
    return;
  }
  const note = (document.getElementById('moodNote') || {}).value || '';
  fetch('/api/humeur/', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'Content-Type':'application/json','X-CSRFToken':getCsrf()},
    body: JSON.stringify({mood: dashSelectedMood, note}),
  })
  .then(r => r.json())
  .then(data => {
    if (data.error) return;
    const msg = document.getElementById('moodSaveMsg');
    if (msg) { msg.style.display = 'block'; setTimeout(() => msg.style.display='none', 2500); }
    // Add to chart data and re-render
    const today = new Date().getDay();
    // JS: 0=Sun,1=Mon...6=Sat → Python: 0=Mon...6=Sun
    const pyDay = today === 0 ? 6 : today - 1;
    MOOD_DATA.push({day: pyDay, score: data.score, emoji: data.emoji});
    renderMoodChart();
    dashSelectedMood = '';
    document.querySelectorAll('.mood-emoji-btn').forEach(b => b.classList.remove('active'));
    if (document.getElementById('moodNote')) document.getElementById('moodNote').value = '';
  });
}

// ── COMMUNITY AJAX ──
function dashToggleLike(postId, btn) {
  fetch('/api/communaute/' + postId + '/like/', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'X-CSRFToken': getCsrf()},
  })
  .then(r => r.json())
  .then(data => {
    if (data.error) return;
    btn.classList.toggle('liked', data.is_liked);
    const countEl = document.getElementById('like-count-' + postId);
    if (countEl) countEl.textContent = data.like_count;
  });
}

function dashToggleSupport(postId, btn) {
  fetch('/api/communaute/' + postId + '/soutenir/', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'X-CSRFToken': getCsrf()},
  })
  .then(r => r.json())
  .then(data => {
    if (data.error) return;
    btn.classList.toggle('liked', data.is_supported);
    const countEl = document.getElementById('support-count-' + postId);
    if (countEl) countEl.textContent = data.support_count;
    if (data.is_supported && btn.dataset.requestsSupport === '1') {
      openSupportPayment(postId);
    }
  });
}

const loadedComments = {};
function toggleComments(postId) {
  const panel = document.getElementById('comments-' + postId);
  if (!panel) return;
  const showing = panel.style.display !== 'none';
  if (showing) { panel.style.display = 'none'; return; }
  panel.style.display = 'block';
  if (!loadedComments[postId]) loadComments(postId);
}

function loadComments(postId) {
  const list = document.getElementById('comments-list-' + postId);
  if (!list) return;
  fetch('/api/communaute/' + postId + '/commentaires/', {credentials: 'same-origin'})
    .then(r => r.json())
    .then(data => {
      loadedComments[postId] = true;
      renderComments(postId, data.comments || []);
    })
    .catch(() => { list.innerHTML = '<div class="post-comments-empty">Impossible de charger les commentaires.</div>'; });
}

function renderComments(postId, comments) {
  const list = document.getElementById('comments-list-' + postId);
  if (!list) return;
  if (!comments.length) {
    list.innerHTML = '<div class="post-comments-empty">Aucun commentaire pour l’instant. Sois le premier à soutenir 🌸</div>';
    return;
  }
  list.innerHTML = comments.map(c =>
    '<div class="post-comment">' +
      '<div class="post-comment-author">' + escHtml(c.anon) + '</div>' +
      '<div class="post-comment-text">' + escHtml(c.content) + '</div>' +
    '</div>'
  ).join('');
}

function submitComment(postId) {
  const input = document.getElementById('comment-input-' + postId);
  const content = (input.value || '').trim();
  if (!content) return;
  fetch('/api/communaute/' + postId + '/commentaires/', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'Content-Type':'application/json','X-CSRFToken': getCsrf()},
    body: JSON.stringify({content}),
  })
  .then(r => r.json())
  .then(data => {
    if (data.error) return;
    input.value = '';
    const list = document.getElementById('comments-list-' + postId);
    if (list) {
      const empty = list.querySelector('.post-comments-empty');
      if (empty) empty.remove();
      const el = document.createElement('div');
      el.className = 'post-comment';
      el.innerHTML = '<div class="post-comment-author">' + escHtml(data.anon) + '</div><div class="post-comment-text">' + escHtml(data.content) + '</div>';
      list.appendChild(el);
    }
    const countEl = document.getElementById('comment-count-' + postId);
    if (countEl) countEl.textContent = data.comment_count;
  });
}

function dashPublishPost() {
  const content = (document.getElementById('dashWriteContent') || {}).value || '';
  const tag = (document.getElementById('dashWriteTag') || {}).value || 'autre';
  const supportCheck = document.getElementById('dashWriteSupport');
  const requestsSupport = !!(supportCheck && supportCheck.checked);
  if (!content.trim()) { document.getElementById('dashWriteContent').focus(); return; }
  fetch('/api/communaute/', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'Content-Type':'application/json','X-CSRFToken':getCsrf()},
    body: JSON.stringify({content: content.trim(), tag, requests_support: requestsSupport}),
  })
  .then(r => r.json())
  .then(data => {
    if (data.error) { alert(data.error); return; }
    document.getElementById('dashWriteContent').value = '';
    if (supportCheck) supportCheck.checked = false;
    const feed = document.getElementById('dashPostFeed');
    const card = document.createElement('div');
    card.className = 'post-card';
    card.id = 'post-' + data.id;
    card.innerHTML =
      '<div class="post-header">' +
        '<div class="post-av post-av-1">' + (data.initial || 'A') + '</div>' +
        '<div>' +
          '<div class="post-anon">' + escHtml(data.anon) + ' <span class="post-tag">' + escHtml(data.tag_label) + '</span>' +
            (data.requests_support ? ' <span class="post-tag post-tag-support">💰 Demande de soutien</span>' : '') +
          '</div>' +
          '<div class="post-meta">À l\'instant</div>' +
        '</div>' +
      '</div>' +
      '<div class="post-text">' + escHtml(data.content) + '</div>' +
      '<div class="post-actions">' +
        '<button class="post-btn" id="like-btn-' + data.id + '" onclick="dashToggleLike(' + data.id + ', this)">❤️ <span id="like-count-' + data.id + '">0</span></button>' +
        '<button class="post-btn" onclick="toggleComments(' + data.id + ')">💬 <span id="comment-count-' + data.id + '">0</span></button>' +
        '<button class="post-btn" id="support-btn-' + data.id + '" data-requests-support="' + (data.requests_support ? '1' : '0') + '" onclick="dashToggleSupport(' + data.id + ', this)">🤝 Soutenir <span id="support-count-' + data.id + '">0</span></button>' +
        '<button class="post-btn post-btn-report" onclick="openReportPost(' + data.id + ')" title="Signaler ce post">🚩</button>' +
      '</div>' +
      '<div class="post-comments" id="comments-' + data.id + '" style="display:none;">' +
        '<div class="post-comments-list" id="comments-list-' + data.id + '"></div>' +
        '<div class="post-comment-input-row">' +
          '<input type="text" class="post-comment-input" id="comment-input-' + data.id + '" placeholder="Écrire un commentaire de soutien…" maxlength="500" onkeydown="if(event.key===\'Enter\'){event.preventDefault();submitComment(' + data.id + ')}">' +
          '<button class="post-comment-send" onclick="submitComment(' + data.id + ')">Envoyer</button>' +
        '</div>' +
      '</div>';
    feed.prepend(card);
  });
}

// ── BLOG AJAX (DASHBOARD) ──
function blogToggleLike(postId, btn){
  fetch('/api/blog/' + postId + '/like/', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'X-CSRFToken': getCsrf()},
  })
  .then(r => r.json())
  .then(data => {
    if (data.error) return;
    btn.classList.toggle('liked', data.is_liked);
    const countEl = document.getElementById('blog-like-count-' + postId);
    if (countEl) countEl.textContent = data.like_count;
  });
}

function blogToggleSave(postId, btn){
  fetch('/api/blog/' + postId + '/enregistrer/', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'X-CSRFToken': getCsrf()},
  })
  .then(r => r.json())
  .then(data => {
    if (data.error) return;
    btn.classList.toggle('liked', data.is_saved);
    btn.innerHTML = data.is_saved ? '🔖 Enregistré' : '🔖 Enregistrer';
  });
}

const loadedBlogComments = {};
function toggleBlogComments(postId){
  const panel = document.getElementById('blog-comments-' + postId);
  if (!panel) return;
  const showing = panel.style.display !== 'none';
  if (showing) { panel.style.display = 'none'; return; }
  panel.style.display = 'block';
  if (!loadedBlogComments[postId]) loadBlogComments(postId);
}

function loadBlogComments(postId){
  const list = document.getElementById('blog-comments-list-' + postId);
  if (!list) return;
  fetch('/api/blog/' + postId + '/commentaires/', {credentials: 'same-origin'})
    .then(r => r.json())
    .then(data => {
      loadedBlogComments[postId] = true;
      renderBlogComments(postId, data.comments || []);
    })
    .catch(() => { list.innerHTML = '<div class="post-comments-empty">Impossible de charger les commentaires.</div>'; });
}

function renderBlogComments(postId, comments){
  const list = document.getElementById('blog-comments-list-' + postId);
  if (!list) return;
  if (!comments.length) {
    list.innerHTML = '<div class="post-comments-empty">Aucun commentaire pour l’instant. Sois le premier à réagir 🌸</div>';
    return;
  }
  list.innerHTML = comments.map(c =>
    '<div class="post-comment">' +
      '<div class="post-comment-author">' + escHtml(c.anon) + '</div>' +
      '<div class="post-comment-text">' + escHtml(c.content) + '</div>' +
    '</div>'
  ).join('');
}

function submitBlogComment(postId){
  const input = document.getElementById('blog-comment-input-' + postId);
  const content = (input.value || '').trim();
  if (!content) return;
  fetch('/api/blog/' + postId + '/commentaires/', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'Content-Type':'application/json','X-CSRFToken': getCsrf()},
    body: JSON.stringify({content}),
  })
  .then(r => r.json())
  .then(data => {
    if (data.error) return;
    input.value = '';
    const list = document.getElementById('blog-comments-list-' + postId);
    if (list) {
      const empty = list.querySelector('.post-comments-empty');
      if (empty) empty.remove();
      const el = document.createElement('div');
      el.className = 'post-comment';
      el.innerHTML = '<div class="post-comment-author">' + escHtml(data.anon) + '</div><div class="post-comment-text">' + escHtml(data.content) + '</div>';
      list.appendChild(el);
    }
    const countEl = document.getElementById('blog-comment-count-' + postId);
    if (countEl) countEl.textContent = data.comment_count;
  });
}

function deleteBlogPost(postId){
  if (!confirm('Supprimer définitivement cet article ?')) return;
  fetch('/api/blog/' + postId + '/supprimer/', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'X-CSRFToken': getCsrf()},
  })
  .then(r => r.json())
  .then(data => {
    if (data.error) { alert(data.error); return; }
    const card = document.getElementById('blog-post-' + postId);
    if (card) card.remove();
  });
}

function handleBlogImageChange(input){
  const label = document.getElementById('blogImageFileName');
  if (!label) return;
  label.textContent = (input.files && input.files[0]) ? input.files[0].name : 'Ajouter une image (optionnel)';
}

function publishBlogPost(){
  const titleEl = document.getElementById('blogWriteTitle');
  const contentEl = document.getElementById('blogWriteContent');
  const categoryEl = document.getElementById('blogWriteCategory');
  const imageEl = document.getElementById('blogWriteImage');
  const title = (titleEl.value || '').trim();
  const content = (contentEl.value || '').trim();
  const category = categoryEl.value || 'astuce';
  if (!title) { titleEl.focus(); return; }
  if (!content) { contentEl.focus(); return; }

  const formData = new FormData();
  formData.append('title', title);
  formData.append('content', content);
  formData.append('category', category);
  if (imageEl.files && imageEl.files[0]) formData.append('image', imageEl.files[0]);

  fetch('/api/blog/', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'X-CSRFToken':getCsrf()},
    body: formData,
  })
  .then(r => r.json())
  .then(data => {
    if (data.error) { alert(data.error); return; }
    titleEl.value = '';
    contentEl.value = '';
    imageEl.value = '';
    handleBlogImageChange(imageEl);
    const feed = document.getElementById('blogFeedAll');
    const empty = feed.querySelector('p');
    if (empty && empty.closest('div').textContent.includes('Aucun article')) empty.closest('div').remove();
    const card = document.createElement('div');
    card.className = 'post-card';
    card.id = 'blog-post-' + data.id;
    card.innerHTML =
      '<div class="post-header">' +
        '<div class="post-av post-av-1">' + (data.initial || 'A') + '</div>' +
        '<div>' +
          '<div class="post-anon">' + escHtml(data.anon) + ' <span class="post-tag">' + escHtml(data.category_label) + '</span></div>' +
          '<div class="post-meta">À l\'instant</div>' +
        '</div>' +
      '</div>' +
      '<div class="blogpost-title">' + escHtml(data.title) + '</div>' +
      (data.image_url ? '<img class="blogpost-image" src="' + escHtml(data.image_url) + '" alt="">' : '') +
      '<div class="post-text">' + escHtml(data.content) + '</div>' +
      '<div class="post-actions">' +
        '<button class="post-btn" id="blog-like-btn-' + data.id + '" onclick="blogToggleLike(' + data.id + ', this)">❤️ <span id="blog-like-count-' + data.id + '">0</span></button>' +
        '<button class="post-btn" onclick="toggleBlogComments(' + data.id + ')">💬 <span id="blog-comment-count-' + data.id + '">0</span></button>' +
        '<button class="post-btn" id="blog-save-btn-' + data.id + '" onclick="blogToggleSave(' + data.id + ', this)">🔖 Enregistrer</button>' +
        '<button class="post-btn" onclick="blogToggleArchive(' + data.id + ')" title="Archiver cet article">📦 Archiver</button>' +
        '<button class="post-btn post-btn-report" onclick="deleteBlogPost(' + data.id + ')" title="Supprimer cet article">🗑️</button>' +
      '</div>' +
      '<div class="post-comments" id="blog-comments-' + data.id + '" style="display:none;">' +
        '<div class="post-comments-list" id="blog-comments-list-' + data.id + '"></div>' +
        '<div class="post-comment-input-row">' +
          '<input type="text" class="post-comment-input" id="blog-comment-input-' + data.id + '" placeholder="Écrire un commentaire…" maxlength="500" onkeydown="if(event.key===\'Enter\'){event.preventDefault();submitBlogComment(' + data.id + ')}">' +
          '<button class="post-comment-send" onclick="submitBlogComment(' + data.id + ')">Envoyer</button>' +
        '</div>' +
      '</div>';
    feed.prepend(card);
  });
}

function blogToggleArchive(postId){
  fetch('/api/blog/' + postId + '/archiver/', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'X-CSRFToken': getCsrf()},
  })
  .then(r => r.json())
  .then(data => {
    if (data.error) { alert(data.error); return; }
    if (data.is_archived){
      const card = document.getElementById('blog-post-' + postId);
      if (card) card.remove();
    }
  });
}

function openMyArchives(){
  document.getElementById('myArchivesModal').style.display = 'flex';
  document.body.style.overflow = 'hidden';
  loadMyArchives();
}
function closeMyArchives(){
  document.getElementById('myArchivesModal').style.display = 'none';
  document.body.style.overflow = '';
}
document.getElementById('myArchivesModal').addEventListener('click', function(e){
  if (e.target === this) closeMyArchives();
});

function loadMyArchives(){
  const list = document.getElementById('myArchivesList');
  fetch('/api/blog/mes-archives/', {credentials: 'same-origin'})
    .then(r => r.json())
    .then(data => renderMyArchives(data.posts || []))
    .catch(() => { list.innerHTML = '<p style="text-align:center;padding:20px;color:var(--txt-s);font-size:.82rem;">Impossible de charger tes archives.</p>'; });
}

function renderMyArchives(posts){
  const list = document.getElementById('myArchivesList');
  if (!posts.length){
    list.innerHTML = '<p style="text-align:center;padding:20px;color:var(--txt-s);font-size:.82rem;">Aucun article archivé pour l\'instant.</p>';
    return;
  }
  list.innerHTML = posts.map(p =>
    '<div style="display:flex;align-items:center;justify-content:space-between;gap:10px;background:var(--blush);border-radius:14px;padding:12px 16px;margin-bottom:8px;">' +
      '<div>' +
        '<div style="font-family:\'Playfair Display\',serif;font-size:.9rem;color:var(--p950);margin-bottom:2px;">' + escHtml(p.title) + '</div>' +
        '<div style="font-size:.68rem;color:var(--txt-s);">' + escHtml(p.category_label) + '</div>' +
      '</div>' +
      '<button class="btn-sm btn-outline-sm" onclick="unarchiveBlogPost(' + p.id + ')">Désarchiver</button>' +
    '</div>'
  ).join('');
}

function unarchiveBlogPost(postId){
  fetch('/api/blog/' + postId + '/archiver/', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'X-CSRFToken': getCsrf()},
  })
  .then(r => r.json())
  .then(data => {
    if (data.error) { alert(data.error); return; }
    loadMyArchives();
    if (!data.is_archived) location.reload();
  });
}

// ── GROUPS AJAX (DASHBOARD) ──
function dashToggleGroup(groupId, btn) {
  fetch('/api/groupes/' + groupId + '/membres/', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'X-CSRFToken': getCsrf()},
  })
  .then(r => r.json())
  .then(data => {
    if (data.error) return;
    btn.classList.toggle('joined', data.is_member);
    btn.textContent = data.is_member ? '✓ Rejoint' : 'Rejoindre';
    const countEl = document.getElementById('dgm-count-' + groupId);
    if (countEl) countEl.textContent = '👥 ' + data.member_count + ' membre' + (data.member_count !== 1 ? 's' : '');
  });
}

function escHtml(str) {
  return (str||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// Theme switcher (setTheme/toggleThemeMenu/restore-on-load) now lives in
// base.html so every page — not just the dashboard — picks up the saved theme.

// ── ACCESSIBILITY SETTINGS (reduced motion / high contrast) ──
function toggleA11ySetting(kind, el){
  const attr = kind==='motion' ? 'data-motion' : 'data-contrast';
  const val  = kind==='motion' ? 'reduced' : 'high';
  const key  = kind==='motion' ? 'sana-motion' : 'sana-contrast';
  const isOn = el.classList.toggle('on');
  if(isOn){ document.documentElement.setAttribute(attr, val); localStorage.setItem(key, val); }
  else{ document.documentElement.removeAttribute(attr); localStorage.removeItem(key); }
}
(function(){
  if(localStorage.getItem('sana-motion')==='reduced'){
    document.documentElement.setAttribute('data-motion','reduced');
    const t=document.getElementById('toggle-motion'); if(t) t.classList.add('on');
  }
  if(localStorage.getItem('sana-contrast')==='high'){
    document.documentElement.setAttribute('data-contrast','high');
    const t=document.getElementById('toggle-contrast'); if(t) t.classList.add('on');
  }
})();
function isReducedMotion(){
  return document.documentElement.getAttribute('data-motion')==='reduced';
}

// ── NOTIFICATION/PRIVACY SETTINGS (persisted server-side) ──
function toggleSetting(el){
  const key = el.dataset.key;
  const value = !el.classList.contains('on');
  el.classList.toggle('on', value);
  fetch('/api/settings/', {
    method: 'POST',
    headers: {'Content-Type':'application/json','X-CSRFToken':getCsrf()},
    body: JSON.stringify({key, value}),
  }).catch(e => {
    console.error('❌ Setting update failed', e);
    el.classList.toggle('on', !value);
  });
}

// ── RIPPLE EFFECT ──
document.addEventListener('click', function(e){
  if(isReducedMotion()) return;
  const el = e.target.closest('.btn-primary-sm,.btn-outline-sm,.btn-post,.chat-send,.nav-item,.mn-item,.theme-opt,.conv-item');
  if(!el) return;
  const rect = el.getBoundingClientRect();
  const ripple = document.createElement('span');
  ripple.className = 'ripple-fx';
  const size = Math.max(rect.width, rect.height);
  ripple.style.width = ripple.style.height = size+'px';
  ripple.style.left = (e.clientX - rect.left - size/2)+'px';
  ripple.style.top  = (e.clientY - rect.top  - size/2)+'px';
  el.appendChild(ripple);
  ripple.addEventListener('animationend', ()=>ripple.remove());
});

// ── MAGNETIC BUTTONS ──
function initMagnetic(selector, strength, maxOffset){
  document.querySelectorAll(selector).forEach(el=>{
    el.addEventListener('mousemove', e=>{
      if(isReducedMotion()) return;
      const r = el.getBoundingClientRect();
      const dx = e.clientX - (r.left + r.width/2);
      const dy = e.clientY - (r.top + r.height/2);
      const x = Math.max(-maxOffset, Math.min(maxOffset, dx*strength));
      const y = Math.max(-maxOffset, Math.min(maxOffset, dy*strength));
      el.style.transform = `translate(${x}px, ${y}px) scale(1.05)`;
    });
    el.addEventListener('mouseleave', ()=>{ el.style.transform = ''; });
  });
}
initMagnetic('.chat-send', 0.35, 8);
initMagnetic('.theme-btn', 0.35, 8);

// ── NOTIFICATION SYSTEM ───────────────────────────────────────────────────────
// VAPID_PUBLIC_KEY is declared inline in dashboard.html (server-rendered value) before this file loads.
let notifSocket = null;
let unreadCount = 0;
let swRegistration = null;

// ── Init ──────────────────────────────────────────────────────────────────────
function initNotifications() {
  connectNotifSocket();
  loadNotifications();
  registerServiceWorkerSilently();
  checkPushStatus();
}

// ── WebSocket (temps réel quand l'appli est ouverte) ──────────────────────────
function connectNotifSocket() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  notifSocket = new WebSocket(`${proto}://${location.host}/ws/notifications/`);
  notifSocket.onmessage = (e) => {
    const notif = JSON.parse(e.data);
    prependNotif(notif);
    playNotifSound();
    updateBadge(++unreadCount);
  };
  notifSocket.onclose = () => setTimeout(connectNotifSocket, 5000);
}

// ── Son de notification (Web Audio API, aucun fichier nécessaire) ─────────────
function playNotifSound() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain); gain.connect(ctx.destination);
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(440, ctx.currentTime + 0.25);
    gain.gain.setValueAtTime(0.18, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.4);
    osc.start(ctx.currentTime); osc.stop(ctx.currentTime + 0.4);
  } catch(e) {}
}

// ── Service Worker (enregistrement silencieux, sans demander de permission) ───
async function registerServiceWorkerSilently() {
  if (!('serviceWorker' in navigator)) return;
  try {
    swRegistration = await navigator.serviceWorker.register('/sw.js');
  } catch(e) {}
}

// ── Vérifier le statut push et adapter l'UI ───────────────────────────────────
async function checkPushStatus() {
  if (!('Notification' in window) || !('serviceWorker' in navigator)) return;
  const perm = Notification.permission;
  const asked = localStorage.getItem('sana-notif-asked');
  const enableBtn = document.getElementById('enablePushBtn');

  if (perm === 'granted') {
    // Déjà accordé → s'assurer qu'on est abonné
    if (enableBtn) enableBtn.style.display = 'none';
    const reg = await navigator.serviceWorker.ready;
    await doSubscribe(reg);
  } else if (perm === 'denied') {
    // Bloqué → afficher bouton pour débloquer manuellement
    if (enableBtn) enableBtn.style.display = 'flex';
    enableBtn.textContent = '🔕 Bloqué';
    enableBtn.title = 'Notifications bloquées. Autorise-les dans les paramètres du navigateur.';
    enableBtn.onclick = () => alert('Les notifications sont bloquées.\n\nPour les activer :\n• Clique sur le cadenas dans la barre d\'adresse\n• Cherche "Notifications" et passe sur "Autoriser"');
  } else {
    // Par défaut → montrer la modale si pas encore demandé
    if (enableBtn) enableBtn.style.display = asked ? 'flex' : 'none';
    if (!asked) setTimeout(showPermModal, 2500);
  }
}

// ── Modale de permission ──────────────────────────────────────────────────────
function showPermModal() {
  if (!('Notification' in window)) return;
  if (Notification.permission !== 'default') return;
  document.getElementById('permOverlay').style.display = 'flex';
}

function overlayClick(e) {
  if (e.target === document.getElementById('permOverlay')) declinePushNotifs();
}

async function enablePushNotifs() {
  document.getElementById('permOverlay').style.display = 'none';
  localStorage.setItem('sana-notif-asked', '1');
  document.getElementById('enablePushBtn').style.display = 'none';
  try {
    const perm = await Notification.requestPermission();
    if (perm !== 'granted') {
      document.getElementById('enablePushBtn').style.display = 'flex';
      return;
    }
    if (!swRegistration) swRegistration = await navigator.serviceWorker.ready;
    await doSubscribe(swRegistration);
  } catch(e) {}
}

function declinePushNotifs() {
  document.getElementById('permOverlay').style.display = 'none';
  localStorage.setItem('sana-notif-asked', '1');
  const btn = document.getElementById('enablePushBtn');
  btn.style.display = 'flex';
  btn.textContent = '📲 Activer';
  btn.onclick = showPermModal;
}

// ── Abonnement push ───────────────────────────────────────────────────────────
async function doSubscribe(reg) {
  if (!('PushManager' in window) || !VAPID_PUBLIC_KEY) return;
  try {
    const existing = await reg.pushManager.getSubscription();
    const sub = existing || await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(VAPID_PUBLIC_KEY),
    });
    await fetch('/api/push/subscribe/', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': getCsrf()},
      body: JSON.stringify(sub.toJSON()),
    });
  } catch(e) {}
}

function urlBase64ToUint8Array(b64) {
  const padding = '='.repeat((4 - b64.length % 4) % 4);
  const base64 = (b64 + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
}

// ── Liste de notifications ────────────────────────────────────────────────────
async function loadNotifications() {
  try {
    const res = await fetch('/api/notifications/');
    const data = await res.json();
    const list = document.getElementById('notifList');
    list.innerHTML = '';
    if (!data.notifications.length) {
      list.innerHTML = '<p class="notif-empty">Aucune notification pour l\'instant</p>';
      return;
    }
    unreadCount = 0;
    data.notifications.forEach(n => {
      if (!n.read) unreadCount++;
      list.appendChild(buildNotifEl(n));
    });
    updateBadge(unreadCount);
  } catch(e) {}
}

function buildNotifEl(n) {
  const icons = {like:'❤️', message:'💬', join:'👋', welcome:'🌸'};
  const div = document.createElement('div');
  div.className = 'notif-item' + (n.read ? '' : ' unread');
  div.dataset.id = n.id;
  div.innerHTML = `
    <div class="notif-icon">${icons[n.type] || '🔔'}</div>
    <div class="notif-content">
      <div class="notif-title">${n.title}</div>
      <div class="notif-body">${n.body}</div>
      <div class="notif-time">${formatNotifTime(n.created_at)}</div>
    </div>`;
  div.onclick = () => markRead(n.id);
  return div;
}

function prependNotif(n) {
  const list = document.getElementById('notifList');
  const empty = list.querySelector('.notif-empty');
  if (empty) empty.remove();
  list.prepend(buildNotifEl({...n, read: false}));
}

// ── Panneau ───────────────────────────────────────────────────────────────────
function toggleNotifPanel() {
  const panel = document.getElementById('notifPanel');
  const isOpen = panel.classList.toggle('open');
  if (isOpen) loadNotifications();
}

function updateBadge(count) {
  const badge = document.getElementById('notifBadge');
  if (count > 0) {
    badge.textContent = count > 99 ? '99+' : count;
    badge.style.display = 'flex';
  } else {
    badge.style.display = 'none';
  }
}

async function markRead(id) {
  try {
    await fetch(`/api/notifications/${id}/read/`, {method:'POST', headers:{'X-CSRFToken':getCsrf()}});
    const el = document.querySelector(`.notif-item[data-id="${id}"]`);
    if (el && el.classList.contains('unread')) {
      el.classList.remove('unread');
      updateBadge(Math.max(0, --unreadCount));
    }
  } catch(e) {}
}

async function markAllRead() {
  try {
    await fetch('/api/notifications/', {method:'PATCH', headers:{'X-CSRFToken':getCsrf()}});
    document.querySelectorAll('.notif-item.unread').forEach(el => el.classList.remove('unread'));
    unreadCount = 0; updateBadge(0);
  } catch(e) {}
}

function formatNotifTime(iso) {
  const d = new Date(iso), now = new Date();
  const diff = Math.floor((now - d) / 60000);
  if (diff < 1) return 'À l\'instant';
  if (diff < 60) return `Il y a ${diff} min`;
  if (diff < 1440) return `Il y a ${Math.floor(diff/60)} h`;
  return d.toLocaleDateString('fr-FR');
}

function getCsrf() {
  return document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
}

// ── Badge polling (every 30s) ─────────────────────────────────────────────────
async function pollUnreadCount() {
  try {
    const res = await fetch('/api/notifications/unread/');
    const data = await res.json();
    unreadCount = data.count;
    updateBadge(unreadCount);
  } catch(e) {}
}

// Auto-mark all as read when panel opens
function toggleNotifPanelAndRead() {
  toggleNotifPanel();
  const panel = document.getElementById('notifPanel');
  if (panel.classList.contains('open')) {
    // Mark all as read after a short delay
    setTimeout(async () => {
      await markAllRead();
    }, 800);
  }
}

// Fermer le panneau au clic extérieur
document.addEventListener('click', (e) => {
  const panel = document.getElementById('notifPanel');
  const btn = document.getElementById('notifBtn');
  if (panel?.classList.contains('open') && !panel.contains(e.target) && !btn?.contains(e.target)) {
    panel.classList.remove('open');
  }
});

document.addEventListener('DOMContentLoaded', () => {
  initNotifications();
  setInterval(pollUnreadCount, 30000);
  setInterval(pollDMUnread, 30000);
  // Auto-open DM if redirected from group chat
  const params = new URLSearchParams(location.search);
  if (params.get('dm')) {
    const uid     = parseInt(params.get('dm'));
    const dmName  = params.get('dm_name')    || 'Anonyme';
    const dmInit  = params.get('dm_initial') || '?';
    // Clean URL without reloading
    history.replaceState({}, '', '/dashboard/');
    openDMWith(uid, dmName, dmInit);
  }
});

// ── DM SYSTEM ─────────────────────────────────────────────────────────────────
let currentDMUserId = null;
let dmPollTimer = null;
let lastDMMsgId = 0;

function openDMPanel() {
  document.getElementById('dmPanel').classList.add('open');
  showDMConvList();
}

function closeDMPanel() {
  document.getElementById('dmPanel').classList.remove('open');
  stopDMPoll();
}

function backToConvList() {
  document.getElementById('dmChatView').style.display = 'none';
  document.getElementById('dmConvView').style.display = 'flex';
  stopDMPoll();
  currentDMUserId = null;
  showDMConvList();
}

async function showDMConvList() {
  document.getElementById('dmConvView').style.display = 'flex';
  document.getElementById('dmChatView').style.display = 'none';
  try {
    const res = await fetch('/api/dm/');
    const data = await res.json();
    const list = document.getElementById('dmConvList');
    list.innerHTML = '';
    if (!data.conversations.length) {
      list.innerHTML = '<p class="dm-empty">Aucune conversation.<br>Clique sur "💬 Message" sous un post pour démarrer.</p>';
      return;
    }
    data.conversations.forEach(c => {
      const div = document.createElement('div');
      div.className = 'dm-conv-item';
      div.innerHTML = `
        <div class="dm-av">${c.initial}</div>
        <div style="flex:1;min-width:0;">
          <div class="dm-conv-name">${c.name}</div>
          <div class="dm-conv-preview">${c.is_me ? 'Toi : ' : ''}${c.last_msg}</div>
        </div>
        ${c.unread > 0 ? `<span class="dm-conv-unread">${c.unread}</span>` : ''}`;
      div.onclick = () => openDMWith(c.user_id, c.name, c.initial);
      list.appendChild(div);
    });
    updateDMBadge(data.unread_total);
  } catch(e) {}
}

function openDMWith(userId, name, initial) {
  currentDMUserId = userId;
  lastDMMsgId = 0;
  document.getElementById('dmConvView').style.display = 'none';
  const chatView = document.getElementById('dmChatView');
  chatView.style.display = 'flex';
  document.getElementById('dmChatAv').textContent = initial || '?';
  document.getElementById('dmChatName').textContent = name || 'Anonyme';
  document.getElementById('dmChatMessages').innerHTML = '';
  document.getElementById('dmPanel').classList.add('open');
  loadDMMessages(true);
  stopDMPoll();
  dmPollTimer = setInterval(() => loadDMMessages(false), 3000);
}

async function loadDMMessages(initial) {
  if (!currentDMUserId) return;
  try {
    const res = await fetch(`/api/dm/${currentDMUserId}/?since=${lastDMMsgId}`);
    const data = await res.json();
    if (data.error) return;
    const area = document.getElementById('dmChatMessages');
    const msgs = data.messages || [];
    let added = 0;
    msgs.forEach(m => {
      if (m.id > lastDMMsgId) {
        lastDMMsgId = m.id;
        area.appendChild(buildDMBubble(m));
        added++;
      }
    });
    if (added > 0 || initial) area.scrollTop = area.scrollHeight;
    if (initial && !msgs.length) {
      area.innerHTML = '<p class="dm-empty">Commence la conversation !</p>';
    }
    if (typeof data.unread_total === 'number') {
      updateDMBadge(data.unread_total);
    }
  } catch(e) {}
}

function buildDMBubble(m) {
  const wrap = document.createElement('div');
  wrap.className = 'dm-bubble-wrap ' + (m.is_me ? 'me' : 'other');
  wrap.dataset.id = m.id;
  const checkHtml = m.is_me ? `<span class="dm-check${m.read ? ' read' : ''}">✓✓</span>` : '';
  wrap.innerHTML = `
    <div class="dm-bubble">${escDM(m.content)}</div>
    <div class="dm-time">${m.sent_at}${checkHtml}</div>`;
  return wrap;
}

function escDM(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

async function dmSend() {
  if (!currentDMUserId) return;
  const input = document.getElementById('dmChatInput');
  const content = input.value.trim();
  if (!content) return;
  input.value = '';
  input.style.height = '';
  try {
    const res = await fetch(`/api/dm/${currentDMUserId}/`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': getCsrf()},
      body: JSON.stringify({content}),
    });
    const m = await res.json();
    if (m.error) return;
    const area = document.getElementById('dmChatMessages');
    const empty = area.querySelector('.dm-empty');
    if (empty) empty.remove();
    if (m.id > lastDMMsgId) {
      lastDMMsgId = m.id;
      area.appendChild(buildDMBubble(m));
      area.scrollTop = area.scrollHeight;
    }
  } catch(e) {}
}

function stopDMPoll() {
  if (dmPollTimer) { clearInterval(dmPollTimer); dmPollTimer = null; }
}

function updateDMBadge(count) {
  const badge = document.getElementById('dmNavBadge');
  if (!badge) return;
  if (count > 0) { badge.textContent = count > 99 ? '99+' : count; badge.style.display = 'inline'; }
  else badge.style.display = 'none';
}

async function loadDMConversationsSection() {
  const container = document.getElementById('dmConvSection');
  if (!container) return;
  try {
    const res = await fetch('/api/dm/');
    const data = await res.json();
    container.innerHTML = '';
    if (!data.conversations.length) {
      container.innerHTML = '<div style="text-align:center;padding:40px 20px;color:var(--txt-s);"><div style="font-size:2.5rem;margin-bottom:12px;opacity:.3;">💬</div><p style="font-size:.85rem;line-height:1.7;">Aucune conversation pour l\'instant.<br>Clique sur <strong>💬 Message</strong> sous un post de la communauté pour démarrer.</p></div>';
      return;
    }
    updateDMBadge(data.unread_total || 0);
    data.conversations.forEach(c => {
      const card = document.createElement('div');
      card.style.cssText = 'display:flex;align-items:center;gap:12px;padding:14px 16px;background:white;border-radius:16px;margin-bottom:10px;cursor:pointer;border:1px solid rgba(194,110,138,.1);transition:all .2s;';
      card.onmouseover = () => card.style.background = 'var(--blush)';
      card.onmouseout  = () => card.style.background = 'white';
      card.innerHTML = `
        <div style="width:44px;height:44px;border-radius:50%;background:linear-gradient(135deg,var(--r300),var(--r500));display:flex;align-items:center;justify-content:center;font-size:.95rem;color:white;font-weight:500;flex-shrink:0;">${c.initial}</div>
        <div style="flex:1;min-width:0;">
          <div style="font-size:.85rem;font-weight:500;color:var(--p950);margin-bottom:3px;">${c.name}</div>
          <div style="font-size:.75rem;color:var(--txt-s);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${c.is_me ? 'Toi : ' : ''}${c.last_msg}</div>
        </div>
        <div style="text-align:right;flex-shrink:0;">
          <div style="font-size:.65rem;color:var(--txt-s);margin-bottom:4px;">${c.sent_at}</div>
          ${c.unread > 0 ? `<span style="background:var(--r500);color:white;border-radius:100px;font-size:.6rem;font-weight:700;padding:2px 7px;">${c.unread}</span>` : ''}
        </div>`;
      card.onclick = () => location.href = '/messages/' + c.user_id + '/';
      container.appendChild(card);
    });
  } catch(e) {
    container.innerHTML = '<p style="text-align:center;padding:20px;color:var(--txt-s);font-size:.8rem;">Erreur de chargement.</p>';
  }
}

async function pollDMUnread() {
  try {
    const res = await fetch('/api/dm/');
    const data = await res.json();
    updateDMBadge(data.unread_total || 0);
  } catch(e) {}
}

// ── JEU MULTIJOUEUR: L'OMBRE PARMI LES LUMIÈRES ──
// Contrairement à Devine l'émotion, ce jeu a sa propre page dédiée (animations
// jour/nuit) plutôt qu'une modale — voir jeux/loup/<code>/.
async function createWerewolfRoom(){
  const msg = document.getElementById('werewolfJoinMsg');
  try{
    const res = await fetch('/api/jeux/loup/creer/', {method:'POST', headers:{'X-CSRFToken':getCsrf()}});
    const data = await res.json();
    if(!res.ok){ msg.textContent = data.error || 'Une erreur est survenue.'; return; }
    location.href = '/jeux/loup/' + data.code + '/';
  }catch(e){
    console.error('❌ Create werewolf room failed', e);
    msg.textContent = 'Une erreur est survenue, réessaie plus tard.';
  }
}

async function joinWerewolfRoom(){
  const input = document.getElementById('joinWerewolfCode');
  const msg = document.getElementById('werewolfJoinMsg');
  const code = input.value.trim().toUpperCase();
  if(!code){ msg.textContent = 'Entre un code de partie.'; return; }
  try{
    const res = await fetch('/api/jeux/loup/rejoindre/', {
      method:'POST',
      headers:{'Content-Type':'application/json','X-CSRFToken':getCsrf()},
      body: JSON.stringify({code}),
    });
    const data = await res.json();
    if(!res.ok){ msg.textContent = data.error || 'Une erreur est survenue.'; return; }
    location.href = '/jeux/loup/' + data.code + '/';
  }catch(e){
    console.error('❌ Join werewolf room failed', e);
    msg.textContent = 'Une erreur est survenue, réessaie plus tard.';
  }
}

// ── JEU MULTIJOUEUR: IMPOSTEUR DES ÉMOTIONS ──
// Page dédiée aussi (thème mascarade/projecteur) — voir jeux/imposteur/<code>/.
async function createImpostorRoom(){
  const msg = document.getElementById('impostorJoinMsg');
  try{
    const res = await fetch('/api/jeux/imposteur/creer/', {method:'POST', headers:{'X-CSRFToken':getCsrf()}});
    const data = await res.json();
    if(!res.ok){ msg.textContent = data.error || 'Une erreur est survenue.'; return; }
    location.href = '/jeux/imposteur/' + data.code + '/';
  }catch(e){
    console.error('❌ Create impostor room failed', e);
    msg.textContent = 'Une erreur est survenue, réessaie plus tard.';
  }
}

async function joinImpostorRoom(){
  const input = document.getElementById('joinImpostorCode');
  const msg = document.getElementById('impostorJoinMsg');
  const code = input.value.trim().toUpperCase();
  if(!code){ msg.textContent = 'Entre un code de partie.'; return; }
  try{
    const res = await fetch('/api/jeux/imposteur/rejoindre/', {
      method:'POST',
      headers:{'Content-Type':'application/json','X-CSRFToken':getCsrf()},
      body: JSON.stringify({code}),
    });
    const data = await res.json();
    if(!res.ok){ msg.textContent = data.error || 'Une erreur est survenue.'; return; }
    location.href = '/jeux/imposteur/' + data.code + '/';
  }catch(e){
    console.error('❌ Join impostor room failed', e);
    msg.textContent = 'Une erreur est survenue, réessaie plus tard.';
  }
}
