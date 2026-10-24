(() => {
  'use strict';
  document.querySelectorAll('[data-reference-upload]').forEach(root => {
    if (root.dataset.ready) return;
    root.dataset.ready = 'true';
    const input = root.querySelector('input[type=file]');
    const previews = root.querySelector('[data-new-previews]');
    const counter = root.querySelector('[data-reference-count]');
    const removals = [...root.querySelectorAll('[name=remove_references]')];
    let files = [], urls = [];
    const sync = () => {
      const transfer = new DataTransfer();
      files.forEach(file => transfer.items.add(file));
      input.files = transfer.files;
    };
    const render = () => {
      urls.forEach(url => URL.revokeObjectURL(url)); urls = [];
      previews.replaceChildren();
      files.forEach((file, index) => {
        const card = document.createElement('div'); card.className = 'reference-thumbnail';
        const img = document.createElement('img');
        const url = URL.createObjectURL(file); urls.push(url); img.src = url;
        img.alt = 'Selected visual reference ' + (index + 1);
        // Unsupported content is still rejected server-side; avoid broken-image icons.
        img.addEventListener('error', () => { img.hidden = true; });
        const remove = document.createElement('button'); remove.type = 'button';
        remove.className = 'button button-outline'; remove.textContent = 'Remove selection ' + (index + 1);
        remove.addEventListener('click', () => { files.splice(index, 1); sync(); render(); input.focus(); });
        card.append(img, remove); previews.append(card);
      });
      const count = removals.filter(box => !box.checked).length + files.length;
      counter.textContent = count + ' of 3 images selected' + (count > 3 ? '. Remove images before saving.' : '.');
      input.setCustomValidity(count > 3 ? 'You can upload a maximum of 3 reference images.' : '');
    };
    input.addEventListener('change', () => { files.push(...input.files); sync(); render(); });
    removals.forEach(box => box.addEventListener('change', render));
    window.addEventListener('pagehide', () => urls.forEach(url => URL.revokeObjectURL(url)));
    render();
  });
  document.querySelectorAll('[data-reference-carousel]').forEach(root => {
    if (root.dataset.ready) return;
    root.dataset.ready = 'true';
    const track = root.querySelector('.reference-track'), slides = [...track.children];
    if (slides.length < 2) return;
    const controls = root.querySelector('.reference-controls'); controls.hidden = false;
    const previous = root.querySelector('[data-previous]'), next = root.querySelector('[data-next]');
    let index = 0;
    const update = () => {
      index = Math.round(track.scrollLeft / track.clientWidth);
      root.querySelector('[data-slide-count]').textContent = (index + 1) + ' / ' + slides.length;
    };
    const move = delta => {
      index = Math.max(0, Math.min(slides.length - 1, index + delta));
      track.scrollTo({left: index * track.clientWidth, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
    };
    previous.addEventListener('click', () => move(-1)); next.addEventListener('click', () => move(1));
    track.addEventListener('scroll', update, {passive: true});
    track.addEventListener('keydown', event => {
      if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') { event.preventDefault(); move(event.key === 'ArrowRight' ? 1 : -1); }
    });
    update();
  });
})();
