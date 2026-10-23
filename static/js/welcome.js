/* Homepage-only, progressively enhanced intro. No network requests or dependencies. */
(() => {
  'use strict';
  const dialog = document.getElementById('creo-welcome');
  const replay = document.querySelector('[data-welcome-replay]');
  if (!dialog || !replay || typeof dialog.showModal !== 'function') return;
  if (getComputedStyle(dialog).getPropertyValue('--cw-ready').trim() !== '1') return;
  const key = 'creo.welcome.seen.v1';
  const title = dialog.querySelector('#cw-title');
  const description = dialog.querySelector('#cw-description');
  const kicker = dialog.querySelector('#cw-kicker');
  const roles = dialog.querySelector('#cw-roles');
  const showcase = dialog.querySelector('#cw-showcase');
  const features = dialog.querySelector('#cw-features');
  const back = dialog.querySelector('#cw-back');
  const scroller = dialog.querySelector('.cw-scroll');
  const paths = ['M4 5h16v14H4z M8 5v14 M11 9h6 M11 13h4', 'M4 17l5-6 4 4 3-5 4 7 M4 4h16v16H4z M8 8h.01', 'M12 3l3 6 6 3-6 3-3 6-3-6-6-3 6-3z', 'M5 12l4 4L19 6 M4 4h7 M4 4v16h16v-7', 'M4 7h16 M4 12h11 M4 17h7 M17 14l3 3-3 3'];
  const data = {
    creator: {
      heading: 'YOUR CRAFT. MORE POSSIBILITY.', tagline: 'Make your work the beginning of something bigger.',
      note: 'Discovery depends on profile and portfolio fit. Evidence review covers submitted claims, not identity or commercial rights.',
      items: [
        ['Build Your Creative Profile', 'Give your creative identity a home, with your story, specialties and availability.'],
        ['Showcase Your Portfolio', 'Publish images and supported video links. Share the process behind the work.'],
        ['Highlight Skills & AI Tools', 'Show brands the skills and tools you use, on your profile and in your projects.'],
        ['Submit Verification Evidence', 'Attach private proof of portfolio claims for staff review.'],
        ['Get Discovered Through Smart Matching', 'Help brands find your work through explainable compatibility scores.'],
        ['Receive Project Invitations', 'Review a brand’s shared brief and decide whether to accept.'],
        ['Manage Brand Collaborations', 'Keep accepted projects and participant activity together in your workspace.'],
        ['Submit Work & Handle Revisions', 'Share an HTTPS delivery link and respond to requested revisions.'],
        ['Track Project Progress', 'Follow each engagement from invitation through delivery and completion.']
      ]
    },
    brand: {
      heading: 'BIG IDEAS. MEET YOUR PEOPLE.', tagline: 'From a spark of inspiration to a creative collaboration.',
      note: 'Gemini features require configuration and consent. Manual creation and local matching remain available. Scores estimate fit; they do not guarantee quality, rights or delivery.',
      items: [
        ['Gemini-Powered Brief Builder', 'Turn a non-confidential idea into an editable draft when Gemini is enabled.'],
        ['CREO Creative Studio', 'Build interactive moodboards with colors, notes, shot ideas and reference images.'],
        ['AI-Assisted Creative Direction', 'Ask Gemini for suggested styles, palettes and creative concepts to refine.'],
        ['Smart Matching 2.0', 'Find creators through weighted compatibility across your brief’s requirements.'],
        ['Budget, Deadline & Availability', 'Compare self-reported project prices, turnaround and availability.'],
        ['AI Semantic Portfolio Analysis', 'Opt in to Gemini analysis grounded in supplied portfolio text.'],
        ['Explainable Recommendations', 'See the score breakdown, evidence and missing information behind each match.'],
        ['Compare Up to Three Creators', 'Review shortlisted creators and their work side by side.'],
        ['Review Portfolios & Verification', 'Explore workflows and distinguish self-declared claims from reviewed evidence.'],
        ['Invite Creators & Manage Projects', 'Share a ready brief with consent and follow the collaboration in your workspace.'],
        ['Request Revisions & Complete Projects', 'Review submitted delivery links, request changes and mark projects complete.']
      ]
    }
  };
  let opener = null;
  let overflow = '';
  let activeRole = null;
  function reset() {
    delete dialog.dataset.role;
    roles.hidden = false; showcase.hidden = true; back.hidden = true;
    title.innerHTML = 'WHO ARE <em>YOU?</em>';
    description.textContent = 'Two perspectives. One extraordinary creative universe.';
    kicker.textContent = 'HUMAN VISION. NEW POSSIBILITIES.';
    scroller.scrollTop = 0;
  }
  function cleanup() {
    document.documentElement.style.overflow = overflow;
    if (opener && opener.isConnected) opener.focus({preventScroll: true});
  }
  function open() {
    if (dialog.open) return;
    opener = document.activeElement === document.body ? replay : document.activeElement;
    overflow = document.documentElement.style.overflow;
    try {
      reset();
      dialog.showModal();
      document.documentElement.style.overflow = 'hidden';
      try { sessionStorage.setItem(key, '1'); } catch (_) { /* Storage is optional. */ }
    } catch (_) {
      if (dialog.open) dialog.close();
      cleanup();
    }
  }
  function choose(role) {
    const selected = data[role];
    if (!selected) return;
    activeRole = role;
    const fragment = document.createDocumentFragment();
    selected.items.forEach(([name, copy], index) => {
      const card = document.createElement('article');
      card.className = 'cw-feature';
      card.style.setProperty('--cw-delay', `${Math.min(index * 35, 280)}ms`);
      const head = document.createElement('div'); head.className = 'cw-feature-head';
      head.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="${paths[index % paths.length]}"/></svg><span aria-hidden="true">${String(index + 1).padStart(2, '0')}</span>`;
      const label = document.createElement('h3'); label.textContent = name;
      const text = document.createElement('p'); text.textContent = copy;
      card.append(head, label, text); fragment.append(card);
    });
    features.replaceChildren(fragment);
    dialog.dataset.role = role;
    title.textContent = selected.heading;
    description.textContent = selected.tagline;
    kicker.textContent = role === 'creator' ? 'FOR THE MINDS THAT MAKE' : 'FOR THE IDEAS THAT DESERVE MORE';
    dialog.querySelector('#cw-footnote').textContent = selected.note;
    roles.hidden = true; showcase.hidden = false; back.hidden = false;
    scroller.scrollTop = 0;
    title.focus({preventScroll: true});
  }
  dialog.querySelectorAll('[data-welcome-close]').forEach(button => button.addEventListener('click', () => dialog.close()));
  dialog.querySelectorAll('[data-welcome-role]').forEach(button => button.addEventListener('click', () => choose(button.dataset.welcomeRole)));
  back.addEventListener('click', () => { reset(); dialog.querySelector(`[data-welcome-role="${activeRole}"]`).focus({preventScroll: true}); });
  dialog.addEventListener('close', cleanup);
  dialog.addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    const buttons = [...dialog.querySelectorAll('button')].filter(button => button.getClientRects().length && !button.disabled);
    const first = buttons[0], last = buttons[buttons.length - 1];
    if (event.shiftKey && (document.activeElement === first || document.activeElement === title)) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  replay.addEventListener('click', open);
  replay.hidden = false;
  let seen = false;
  try { seen = sessionStorage.getItem(key) === '1'; } catch (_) { /* Show once per page if storage is blocked. */ }
  // Preserve deep links into the homepage and browser-restored scroll positions.
  if (!seen && !location.hash) open();
})();
