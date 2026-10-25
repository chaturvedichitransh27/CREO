// Dependency-free checks for the async save lifecycle; run with node.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/js/brief-save.js'), 'utf8');

function setup() {
  const element = () => ({hidden: true, children: [], textContent: '', disabled: false,
    append(child) { this.children.push(child); }, replaceChildren() { this.children = []; },
    focus() {}, scrollIntoView() {}, addEventListener() {}});
  const list = element(), errors = element(), button = element(), status = element();
  button.textContent = 'Save brief ↗';
  errors.querySelector = () => list;
  const nodes = {'[data-save-errors]': errors, '[data-save-button]': button, '[data-save-status]': status};
  let submit, resolve, reject, calls = 0, redirect;
  const form = {action: 'http://localhost/briefs/new/?draft=edited',
    querySelector: selector => nodes[selector], addEventListener: (name, fn) => {submit = fn;},
    setAttribute() {}, removeAttribute() {}, contains: () => false};
  const context = {addEventListener() {}, document: {querySelector: () => form, getElementById: () => null, createElement: element},
    location: {href: form.action, origin: 'http://localhost', assign: url => {redirect = url;}},
    URL, TypeError, FormData: class { constructor(value) { assert.equal(value, form); } },
    fetch: (url, options) => {
      calls++;
      assert.equal(url, form.action); // Keep the generated draft identity.
      assert.equal(options.method, 'POST');
      assert.equal(options.headers.Accept, 'application/json');
      return new Promise((yes, no) => {resolve = yes; reject = no;});
    }};
  context.window = context;
  vm.runInNewContext(source, context);
  return {button, errors, list, status, submit: () => submit({preventDefault() {}}),
    resolve: result => resolve(result), reject: error => reject(error),
    calls: () => calls, redirect: () => redirect};
}
const response = (status, data) => ({status, ok: status === 200, redirected: false,
  headers: {get: () => 'application/json'}, json: async () => data});

test('repeated clicks issue one request; success navigates only after confirmation', async () => {
  const ui = setup(), pending = ui.submit();
  assert.equal(ui.button.disabled, true);
  assert.equal(ui.button.textContent, 'Saving…');
  assert.equal(ui.redirect(), undefined);
  await ui.submit();
  assert.equal(ui.calls(), 1);
  ui.resolve(response(200, {saved: true, redirect_url: '/briefs/42/'}));
  await pending;
  assert.equal(ui.redirect(), 'http://localhost/briefs/42/');
});

test('validation or persistence errors display and allow correcting/retrying', async () => {
  const ui = setup(), pending = ui.submit();
  ui.resolve(response(422, {saved: false, errors: [{message: 'Your brief could not be saved.'}]}));
  await pending;
  assert.equal(ui.errors.hidden, false);
  assert.equal(ui.list.children[0].textContent, 'Your brief could not be saved.');
  assert.equal(ui.button.disabled, false);
  assert.equal(ui.redirect(), undefined);
});

test('connection loss never reports success and preserves the form for retry', async () => {
  const ui = setup(), pending = ui.submit();
  ui.reject(new TypeError('Failed to fetch'));
  await pending;
  assert.match(ui.list.children[0].textContent, /Check your workspace before retrying/);
  assert.equal(ui.button.disabled, false);
  assert.equal(ui.redirect(), undefined);
});

test('login redirect does not discard the edited form', async () => {
  const ui = setup(), pending = ui.submit();
  ui.resolve({...response(200, {}), redirected: true});
  await pending;
  assert.match(ui.list.children[0].textContent, /Sign in in another tab/);
  assert.equal(ui.redirect(), undefined);
  assert.equal(ui.button.disabled, false);
});
