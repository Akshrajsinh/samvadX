/* ===========================================================
   profile.js — profile loading, follow/unfollow, edit profile,
   post grid.
   =========================================================== */

const VIEW_USER = document.body.dataset.viewUser;
let editProfileFile = null;

let hasLoadedOnce = false;

async function loadProfile() {
  try {
    const res = await fetch(`/api/profile/${encodeURIComponent(VIEW_USER)}`);
    if (!res.ok) {
      document.getElementById('profileSkeleton').innerHTML =
        '<p style="color:var(--text-dim);">User not found.</p>';
      return;
    }
    const data = await res.json();
    renderProfile(data);
    hasLoadedOnce = true;
  } catch (e) {
    if (!hasLoadedOnce) showToast('Could not load profile');
  }
}

let pendingFollowPoll = null;

function followBtnHtml(data) {
  if (data.isSelf) {
    return `<button class="btn btn-ghost btn-sm" id="editProfileBtn">Edit profile</button>`;
  }
  if (data.followStatus === 'accepted') {
    return `<button class="btn btn-ghost btn-sm" id="followToggleBtn" data-following="true">Following</button>`;
  }
  if (data.followStatus === 'pending') {
    return `<button class="btn btn-ghost btn-sm" id="followToggleBtn" data-following="pending">Requested</button>`;
  }
  return `<button class="btn btn-primary btn-sm" id="followToggleBtn" data-following="false">Follow</button>`;
}

function manageFollowPoll(isPending) {
  clearInterval(pendingFollowPoll);
  pendingFollowPoll = null;
  if (isPending) {
    // While a request is awaiting approval, check periodically so this
    // private account's posts unlock as soon as they accept -- no manual
    // refresh needed.
    pendingFollowPoll = setInterval(async () => {
      const wasPending = true;
      await loadProfile();
      const nowBtn = document.getElementById('followToggleBtn');
      if (wasPending && nowBtn && nowBtn.dataset.following === 'true') {
        showToast(`@${VIEW_USER} accepted your follow request`);
      }
    }, 8000);
  }
}

function renderProfile(data) {
  document.getElementById('profileSkeleton').style.display = 'none';
  document.getElementById('profileContent').style.display = 'block';
  document.getElementById('profileTitle').textContent = data.username;
  document.getElementById('profileAvatar').src = `/uploads/${data.profilePic}`;
  document.getElementById('profileUsername').textContent = data.username;
  document.getElementById('profileActions').innerHTML = followBtnHtml(data);
  document.getElementById('statPosts').textContent = data.postCount;
  document.getElementById('statFollowers').textContent = data.followerCount;
  document.getElementById('statFollowing').textContent = data.followingCount;
  document.getElementById('profileBio').textContent = data.bio;

  manageFollowPoll(!data.isSelf && data.followStatus === 'pending');

  if (data.canViewPosts) {
    document.getElementById('profilePrivateMsg').style.display = 'none';
    const grid = document.getElementById('profileGrid');
    if (!data.posts.length) {
      grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1;">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>
        <h3>No posts yet</h3>
      </div>`;
    } else {
      grid.innerHTML = data.posts.map(p => `
        <a class="grid-tile" href="#" data-post-id="${p.id}">
          ${p.mediaType === 'video'
            ? `<video src="/postimg/${p.image}" muted preload="metadata"></video><span class="video-badge">▶ Video</span>`
            : `<img src="/postimg/${p.image}" alt="" loading="lazy">`}
          <div class="grid-overlay">
            <span><svg viewBox="0 0 24 24" fill="currentColor"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>${p.likeCount}</span>
            <span><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>${p.commentCount}</span>
          </div>
        </a>
      `).join('');
    }
  } else {
    document.getElementById('profileGrid').innerHTML = '';
    document.getElementById('profilePrivateMsg').style.display = 'flex';
  }

  if (data.isSelf) {
    document.getElementById('editProfileBtn').addEventListener('click', () => {
      document.getElementById('editAvatarPreview').src = `/uploads/${data.profilePic}`;
      document.getElementById('editBioInput').value = data.bio;
      document.getElementById('editPrivateCheckbox').checked = data.isPrivate;
      openModal('editProfileModal');
    });
  } else {
    document.getElementById('followToggleBtn').addEventListener('click', handleFollowToggle);
  }
}

async function handleFollowToggle(e) {
  const btn = e.currentTarget;
  const state = btn.dataset.following;
  try {
    if (state === 'false') {
      const res = await fetch('/api/follow', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: `username=${encodeURIComponent(VIEW_USER)}`,
      });
      const data = await res.json();
      if (data.status === 'pending') {
        btn.textContent = 'Requested';
        btn.dataset.following = 'pending';
        btn.className = 'btn btn-ghost btn-sm';
      } else {
        btn.textContent = 'Following';
        btn.dataset.following = 'true';
        btn.className = 'btn btn-ghost btn-sm';
        loadProfile();
      }
    } else {
      await fetch('/api/unfollow', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: `username=${encodeURIComponent(VIEW_USER)}`,
      });
      btn.textContent = 'Follow';
      btn.dataset.following = 'false';
      btn.className = 'btn btn-primary btn-sm';
      loadProfile();
    }
  } catch (err) {
    showToast('Could not update follow status');
  }
}

document.getElementById('editProfileFile').addEventListener('change', (e) => {
  const file = e.target.files[0];
  if (!file) return;
  editProfileFile = file;
  const reader = new FileReader();
  reader.onload = (ev) => { document.getElementById('editAvatarPreview').src = ev.target.result; };
  reader.readAsDataURL(file);
});

document.getElementById('saveProfileBtn').addEventListener('click', async () => {
  const formData = new FormData();
  formData.append('bio', document.getElementById('editBioInput').value);
  formData.append('is_private', document.getElementById('editPrivateCheckbox').checked ? 'true' : 'false');
  if (editProfileFile) formData.append('profile', editProfileFile);

  try {
    await fetch('/api/profile/update', { method: 'POST', body: formData });
    closeModal('editProfileModal');
    showToast('Profile updated');
    editProfileFile = null;
    loadProfile();
  } catch (err) {
    showToast('Could not update profile');
  }
});

/* ---------------------------------------------------------------
   Post detail modal — opens when a post in the grid is clicked.
   Shows the full media (image or video), caption, comments, and
   lets you like / comment / delete right from the profile dashboard.
   --------------------------------------------------------------- */
let activeDetailPostId = null;

document.getElementById('profileGrid').addEventListener('click', (e) => {
  const tile = e.target.closest('[data-post-id]');
  if (!tile) return;
  e.preventDefault();
  openPostDetail(tile.dataset.postId);
});

async function openPostDetail(postId) {
  activeDetailPostId = postId;
  openModal('postDetailModal');

  const mediaEl = document.getElementById('postDetailMedia');
  const commentsEl = document.getElementById('postDetailComments');
  mediaEl.innerHTML = '<div class="skeleton" style="width:100%;height:100%;"></div>';
  commentsEl.innerHTML = '<div class="skeleton" style="height:40px;"></div>';

  try {
    const res = await fetch(`/api/posts/${postId}`);
    if (!res.ok) {
      closeModal('postDetailModal');
      showToast('Could not open that post');
      return;
    }
    const post = await res.json();
    renderPostDetail(post);
  } catch (e) {
    closeModal('postDetailModal');
    showToast('Could not open that post');
  }
}

function renderPostDetail(post) {
  const mediaEl = document.getElementById('postDetailMedia');
  mediaEl.innerHTML = post.mediaType === 'video'
    ? `<video src="/postimg/${escapeHtml(post.image)}" controls autoplay muted loop></video>`
    : `<img id="postDetailImg" src="/postimg/${escapeHtml(post.image)}" alt="">`;

  // Double-click-to-like, Instagram style (image posts only — videos keep
  // native double-click-for-fullscreen behavior so we don't fight the player).
  if (post.mediaType !== 'video') {
    mediaEl.addEventListener('dblclick', () => {
      spawnHeartPop(mediaEl);
      if (!document.getElementById('postDetailLikeBtn').classList.contains('liked')) {
        toggleDetailLike();
      }
    });
  }

  document.getElementById('postDetailAvatar').src = `/uploads/${escapeHtml(post.profilePic)}`;
  const userLink = document.getElementById('postDetailUsername');
  userLink.textContent = post.username;
  userLink.href = `/profile/${encodeURIComponent(post.username)}`;

  const deleteBtn = document.getElementById('postDetailDeleteBtn');
  deleteBtn.style.display = post.isOwner ? '' : 'none';

  const likeBtn = document.getElementById('postDetailLikeBtn');
  likeBtn.classList.toggle('liked', post.likedByMe);
  likeBtn.querySelector('svg').setAttribute('fill', post.likedByMe ? 'currentColor' : 'none');

  document.getElementById('postDetailLikeCount').textContent =
    `${post.likeCount} ${post.likeCount === 1 ? 'like' : 'likes'}`;
  document.getElementById('postDetailTime').textContent = timeAgo(post.createdAt);

  const commentsEl = document.getElementById('postDetailComments');
  const captionHtml = post.caption ? `
    <div class="post-detail-caption">
      <span class="post-username">${escapeHtml(post.username)}</span>${escapeHtml(post.caption)}
    </div>` : '';

  if (!post.comments.length) {
    commentsEl.innerHTML = captionHtml + '<p class="post-detail-empty">No comments yet. Be the first.</p>';
  } else {
    commentsEl.innerHTML = captionHtml + post.comments.map(c => `
      <div class="comment-row" data-comment-id="${c.id}">
        <img class="avatar" src="/uploads/${escapeHtml(c.profilePic || 'default.png')}" alt="" onerror="this.onerror=null;this.src='/uploads/default.png';">
        <div>
          <div class="comment-text"><strong>${escapeHtml(c.username)}</strong>${escapeHtml(c.text)}</div>
          <div class="comment-time">${timeAgo(c.createdAt)}</div>
        </div>
        ${(c.username === CURRENT_USER || post.isOwner) ? `<button class="comment-delete" data-delete-comment="${c.id}">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>
        </button>` : ''}
      </div>
    `).join('');
  }
}

function spawnHeartPop(container) {
  const pop = document.createElement('div');
  pop.className = 'heart-pop';
  pop.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>';
  container.style.position = 'relative';
  container.appendChild(pop);
  setTimeout(() => pop.remove(), 850);
}

async function toggleDetailLike() {
  if (!activeDetailPostId) return;
  try {
    const res = await fetch(`/api/posts/${activeDetailPostId}/like`, { method: 'POST' });
    const data = await res.json();
    const likeBtn = document.getElementById('postDetailLikeBtn');
    likeBtn.classList.toggle('liked', data.liked);
    likeBtn.querySelector('svg').setAttribute('fill', data.liked ? 'currentColor' : 'none');
    document.getElementById('postDetailLikeCount').textContent =
      `${data.likeCount} ${data.likeCount === 1 ? 'like' : 'likes'}`;
    // Keep the grid tile's hover overlay count in sync without a full reload.
    const tile = document.querySelector(`.grid-tile[data-post-id="${activeDetailPostId}"] .grid-overlay span`);
    if (tile) tile.lastChild.textContent = data.likeCount;
  } catch (err) {
    showToast('Could not update like');
  }
}

document.getElementById('postDetailLikeBtn').addEventListener('click', toggleDetailLike);

document.getElementById('postDetailComments').addEventListener('click', async (e) => {
  const delBtn = e.target.closest('[data-delete-comment]');
  if (!delBtn) return;
  const commentId = delBtn.dataset.deleteComment;
  try {
    await fetch(`/api/comments/${commentId}`, { method: 'DELETE' });
    delBtn.closest('.comment-row').remove();
  } catch (err) {
    showToast('Could not delete comment');
  }
});

document.getElementById('postDetailCommentForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!activeDetailPostId) return;
  const input = document.getElementById('postDetailCommentInput');
  const text = input.value.trim();
  if (!text) return;
  try {
    await fetch(`/api/posts/${activeDetailPostId}/comments`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: `text=${encodeURIComponent(text)}`,
    });
    input.value = '';
    openPostDetail(activeDetailPostId);
  } catch (err) {
    showToast('Could not add comment');
  }
});

document.getElementById('postDetailDeleteBtn').addEventListener('click', async () => {
  if (!activeDetailPostId || !confirm('Delete this post?')) return;
  try {
    await fetch(`/api/posts/${activeDetailPostId}`, { method: 'DELETE' });
    closeModal('postDetailModal');
    showToast('Post deleted');
    document.querySelector(`.grid-tile[data-post-id="${activeDetailPostId}"]`)?.remove();
    loadProfile();
  } catch (err) {
    showToast('Could not delete post');
  }
});

loadProfile();
