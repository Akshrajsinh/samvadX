/* ===========================================================
   feed.js — feed rendering, infinite scroll, likes, comments,
   create-post modal.
   =========================================================== */

let feedBeforeId = null;
let feedLoading = false;
let feedDone = false;
let activePostIdForComments = null;
let selectedPostFile = null;

const feedList = document.getElementById('feedList');
const feedSentinel = document.getElementById('feedSentinel');

function postCardHtml(p) {
  const topComments = (p.topComments || []).map(c => `
    <div class="post-comment-line"><span class="post-username">${escapeHtml(c.username)}</span>${escapeHtml(c.text)}</div>
  `).join('');

  const mediaHtml = p.mediaType === 'video'
    ? `<div class="post-media-wrap">
         <video class="post-media" src="/postimg/${escapeHtml(p.image)}" controls preload="metadata"></video>
         <span class="video-badge">▶ Video</span>
       </div>`
    : `<div class="post-media-wrap" data-dbl-like="${p.id}">
         <img class="post-media" src="/postimg/${escapeHtml(p.image)}" alt="" loading="lazy" data-open-comments="${p.id}">
       </div>`;

  return `
  <article class="post-card card" data-post-id="${p.id}">
    <div class="post-header">
      <a href="/profile/${encodeURIComponent(p.username)}"><img class="avatar" src="/uploads/${escapeHtml(p.profilePic)}" alt="" onerror="this.onerror=null;this.src='/uploads/default.png';"></a>
      <div class="post-meta">
        <a href="/profile/${encodeURIComponent(p.username)}" class="post-username">${escapeHtml(p.username)}</a>
        ${p.location ? `<div class="post-location">${escapeHtml(p.location)}</div>` : ''}
      </div>
      ${p.isOwner ? `<button class="post-menu-btn" data-delete-post="${p.id}" title="Delete post">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>
      </button>` : ''}
    </div>
    ${mediaHtml}
    <div class="post-actions">
      <button class="like-btn ${p.likedByMe ? 'liked' : ''}" data-like-post="${p.id}">
        <svg viewBox="0 0 24 24" fill="${p.likedByMe ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>
      </button>
      <button data-open-comments="${p.id}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>
      </button>
    </div>
    <div class="post-likes" data-like-count="${p.id}">${p.likeCount} ${p.likeCount === 1 ? 'like' : 'likes'}</div>
    ${p.caption ? `<div class="post-caption"><span class="post-username">${escapeHtml(p.username)}</span>${escapeHtml(p.caption)}</div>` : ''}
    ${p.commentCount > 0 ? `
    <div class="post-comments-preview">
      ${p.commentCount > 2 ? `<button class="view-all" data-open-comments="${p.id}">View all ${p.commentCount} comments</button>` : ''}
      ${topComments}
    </div>` : ''}
    <div class="post-time">${timeAgo(p.createdAt)}</div>
    <form class="add-comment-row" data-quick-comment="${p.id}">
      <input type="text" placeholder="Add a comment..." maxlength="500">
      <button type="submit">Post</button>
    </form>
  </article>`;
}

async function loadFeed() {
  if (feedLoading || feedDone) return;
  feedLoading = true;
  try {
    const url = feedBeforeId ? `/api/feed?before=${feedBeforeId}` : '/api/feed';
    const res = await fetch(url);
    const posts = await res.json();
    if (posts.length === 0) {
      feedDone = true;
      if (!feedBeforeId) {
        feedList.innerHTML = `<div class="empty-state">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 9.5L12 3l9 6.5V20a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1V9.5z"/></svg>
          <h3>Your feed is empty</h3>
          <p>Follow people from Explore, or share your first post.</p>
        </div>`;
      }
      return;
    }
    feedList.insertAdjacentHTML('beforeend', posts.map(postCardHtml).join(''));
    feedBeforeId = posts[posts.length - 1].id;
  } catch (e) {
    showToast('Could not load feed');
  } finally {
    feedLoading = false;
  }
}

const feedObserver = new IntersectionObserver((entries) => {
  if (entries[0].isIntersecting) loadFeed();
}, { rootMargin: '400px' });
feedObserver.observe(feedSentinel);

async function likePostById(postId, { forceLikeOnly = false } = {}) {
  const likeBtn = feedList.querySelector(`[data-like-post="${postId}"]`);
  if (forceLikeOnly && likeBtn && likeBtn.classList.contains('liked')) return; // already liked, no toggle-off
  try {
    const res = await fetch(`/api/posts/${postId}/like`, { method: 'POST' });
    const data = await res.json();
    if (likeBtn) {
      likeBtn.classList.toggle('liked', data.liked);
      likeBtn.querySelector('svg').setAttribute('fill', data.liked ? 'currentColor' : 'none');
    }
    const countEl = feedList.querySelector(`[data-like-count="${postId}"]`);
    if (countEl) countEl.textContent = `${data.likeCount} ${data.likeCount === 1 ? 'like' : 'likes'}`;
  } catch (err) {
    showToast('Could not update like');
  }
}

feedList.addEventListener('click', async (e) => {
  const likeBtn = e.target.closest('[data-like-post]');
  if (likeBtn) {
    await likePostById(likeBtn.dataset.likePost);
    return;
  }

  const openComments = e.target.closest('[data-open-comments]');
  if (openComments) {
    activePostIdForComments = openComments.dataset.openComments;
    openCommentsModal(activePostIdForComments);
    return;
  }

  const deleteBtn = e.target.closest('[data-delete-post]');
  if (deleteBtn) {
    if (!confirm('Delete this post?')) return;
    const postId = deleteBtn.dataset.deletePost;
    try {
      await fetch(`/api/posts/${postId}`, { method: 'DELETE' });
      feedList.querySelector(`[data-post-id="${postId}"]`)?.remove();
      showToast('Post deleted');
    } catch (err) { showToast('Could not delete post'); }
  }
});

/* ---- Double-tap / double-click to like, Instagram style ---- */
feedList.addEventListener('dblclick', async (e) => {
  const wrap = e.target.closest('[data-dbl-like]');
  if (!wrap) return;
  const postId = wrap.dataset.dblLike;
  spawnHeartPop(wrap);
  await likePostById(postId, { forceLikeOnly: true });
});

function spawnHeartPop(container) {
  const pop = document.createElement('div');
  pop.className = 'heart-pop';
  pop.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>';
  container.appendChild(pop);
  setTimeout(() => pop.remove(), 850);
}

feedList.addEventListener('submit', async (e) => {
  const form = e.target.closest('[data-quick-comment]');
  if (!form) return;
  e.preventDefault();
  const postId = form.dataset.quickComment;
  const input = form.querySelector('input');
  const text = input.value.trim();
  if (!text) return;
  try {
    await fetch(`/api/posts/${postId}/comments`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: `text=${encodeURIComponent(text)}`,
    });
    input.value = '';
    showToast('Comment added');
  } catch (err) { showToast('Could not add comment'); }
});

/* ---- Comments modal ---- */
async function openCommentsModal(postId) {
  openModal('commentsModal');
  const list = document.getElementById('commentsList');
  list.innerHTML = '<div class="skeleton" style="height:60px;"></div>';
  try {
    const res = await fetch(`/api/posts/${postId}/comments`);
    const comments = await res.json();
    if (!comments.length) {
      list.innerHTML = '<p style="color:var(--text-dim);font-size:0.85rem;">No comments yet. Be the first.</p>';
      return;
    }
    list.innerHTML = comments.map(c => `
      <div class="comment-row" data-comment-id="${c.id}">
        <img class="avatar" src="/uploads/${escapeHtml(c.profilePic || 'default.png')}" alt="" onerror="this.onerror=null;this.src='/uploads/default.png';">
        <div>
          <div class="comment-text"><strong>${escapeHtml(c.username)}</strong>${escapeHtml(c.text)}</div>
          <div class="comment-time">${timeAgo(c.createdAt)}</div>
        </div>
        ${c.username === CURRENT_USER ? `<button class="comment-delete" data-delete-comment="${c.id}">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>
        </button>` : ''}
      </div>
    `).join('');
  } catch (e) {
    list.innerHTML = '<p style="color:var(--text-dim);">Could not load comments.</p>';
  }
}

document.getElementById('commentsList').addEventListener('click', async (e) => {
  const delBtn = e.target.closest('[data-delete-comment]');
  if (!delBtn) return;
  const commentId = delBtn.dataset.deleteComment;
  try {
    await fetch(`/api/comments/${commentId}`, { method: 'DELETE' });
    delBtn.closest('.comment-row').remove();
  } catch (err) { showToast('Could not delete comment'); }
});

document.getElementById('submitCommentBtn').addEventListener('click', async () => {
  const input = document.getElementById('newCommentInput');
  const text = input.value.trim();
  if (!text || !activePostIdForComments) return;
  try {
    await fetch(`/api/posts/${activePostIdForComments}/comments`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: `text=${encodeURIComponent(text)}`,
    });
    input.value = '';
    openCommentsModal(activePostIdForComments);
  } catch (err) { showToast('Could not add comment'); }
});

/* ---- Create post modal ---- */
document.getElementById('openCreatePost').addEventListener('click', () => openModal('createPostModal'));

document.getElementById('postUploadDrop').addEventListener('click', (e) => {
  if (e.target.tagName !== 'INPUT') document.getElementById('postImageInput').click();
});

document.getElementById('postImageInput').addEventListener('change', (e) => {
  const file = e.target.files[0];
  if (!file) return;
  selectedPostFile = file;
  const isVideo = file.type.startsWith('video/');
  const hint = document.getElementById('postMediaTypeHint');
  hint.style.display = 'inline-block';
  hint.textContent = isVideo ? '🎬 Video' : '📷 Photo';

  const url = URL.createObjectURL(file);
  document.getElementById('postUploadText').innerHTML = isVideo
    ? `<video src="${url}" muted controls style="max-width:100%;max-height:220px;border-radius:var(--radius-sm);"></video>`
    : `<img src="${url}" alt="">`;
});

document.getElementById('submitPostBtn').addEventListener('click', async () => {
  if (!selectedPostFile) {
    showToast('Choose a photo or video first');
    return;
  }
  const btn = document.getElementById('submitPostBtn');
  btn.disabled = true;
  btn.textContent = 'Sharing...';

  const formData = new FormData();
  formData.append('image', selectedPostFile);
  formData.append('caption', document.getElementById('postCaption').value);
  formData.append('location', document.getElementById('postLocation').value);

  try {
    const res = await fetch('/api/posts', { method: 'POST', body: formData });
    const data = await res.json();
    if (data.error) {
      showToast(data.error);
    } else {
      showToast(data.mediaType === 'video' ? 'Video shared' : 'Post shared');
      closeModal('createPostModal');
      document.getElementById('postCaption').value = '';
      document.getElementById('postLocation').value = '';
      document.getElementById('postUploadText').textContent = 'Click to choose a photo or video';
      document.getElementById('postMediaTypeHint').style.display = 'none';
      selectedPostFile = null;
      feedBeforeId = null;
      feedDone = false;
      feedList.innerHTML = '';
      loadFeed();
    }
  } catch (err) {
    showToast('Could not share post');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Share';
  }
});

loadFeed();
