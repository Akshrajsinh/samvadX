/* ===========================================================
   chat.js — contacts/requests/people tabs, WebSocket messaging,
   encrypted-thread UI (safety number), disappearing messages,
   replies, reactions, typing indicator, image attachments.
   =========================================================== */

let activePartner = null;
let activeTab = 'chats';
let socket = null;
let typingTimeout = null;
let replyToId = null;
let selectedTtl = 0;
let attachFile = null;
let usersCache = [];
let lastMessageByUser = {};

const contactsList = document.getElementById('contactsList');
const messagesPane = document.getElementById('messagesPane');
const threadEmpty = document.getElementById('threadEmpty');
const threadActive = document.getElementById('threadActive');
const messageInput = document.getElementById('messageInput');
const sendBtn = document.getElementById('sendBtn');

/* ---------------------------------------------------------------
   Emoji system — quick reactions + a full picker shared between
   the composer's emoji button and the "more" option in the
   per-message reaction bar.
   --------------------------------------------------------------- */
const QUICK_REACTIONS = ['❤️', '😂', '👍', '😮', '😢', '🙏'];
const EMOJI_CATEGORIES = {
  'Smileys': ['😀','😁','😂','🤣','😊','😍','😘','😎','🤩','😉','😇','🙂','🥰','😏','🤔','😴','🥳','🤗'],
  'Reactions': ['👍','👎','👏','🙌','🤝','💪','🙏','✌️','🤞','👌','🫶','💯','🔥','✨','🎉','⚡','💔','❤️'],
  'Faces & Feelings': ['😢','😭','😡','😱','😨','😅','😬','🤯','🥺','😤','🤤','😷','🤧','🥶','🥵','😈','👻','💀'],
  'Animals & Nature': ['🐶','🐱','🦊','🐼','🦁','🐸','🐧','🦋','🌸','🌻','🌙','⭐','☀️','🌈','🍀','🌊','🔥','❄️'],
};

let activeEmojiPanel = null;
let activeReactionBar = null;

function buildEmojiPanel(onPick) {
  const panel = document.createElement('div');
  panel.className = 'emoji-panel';
  panel.innerHTML = Object.entries(EMOJI_CATEGORIES).map(([label, emojis]) => `
    <div class="emoji-panel-cat-label">${label}</div>
    <div class="emoji-panel-grid">
      ${emojis.map(em => `<button type="button" data-emoji="${em}">${em}</button>`).join('')}
    </div>
  `).join('');
  panel.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-emoji]');
    if (!btn) return;
    onPick(btn.dataset.emoji);
  });
  return panel;
}

function closeEmojiPanel() {
  activeEmojiPanel?.remove();
  activeEmojiPanel = null;
  document.getElementById('emojiBtn')?.classList.remove('active');
}

function openEmojiPanel(anchorEl, onPick, { openUpward = true } = {}) {
  closeEmojiPanel();
  closeReactionBar();
  const panel = buildEmojiPanel((emoji) => { onPick(emoji); closeEmojiPanel(); });
  document.body.appendChild(panel);
  panel.classList.add('open');

  const rect = anchorEl.getBoundingClientRect();
  const panelWidth = 280;
  let left = rect.left;
  if (left + panelWidth > window.innerWidth - 12) left = window.innerWidth - panelWidth - 12;
  panel.style.left = `${Math.max(12, left)}px`;
  if (openUpward) {
    panel.style.top = `${rect.top - panel.offsetHeight - 10}px`;
    if (parseFloat(panel.style.top) < 10) panel.style.top = `${rect.bottom + 10}px`;
  } else {
    panel.style.top = `${rect.bottom + 8}px`;
  }
  activeEmojiPanel = panel;
}

function closeReactionBar() {
  activeReactionBar?.remove();
  activeReactionBar = null;
}

function sendReaction(msgId, emoji) {
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(`REACT|${msgId}|${emoji}`);
  }
}

function openReactionBar(msgRow, msgId) {
  closeReactionBar();
  closeEmojiPanel();
  const bar = document.createElement('div');
  bar.className = 'reaction-bar';
  bar.innerHTML = QUICK_REACTIONS.map(em => `<button type="button" data-quick-react="${em}">${em}</button>`).join('')
    + `<button type="button" class="reaction-more-btn" title="More emoji">+</button>`;
  bar.addEventListener('click', (e) => {
    const quick = e.target.closest('[data-quick-react]');
    if (quick) {
      sendReaction(msgId, quick.dataset.quickReact);
      closeReactionBar();
      return;
    }
    if (e.target.closest('.reaction-more-btn')) {
      openEmojiPanel(bar, (emoji) => { sendReaction(msgId, emoji); closeReactionBar(); });
    }
  });
  msgRow.appendChild(bar);
  if (msgRow.getBoundingClientRect().top - messagesPane.getBoundingClientRect().top < 60) {
    bar.classList.add('below');
  }
  activeReactionBar = bar;
}

document.addEventListener('click', (e) => {
  if (activeEmojiPanel && !activeEmojiPanel.contains(e.target) &&
      e.target.id !== 'emojiBtn' && !e.target.closest('.reaction-more-btn')) {
    closeEmojiPanel();
  }
  if (activeReactionBar && !activeReactionBar.contains(e.target) && !e.target.closest('[data-react]')) {
    closeReactionBar();
  }
});

/** Quick check used to give emoji-only messages (1-6 emoji, no other text)
 *  a bigger, borderless bubble — the way most modern chat apps do. */
function isEmojiOnly(text) {
  if (!text || text.length > 32) return false;
  return /^(\p{Extended_Pictographic}|\u200d|\ufe0f|\s){1,6}$/u.test(text.trim()) && /\p{Extended_Pictographic}/u.test(text);
}

/* ---------------------------------------------------------------
   WebSocket connection
   --------------------------------------------------------------- */
function connectSocket() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  socket = new WebSocket(`${proto}://${location.host}/ws/${encodeURIComponent(CURRENT_USER)}`);

  socket.onmessage = (event) => {
    let data;
    try { data = JSON.parse(event.data); } catch (e) { return; }
    handleSocketMessage(data);
  };

  socket.onclose = () => {
    setTimeout(connectSocket, 2000);
  };
}

function handleSocketMessage(data) {
  if (data.type === 'chat') {
    const partner = data.sender === CURRENT_USER ? data.receiver : data.sender;
    lastMessageByUser[partner] = { text: data.message, sentAt: data.sentAt };
    if (activePartner === partner) {
      appendMessage(data, data.sender === CURRENT_USER);
      scrollMessagesToBottom();
      removeTypingIndicator();
    } else if (data.sender !== CURRENT_USER) {
      showToast(`New message from @${data.sender}`);
    }
    if (activeTab === 'chats') renderContactsList();
  } else if (data.type === 'typing') {
    if (activePartner === data.sender) showTypingIndicator();
  } else if (data.type === 'seen') {
    const row = messagesPane.querySelector(`[data-msg-id="${data.id}"] .seen-indicator`);
    if (row) row.textContent = 'Seen';
  } else if (data.type === 'delete') {
    messagesPane.querySelector(`[data-msg-id="${data.id}"]`)?.remove();
  } else if (data.type === 'edit') {
    const bubble = messagesPane.querySelector(`[data-msg-id="${data.id}"] .msg-text`);
    if (bubble) bubble.textContent = data.message;
  } else if (data.type === 'react') {
    let chip = messagesPane.querySelector(`[data-msg-id="${data.id}"] .msg-reaction-chip`);
    const row = messagesPane.querySelector(`[data-msg-id="${data.id}"]`);
    if (row && !chip) {
      chip = document.createElement('div');
      chip.className = 'msg-reaction-chip';
      row.querySelector('.msg-bubble').appendChild(chip);
    }
    if (chip) chip.textContent = data.emoji;
  } else if (data.type === 'burn') {
    const row = messagesPane.querySelector(`[data-msg-id="${data.id}"]`);
    if (row) {
      const notice = document.createElement('div');
      notice.className = 'burn-notice';
      notice.textContent = 'This message has disappeared';
      row.replaceWith(notice);
    }
  } else if (data.type === 'status') {
    updatePresenceDot(data.user, data.online);
  } else if (data.type === 'system') {
    const div = document.createElement('div');
    div.className = 'system-msg';
    div.textContent = data.message;
    messagesPane.appendChild(div);
    scrollMessagesToBottom();
  }
}

function updatePresenceDot(username, online) {
  const dot = contactsList.querySelector(`[data-presence-for="${username}"]`);
  if (dot) dot.classList.toggle('online', online);
  if (activePartner === username) {
    document.getElementById('threadStatus').textContent = online ? 'online' : 'offline';
  }
}

/* ---------------------------------------------------------------
   Tabs: chats / requests / people
   --------------------------------------------------------------- */
document.querySelectorAll('.contacts-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.contacts-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    activeTab = tab.dataset.tab;
    if (activeTab === 'chats') renderContactsList();
    else if (activeTab === 'requests') renderRequestsList();
    else renderPeopleList();
  });
});

async function getAcceptedPartners() {
  const res = await fetch('/api/users');
  usersCache = await res.json();

  const contactsRes = await fetch('/api/chat-contacts');
  return await contactsRes.json();
}

async function renderContactsList() {
  contactsList.innerHTML = '<div class="skeleton" style="height:50px;margin-bottom:8px;"></div>'.repeat(4);
  const partners = await getAcceptedPartners();
  if (!partners.length) {
    contactsList.innerHTML = `<div class="empty-state">
      <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>
      <h3>No conversations yet</h3>
      <p>Go to the People tab to send a chat request.</p>
    </div>`;
    return;
  }
  contactsList.innerHTML = partners.map(u => {
    const preview = lastMessageByUser[u.username];
    return `
    <div class="contact-row ${activePartner === u.username ? 'active' : ''}" data-open-thread="${u.username}">
      <div class="avatar-wrap">
        <img class="avatar" src="/uploads/${escapeHtml(u.profile)}" alt="" onerror="this.onerror=null;this.src='/uploads/default.png';">
        <span class="presence-dot" data-presence-for="${u.username}"></span>
      </div>
      <div class="contact-meta">
        <div class="contact-name">${escapeHtml(u.username)}</div>
        <div class="contact-preview">${preview ? escapeHtml(preview.text).slice(0, 40) : 'Start the conversation'}</div>
      </div>
    </div>`;
  }).join('');
}

async function renderRequestsList() {
  contactsList.innerHTML = '<div class="skeleton" style="height:50px;margin-bottom:8px;"></div>'.repeat(2);
  const res = await fetch('/api/requests');
  const requests = await res.json();
  document.getElementById('reqBadge').style.display = requests.length ? 'inline-flex' : 'none';
  document.getElementById('reqBadge').textContent = requests.length;

  if (!requests.length) {
    contactsList.innerHTML = `<div class="empty-state">
      <h3>No pending requests</h3>
      <p>Chat requests from other people will appear here.</p>
    </div>`;
    return;
  }

  const usersRes = await fetch('/api/users');
  const allUsers = await usersRes.json();
  const findProfile = (name) => allUsers.find(u => u.username === name)?.profile || 'default.png';

  contactsList.innerHTML = requests.map(r => `
    <div class="request-row">
      <img class="avatar" src="/uploads/${escapeHtml(findProfile(r.sender))}" alt="" onerror="this.onerror=null;this.src='/uploads/default.png';">
      <div class="req-name">${escapeHtml(r.sender)}</div>
      <div class="request-actions">
        <button class="btn btn-primary btn-sm" data-accept-req="${r.id}">Accept</button>
        <button class="btn btn-ghost btn-sm" data-reject-req="${r.id}">Decline</button>
      </div>
    </div>
  `).join('');
}

async function renderPeopleList() {
  contactsList.innerHTML = '<div class="skeleton" style="height:50px;margin-bottom:8px;"></div>'.repeat(4);
  const res = await fetch('/api/users');
  const allUsers = await res.json();
  usersCache = allUsers;

  contactsList.innerHTML = allUsers.map((u) => {
    let actionHtml = `<button class="btn btn-primary btn-sm" data-send-req="${u.username}">Message</button>`;
    if (u.status === 'pending') {
      actionHtml = u.direction === 'sent'
        ? `<button class="btn btn-ghost btn-sm" disabled>Pending</button>`
        : `<button class="btn btn-primary btn-sm" data-go-requests="true">Respond</button>`;
    } else if (u.status === 'accepted') {
      actionHtml = `<button class="btn btn-ghost btn-sm" data-open-thread="${u.username}">Open chat</button>`;
    }
    return `
    <div class="contact-row" style="cursor:default;">
      <img class="avatar" src="/uploads/${escapeHtml(u.profile)}" alt="" onerror="this.onerror=null;this.src='/uploads/default.png';">
      <div class="contact-meta"><div class="contact-name">${escapeHtml(u.username)}</div></div>
      ${actionHtml}
    </div>`;
  }).join('');
}

contactsList.addEventListener('click', async (e) => {
  const openThread = e.target.closest('[data-open-thread]');
  if (openThread) {
    openThreadWith(openThread.dataset.openThread);
    return;
  }
  const sendReq = e.target.closest('[data-send-req]');
  if (sendReq) {
    const target = sendReq.dataset.sendReq;
    sendReq.disabled = true;
    try {
      const res = await fetch('/api/send-request', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: `receiver=${encodeURIComponent(target)}`,
      });
      const data = await res.json();
      showToast(data.message);
      renderPeopleList();
    } catch (err) { showToast('Could not send request'); }
    return;
  }
  const goRequests = e.target.closest('[data-go-requests]');
  if (goRequests) {
    document.querySelector('[data-tab="requests"]').click();
    return;
  }
  const acceptReq = e.target.closest('[data-accept-req]');
  if (acceptReq) {
    const id = acceptReq.dataset.acceptReq;
    try {
      const res = await fetch('/api/accept-request', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: `id=${id}`,
      });
      const data = await res.json();
      showToast(data.message);
      renderRequestsList();
      pollChatBadge();
    } catch (err) { showToast('Could not accept request'); }
    return;
  }
  const rejectReq = e.target.closest('[data-reject-req]');
  if (rejectReq) {
    const id = rejectReq.dataset.rejectReq;
    try {
      const res = await fetch('/api/reject-request', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: `id=${id}`,
      });
      const data = await res.json();
      showToast(data.message);
      renderRequestsList();
      pollChatBadge();
    } catch (err) { showToast('Could not decline request'); }
  }
});

/* ---------------------------------------------------------------
   New chat modal
   --------------------------------------------------------------- */
document.getElementById('newChatBtn').addEventListener('click', async () => {
  openModal('newChatModal');
  const listEl = document.getElementById('newChatUserList');
  listEl.innerHTML = '<div class="skeleton" style="height:50px;"></div>'.repeat(3);
  const res = await fetch('/api/users');
  const allUsers = await res.json();
  listEl.innerHTML = allUsers.map(u => `
    <div class="contact-row" style="cursor:default;">
      <img class="avatar" src="/uploads/${escapeHtml(u.profile)}" alt="" onerror="this.onerror=null;this.src='/uploads/default.png';">
      <div class="contact-meta"><div class="contact-name">${escapeHtml(u.username)}</div></div>
      <button class="btn btn-primary btn-sm" data-modal-send-req="${u.username}">Message</button>
    </div>
  `).join('');
});

document.getElementById('newChatUserList').addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-modal-send-req]');
  if (!btn) return;
  const target = btn.dataset.modalSendReq;
  try {
    const res = await fetch('/api/send-request', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: `receiver=${encodeURIComponent(target)}`,
    });
    const data = await res.json();
    showToast(data.message);
    closeModal('newChatModal');
  } catch (err) { showToast('Could not send request'); }
});

/* ---------------------------------------------------------------
   Opening a thread
   --------------------------------------------------------------- */
async function openThreadWith(username) {
  activePartner = username;
  threadEmpty.style.display = 'none';
  threadActive.style.display = 'flex';
  document.getElementById('contactsCol').classList.add('hide-on-mobile');
  document.getElementById('threadCol').classList.remove('hide-on-mobile');

  const userInfo = usersCache.find(u => u.username === username) ||
    (await (await fetch('/api/users')).json()).find(u => u.username === username);

  document.getElementById('threadAvatar').src = `/uploads/${userInfo?.profile || 'default.png'}`;
  document.getElementById('threadName').textContent = username;

  cancelReply();
  messagesPane.innerHTML = '<div class="skeleton" style="height:40px;margin-bottom:10px;width:60%;"></div>';

  loadThreadInfo(username);
  await loadHistory(username);
  renderContactsList();
}

document.getElementById('backToContacts').addEventListener('click', () => {
  document.getElementById('contactsCol').classList.remove('hide-on-mobile');
  document.getElementById('threadCol').classList.add('hide-on-mobile');
});

async function loadThreadInfo(username) {
  try {
    const res = await fetch(`/api/thread-info?user=${encodeURIComponent(username)}`);
    if (!res.ok) return;
    const data = await res.json();
    document.getElementById('fingerprintCode').textContent = data.fingerprint;
    document.getElementById('threadEstablishedAt').textContent = `Established ${data.establishedAt}`;
  } catch (e) { /* ignore */ }
}

document.getElementById('safetyNumberBtn').addEventListener('click', () => openModal('safetyModal'));
document.getElementById('viewFingerprintLink').addEventListener('click', () => openModal('safetyModal'));

async function loadHistory(username) {
  try {
    const res = await fetch(`/api/history?user=${encodeURIComponent(username)}`);
    const messages = await res.json();
    messagesPane.innerHTML = '';
    if (!messages.length) {
      messagesPane.innerHTML = `<div class="empty-state">
        <h3>No messages yet</h3>
        <p>Say hello — your conversation is end-to-end style encrypted.</p>
      </div>`;
    } else {
      messages.forEach(m => appendMessage(toSocketShape(m), m.sender === CURRENT_USER));
    }
    scrollMessagesToBottom();
  } catch (e) {
    showToast('Could not load message history');
  }
}

function toSocketShape(m) {
  return {
    id: m.id, sender: m.sender, receiver: m.receiver, message: m.message,
    replyTo: m.replyTo, sentAt: m.sentAt, expiresAt: m.expiresAt,
    attachment: m.attachment, reaction: m.reaction,
  };
}

/* ---------------------------------------------------------------
   Rendering a message row
   --------------------------------------------------------------- */
function appendMessage(m, isMine) {
  removeEmptyState();
  const row = document.createElement('div');
  row.className = `msg-row ${isMine ? 'mine' : 'theirs'}`;
  row.dataset.msgId = m.id;

  let replyHtml = '';
  if (m.replyTo) {
    const original = messagesPane.querySelector(`[data-msg-id="${m.replyTo}"] .msg-text`);
    if (original) replyHtml = `<div class="msg-reply-preview">${escapeHtml(original.textContent.slice(0, 60))}</div>`;
  }

  let attachmentHtml = '';
  if (m.attachment) {
    attachmentHtml = `<img class="msg-attachment" src="/uploads/${escapeHtml(m.attachment)}" alt="" onerror="this.onerror=null;this.src='/uploads/default.png';">`;
  }

  let ttlHtml = '';
  if (m.expiresAt) {
    ttlHtml = `<span class="ttl-chip"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>disappearing</span>`;
  }

  row.innerHTML = `
    <div class="msg-bubble ${isEmojiOnly(m.message) ? 'emoji-only' : ''}">
      ${replyHtml}
      <div class="msg-text">${escapeHtml(m.message)}</div>
      ${attachmentHtml}
      ${m.reaction ? `<div class="msg-reaction-chip">${m.reaction}</div>` : ''}
    </div>
    <div class="msg-actions-hover">
      <button data-react="${m.id}" title="React">😀</button>
      <button data-reply="${m.id}" title="Reply">↩</button>
      ${isMine ? `<button data-delete="${m.id}" title="Delete">🗑</button>` : ''}
    </div>
  `;

  const metaRow = document.createElement('div');
  metaRow.className = 'msg-meta-row';
  metaRow.innerHTML = `<span>${timeAgo(m.sentAt)}</span>${ttlHtml}${isMine ? '<span class="seen-indicator"></span>' : ''}`;

  const wrapper = document.createElement('div');
  wrapper.style.display = 'flex';
  wrapper.style.flexDirection = 'column';
  wrapper.style.maxWidth = '100%';
  row.appendChild(metaRow);

  messagesPane.appendChild(row);
}

function removeEmptyState() {
  const empty = messagesPane.querySelector('.empty-state');
  if (empty) empty.remove();
}

function scrollMessagesToBottom() {
  messagesPane.scrollTop = messagesPane.scrollHeight;
}

let typingIndicatorEl = null;
function showTypingIndicator() {
  removeTypingIndicator();
  typingIndicatorEl = document.createElement('div');
  typingIndicatorEl.className = 'typing-indicator';
  typingIndicatorEl.innerHTML = '<span></span><span></span><span></span>';
  messagesPane.appendChild(typingIndicatorEl);
  scrollMessagesToBottom();
  clearTimeout(typingIndicatorEl._timer);
  typingIndicatorEl._timer = setTimeout(removeTypingIndicator, 3000);
}
function removeTypingIndicator() {
  typingIndicatorEl?.remove();
  typingIndicatorEl = null;
}

/* ---------------------------------------------------------------
   Message actions: reply, react, delete
   --------------------------------------------------------------- */
messagesPane.addEventListener('click', (e) => {
  const replyBtn = e.target.closest('[data-reply]');
  if (replyBtn) {
    replyToId = replyBtn.dataset.reply;
    const text = messagesPane.querySelector(`[data-msg-id="${replyToId}"] .msg-text`)?.textContent || '';
    document.getElementById('replyBannerText').textContent = text.slice(0, 80);
    document.getElementById('replyBanner').style.display = 'flex';
    messageInput.focus();
    return;
  }
  const reactBtn = e.target.closest('[data-react]');
  if (reactBtn) {
    const row = reactBtn.closest('.msg-row');
    openReactionBar(row, reactBtn.dataset.react);
    return;
  }
  const deleteBtn = e.target.closest('[data-delete]');
  if (deleteBtn && socket?.readyState === WebSocket.OPEN) {
    socket.send(`DELETE|${deleteBtn.dataset.delete}`);
  }
});

/* Double-click a bubble for an instant ❤️ react, with a little heart burst —
   the same shortcut people already expect from WhatsApp/iMessage/Instagram. */
messagesPane.addEventListener('dblclick', (e) => {
  const bubble = e.target.closest('.msg-bubble');
  if (!bubble) return;
  const row = bubble.closest('.msg-row');
  const pop = document.createElement('div');
  pop.className = 'heart-pop-mini';
  pop.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>';
  row.appendChild(pop);
  setTimeout(() => pop.remove(), 700);
  sendReaction(row.dataset.msgId, '❤️');
});

document.getElementById('cancelReplyBtn').addEventListener('click', cancelReply);
function cancelReply() {
  replyToId = null;
  document.getElementById('replyBanner').style.display = 'none';
}

/* ---------------------------------------------------------------
   Composer: send, typing, TTL picker, attachments
   --------------------------------------------------------------- */
messageInput.addEventListener('input', () => {
  sendBtn.disabled = messageInput.value.trim().length === 0 && !attachFile;
  messageInput.style.height = 'auto';
  messageInput.style.height = Math.min(messageInput.scrollHeight, 120) + 'px';

  if (socket?.readyState === WebSocket.OPEN && activePartner) {
    clearTimeout(typingTimeout);
    socket.send(`TYPING|${activePartner}`);
  }
});

messageInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

sendBtn.addEventListener('click', sendMessage);

async function sendMessage() {
  const text = messageInput.value.trim();
  if ((!text && !attachFile) || !activePartner) return;

  let attachmentName = '';
  if (attachFile) {
    const formData = new FormData();
    formData.append('file', attachFile);
    try {
      const res = await fetch('/api/upload', { method: 'POST', body: formData });
      attachmentName = (await res.text()).trim();
    } catch (e) { showToast('Could not upload attachment'); }
  }

  const finalText = attachmentName ? (text || '[image]') : text;

  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(`CHAT|${activePartner}|${encodeURIComponent(finalText)}|${replyToId || 0}|${selectedTtl}`);
  }

  messageInput.value = '';
  messageInput.style.height = 'auto';
  sendBtn.disabled = true;
  cancelReply();
  attachFile = null;
  document.getElementById('attachInput').value = '';
}

document.getElementById('attachBtn').addEventListener('click', () => document.getElementById('attachInput').click());
document.getElementById('attachInput').addEventListener('change', (e) => {
  attachFile = e.target.files[0] || null;
  if (attachFile) {
    sendBtn.disabled = false;
    showToast(`Attached ${attachFile.name}`);
  }
});

/* Composer emoji picker — inserts the chosen emoji at the cursor position */
const emojiBtn = document.getElementById('emojiBtn');
emojiBtn.addEventListener('click', () => {
  if (activeEmojiPanel) { closeEmojiPanel(); return; }
  openEmojiPanel(emojiBtn, (emoji) => {
    const start = messageInput.selectionStart ?? messageInput.value.length;
    const end = messageInput.selectionEnd ?? messageInput.value.length;
    messageInput.value = messageInput.value.slice(0, start) + emoji + messageInput.value.slice(end);
    const newPos = start + emoji.length;
    messageInput.focus();
    messageInput.setSelectionRange(newPos, newPos);
    messageInput.dispatchEvent(new Event('input'));
  });
  emojiBtn.classList.add('active');
});

/* TTL (disappearing messages) picker */
const ttlBtn = document.getElementById('ttlBtn');
const ttlPicker = document.getElementById('ttlPicker');
ttlBtn.addEventListener('click', () => ttlPicker.classList.toggle('open'));
document.addEventListener('click', (e) => {
  if (!ttlPicker.contains(e.target) && e.target !== ttlBtn && !ttlBtn.contains(e.target)) {
    ttlPicker.classList.remove('open');
  }
});
ttlPicker.querySelectorAll('button').forEach(btn => {
  btn.addEventListener('click', () => {
    selectedTtl = parseInt(btn.dataset.ttl, 10);
    ttlPicker.querySelectorAll('button').forEach(b => b.classList.remove('selected'));
    btn.classList.add('selected');
    ttlPicker.classList.remove('open');
    ttlBtn.style.color = selectedTtl > 0 ? 'var(--amber)' : '';
    if (selectedTtl > 0) showToast('New messages will disappear after the chosen time');
  });
});

/* ---------------------------------------------------------------
   Contacts search filter
   --------------------------------------------------------------- */
document.getElementById('contactsSearchInput').addEventListener('input', (e) => {
  const q = e.target.value.trim().toLowerCase();
  contactsList.querySelectorAll('.contact-row, .request-row').forEach(row => {
    const name = row.querySelector('.contact-name, .req-name')?.textContent.toLowerCase() || '';
    row.style.display = name.includes(q) ? '' : 'none';
  });
});

connectSocket();
renderContactsList();
