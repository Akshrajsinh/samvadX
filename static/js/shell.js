/* ===========================================================
   shell.js — shared across feed/explore/profile/chat pages:
   toast helper, modal open/close, notifications dropdown,
   unread badge polling.
   =========================================================== */

const CURRENT_USER = document.body.dataset.username;

function showToast(message, ms = 2600) {
  const toast = document.getElementById('toast');
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add('show');
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => toast.classList.remove('show'), ms);
}

function openModal(id) {
  document.getElementById(id)?.classList.add('open');
}
function closeModal(id) {
  document.getElementById(id)?.classList.remove('open');
}

document.addEventListener('click', (e) => {
  const closeBtn = e.target.closest('[data-close]');
  if (closeBtn) closeModal(closeBtn.dataset.close);
  if (e.target.classList?.contains('modal-overlay')) e.target.classList.remove('open');
});

function timeAgo(iso) {
  if (!iso) return '';
  const date = new Date(iso.replace(' ', 'T'));
  const diffSec = Math.floor((Date.now() - date.getTime()) / 1000);
  if (diffSec < 60) return 'now';
  if (diffSec < 3600) return Math.floor(diffSec / 60) + 'm';
  if (diffSec < 86400) return Math.floor(diffSec / 3600) + 'h';
  if (diffSec < 604800) return Math.floor(diffSec / 86400) + 'd';
  return date.toLocaleDateString();
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str ?? '';
  return div.innerHTML;
}

/* ---- Notifications dropdown ---- */
const notifBellBtn = document.getElementById('notifBellBtn');
const notifPanel = document.getElementById('notifPanel');
const notifList = document.getElementById('notifList');
const notifUnreadDot = document.getElementById('notifUnreadDot');
const followRequestsSection = document.getElementById('followRequestsSection');
const followRequestsList = document.getElementById('followRequestsList');

const NOTIF_VERBS = {
  like: 'liked your post',
  comment: 'commented on your post',
  follow: 'started following you',
  follow_request: 'requested to follow you',
};

async function loadFollowRequests() {
  if (!followRequestsList) return 0;
  try {
    const res = await fetch('/api/follow-requests');
    const items = await res.json();
    if (!items.length) {
      followRequestsSection.style.display = 'none';
      followRequestsList.innerHTML = '';
      return 0;
    }
    followRequestsSection.style.display = 'block';
    followRequestsList.innerHTML = items.map(r => `
      <div class="follow-req-item" data-req-row="${r.id}">
        <img class="avatar" src="/uploads/${escapeHtml(r.profilePic || 'default.png')}" alt="" onerror="this.onerror=null;this.src='/uploads/default.png';">
        <a class="follow-req-name" href="/profile/${encodeURIComponent(r.username)}">${escapeHtml(r.username)}</a>
        <div class="follow-req-actions">
          <button class="btn btn-primary btn-sm" data-follow-req-accept="${r.id}">Confirm</button>
          <button class="btn btn-ghost btn-sm" data-follow-req-reject="${r.id}">Delete</button>
        </div>
      </div>
    `).join('');
    return items.length;
  } catch (e) {
    return 0;
  }
}

if (followRequestsList) {
  followRequestsList.addEventListener('click', async (e) => {
    const acceptBtn = e.target.closest('[data-follow-req-accept]');
    const rejectBtn = e.target.closest('[data-follow-req-reject]');
    const btn = acceptBtn || rejectBtn;
    if (!btn) return;

    const reqId = (acceptBtn || rejectBtn).dataset.followReqAccept || (acceptBtn || rejectBtn).dataset.followReqReject;
    const action = acceptBtn ? 'accept' : 'reject';
    btn.disabled = true;

    try {
      await fetch('/api/follow-requests/respond', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: `id=${encodeURIComponent(reqId)}&action=${action}`,
      });
      document.querySelector(`[data-req-row="${reqId}"]`)?.remove();
      showToast(action === 'accept' ? 'Follow request accepted' : 'Request deleted');
      pollUnreadCount();
      // If the accepted follower's profile is open right now, refresh it so
      // their posts appear immediately without a manual reload.
      if (typeof loadProfile === 'function') loadProfile();
    } catch (err) {
      showToast('Could not update request');
      btn.disabled = false;
    }
  });
}

async function loadNotifications() {
  if (!notifList) return;
  await loadFollowRequests();
  try {
    const res = await fetch('/api/notifications');
    const items = await res.json();
    if (!items.length) {
      notifList.innerHTML = '<div class="notif-item"><span class="notif-text">No notifications yet.</span></div>';
      return;
    }
    notifList.innerHTML = items.map(n => `
      <div class="notif-item">
        <img class="avatar" src="/uploads/${escapeHtml(n.profilePic || 'default.png')}" alt="" onerror="this.onerror=null;this.src='/uploads/default.png';">
        <span class="notif-text"><strong>@${escapeHtml(n.actor)}</strong> ${NOTIF_VERBS[n.type] || 'interacted with you'}</span>
        <span class="notif-time">${timeAgo(n.createdAt)}</span>
      </div>
    `).join('');
  } catch (e) { /* network hiccup, ignore */ }
}

async function pollUnreadCount() {
  try {
    const [notifRes, reqRes] = await Promise.all([
      fetch('/api/notifications/unread-count'),
      fetch('/api/follow-requests'),
    ]);
    const notifData = await notifRes.json();
    const reqData = await reqRes.json();
    const hasUnread = notifData.count > 0 || reqData.length > 0;
    if (notifUnreadDot) notifUnreadDot.style.display = hasUnread ? 'block' : 'none';
  } catch (e) { /* ignore */ }
}

if (notifBellBtn) {
  notifBellBtn.addEventListener('click', (e) => {
    e.preventDefault();
    notifPanel.classList.toggle('open');
    if (notifPanel.classList.contains('open')) {
      loadNotifications();
      if (notifUnreadDot) notifUnreadDot.style.display = 'none';
    }
  });
  document.addEventListener('click', (e) => {
    if (!notifPanel.contains(e.target) && e.target !== notifBellBtn && !notifBellBtn.contains(e.target)) {
      notifPanel.classList.remove('open');
    }
  });
  pollUnreadCount();
  setInterval(pollUnreadCount, 15000);
}

/* ---- Chat unread badge (lightweight: based on pending requests) ---- */
async function pollChatBadge() {
  const dot = document.getElementById('chatUnreadDot');
  if (!dot) return;
  try {
    const res = await fetch('/api/requests');
    const reqs = await res.json();
    dot.style.display = reqs.length > 0 ? 'block' : 'none';
  } catch (e) { /* ignore */ }
}
if (document.getElementById('chatUnreadDot')) {
  pollChatBadge();
  setInterval(pollChatBadge, 20000);
}
