(() => {
  'use strict';
  const form = document.querySelector('[data-brief-save]');
  if (!form) return;
  const errors = form.querySelector('[data-save-errors]');
  const button = form.querySelector('[data-save-button]');
  const status = form.querySelector('[data-save-status]');
  const label = button.textContent;
  let saving = false;
  const showErrors = items => {
    const list = errors.querySelector('ul');
    list.replaceChildren();
    items.forEach(item => {
      const li = document.createElement('li');
      const field = item.field_id && document.getElementById(item.field_id);
      if (field && form.contains(field)) {
        const link = document.createElement('a');
        link.href = '#' + field.id;
        link.textContent = item.message;
        link.addEventListener('click', () => field.focus());
        li.append(link);
      } else li.textContent = item.message;
      list.append(li);
    });
    errors.hidden = false;
    errors.focus();
    errors.scrollIntoView({block: 'center'});
  };
  if (!errors.hidden) errors.focus();
  if (!window.fetch || !window.FormData) return; // Native POST remains supported.
  window.addEventListener('pageshow', event => {
    if (!event.persisted) return;
    saving = false;
    button.disabled = false;
    button.textContent = label;
    form.removeAttribute('aria-busy');
    status.textContent = '';
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (saving) return;
    // Snapshot every current field, checkbox, taxonomy and selected file.
    const body = new FormData(form);
    saving = true;
    button.disabled = true;
    button.textContent = 'Saving…';
    form.setAttribute('aria-busy', 'true');
    errors.hidden = true;
    status.textContent = 'Saving your brief…';
    let navigating = false;
    try {
      const response = await fetch(form.action, {method: 'POST', body,
        headers: {'Accept': 'application/json'}, credentials: 'same-origin'});
      if (response.redirected || response.status === 403) {
        throw new Error('Your sign-in or security token may have expired. Sign in in another tab, then try again. Your edits are still here.');
      }
      if (!response.headers.get('content-type')?.includes('application/json')) {
        throw new Error('The server could not confirm the save. Your edits are still here. Check your workspace before retrying.');
      }
      let result;
      try { result = await response.json(); }
      catch (_) { throw new Error('The server returned an unreadable save confirmation. Your edits are still here. Check your workspace before retrying.'); }
      if (response.ok && result.saved === true && result.redirect_url) {
        const destination = new URL(result.redirect_url, location.href);
        if (destination.origin !== location.origin) throw new Error('The save returned an unexpected destination. Check your workspace before retrying.');
        status.textContent = 'Brief saved. Opening your brief…';
        location.assign(destination.href);
        navigating = true;
      } else {
        showErrors(result.errors?.length ? result.errors : [{message: 'The brief could not be saved. Your edits are still here; please try again.'}]);
      }
    } catch (error) {
      showErrors([{message: error instanceof TypeError
        ? 'The connection was interrupted and the save could not be confirmed. Your edits are still here. Check your workspace before retrying.'
        : error.message}]);
    } finally {
      if (!navigating) {
        saving = false;
        button.disabled = false;
        button.textContent = label;
        form.removeAttribute('aria-busy');
        status.textContent = '';
      }
    }
  });
})();
