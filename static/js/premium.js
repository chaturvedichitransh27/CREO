/* Progressive presentation only. Never intercept application forms or requests. */
(() => {
  'use strict';
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  document.addEventListener('toggle', event => {
    const details = event.target;
    if (!(details instanceof HTMLDetailsElement) || !details.open || reduced.matches) return;
    [...details.children].filter(child => child.tagName !== 'SUMMARY').forEach(child => {
      child.classList.remove('premium-details');
      requestAnimationFrame(() => child.classList.add('premium-details'));
    });
  }, true);
  document.addEventListener('htmx:afterSwap', event => {
    const target = event.detail && event.detail.target;
    if (target && target.id === 'studio-editor' && !reduced.matches) {
      const editor = document.getElementById('studio-editor');
      if (editor) editor.classList.add('premium-saved');
    }
  });
  const cover = document.querySelector('.project-cover');
  if (!cover || typeof HTMLDialogElement === 'undefined') return;
  const viewer = document.createElement('dialog');
  if (typeof viewer.showModal !== 'function') return;
  viewer.className = 'premium-viewer';
  viewer.setAttribute('aria-label', 'Project image preview');
  const close = document.createElement('button'); close.type = 'button'; close.textContent = 'Close preview ×';
  const image = document.createElement('img'); image.alt = cover.alt;
  const caption = document.createElement('p'); caption.textContent = cover.alt;
  viewer.append(close, image, caption); document.body.append(viewer);
  let previousOverflow = '';
  cover.tabIndex = 0; cover.setAttribute('role', 'button'); cover.setAttribute('aria-label', 'Enlarge project image'); cover.setAttribute('aria-haspopup', 'dialog');
  const open = () => {
    if (viewer.open) return;
    image.src = cover.currentSrc || cover.src;
    previousOverflow = document.documentElement.style.overflow;
    try { viewer.showModal(); document.documentElement.style.overflow = 'hidden'; }
    catch (_) { document.documentElement.style.overflow = previousOverflow; }
  };
  cover.addEventListener('click', open);
  cover.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); } });
  close.addEventListener('click', () => viewer.close());
  viewer.addEventListener('close', () => { document.documentElement.style.overflow = previousOverflow; cover.focus({preventScroll:true}); });
  viewer.addEventListener('keydown', event => { if (event.key === 'Tab') { event.preventDefault(); close.focus(); } });
})();
