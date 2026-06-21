/* ===========================================================
   explore.js — discovery grid + people search
   =========================================================== */

const exploreGrid = document.getElementById('exploreGrid');
const searchInput = document.getElementById('userSearchInput');
const searchResults = document.getElementById('searchResults');

async function loadExplore() {
  try {
    const res = await fetch('/api/explore');
    const posts = await res.json();
    if (!posts.length) {
      exploreGrid.innerHTML = `<div class="empty-state" style="grid-column:1/-1;">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
        <h3>Nothing to explore yet</h3>
        <p>Public posts from the community will show up here.</p>
      </div>`;
      return;
    }
    exploreGrid.innerHTML = posts.map(p => `
      <a class="grid-tile" href="/profile/${encodeURIComponent(p.username)}">
        ${p.mediaType === 'video'
          ? `<video src="/postimg/${escapeHtml(p.image)}" muted preload="metadata"></video><span class="video-badge">▶ Video</span>`
          : `<img src="/postimg/${escapeHtml(p.image)}" alt="" loading="lazy">`}
        <div class="grid-overlay">
          <span>
            <svg viewBox="0 0 24 24" fill="currentColor"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>
            ${p.likeCount}
          </span>
        </div>
      </a>
    `).join('');
  } catch (e) {
    showToast('Could not load explore feed');
  }
}

let searchDebounce;
searchInput.addEventListener('input', () => {
  clearTimeout(searchDebounce);
  const q = searchInput.value.trim();
  if (!q) {
    searchResults.style.display = 'none';
    return;
  }
  searchDebounce = setTimeout(async () => {
    try {
      const res = await fetch(`/api/search-users?q=${encodeURIComponent(q)}`);
      const users = await res.json();
      if (!users.length) {
        searchResults.innerHTML = '<div class="search-result-item" style="color:var(--text-dim);">No users found</div>';
      } else {
        searchResults.innerHTML = users.map(u => `
          <a class="search-result-item" href="/profile/${encodeURIComponent(u.username)}">
            <img class="avatar" src="/uploads/${escapeHtml(u.profilePic || 'default.png')}" alt="" onerror="this.onerror=null;this.src='/uploads/default.png';">
            <span>${escapeHtml(u.username)}</span>
          </a>
        `).join('');
      }
      searchResults.style.display = 'block';
    } catch (e) { /* ignore */ }
  }, 250);
});

document.addEventListener('click', (e) => {
  if (!searchResults.contains(e.target) && e.target !== searchInput) {
    searchResults.style.display = 'none';
  }
});

loadExplore();
