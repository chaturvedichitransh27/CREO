"""Exercise the disposable Render staging service over HTTPS with synthetic data.

Never calls Gemini. Creates two random test accounts and one clearly labeled test
portfolio; records are disposable and disappear when staging storage is replaced.
No credentials are printed or persisted. Production hosts are explicitly refused.
"""
import http.cookiejar
import re
import secrets
from datetime import datetime, timedelta, timezone
from html import unescape
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener


BASE = 'https://creo-smart-matching-staging.onrender.com'


class Session:
    def __init__(self):
        self.opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, path, data=None, csrf=True, headers=None):
        url = BASE + path
        assert urlsplit(url).hostname == 'creo-smart-matching-staging.onrender.com'
        outgoing = dict(headers or {})
        if data is not None:
            data = dict(data)
            if csrf:
                _, _, page, _ = self.request(path)
                match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page)
                assert match, 'Missing CSRF form token: ' + path
                data['csrfmiddlewaretoken'] = match[1]
            outgoing['Referer'] = url
            outgoing['Content-Type'] = 'application/x-www-form-urlencoded'
        request = Request(url, data=urlencode(data).encode() if data is not None else None, headers=outgoing)
        try:
            response = self.opener.open(request, timeout=55)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, response.geturl(), response.read().decode(), response.headers

    def register(self, role, suffix):
        password = secrets.token_urlsafe(24)
        status, url, _, _ = self.request('/accounts/register/', {
            'first_name': 'Staging QA ' + role, 'username': 'qa_' + role.lower() + suffix,
            'email': role.lower() + suffix + '@example.invalid', 'role': role,
            'password1': password, 'password2': password,
        })
        assert status == 200 and url.endswith('/dashboard/'), 'Registration failed: ' + role


def card(page, name):
    matches = [part for part in re.findall(r'<article class="match-card">(.*?)</article>', page, re.S) if name in part]
    assert len(matches) == 1, 'Synthetic creator missing from results'
    return unescape(matches[0])


def score(part):
    return int(re.search(r'aria-label="Compatibility (\d+) out of 100"', part)[1])


def main():
    creator, brand, anonymous = Session(), Session(), Session()
    status, _, _, headers = anonymous.request('/health/')
    assert status == 200 and headers['X-Content-Type-Options'] == 'nosniff'
    suffix = secrets.token_hex(5)
    creator.register('CREATOR', suffix)
    brand.register('BRAND', suffix)
    print('PASS HTTPS health and creator/brand registration')
    name = 'Staging QA ' + suffix
    terms = {'display_name': name, 'headline': 'Synthetic verification fixture, not a real creator',
             'availability': 'AVAILABLE', 'currency': 'USD', 'price_min': '100', 'price_max': '200',
             'turnaround_days': '4', 'experience_years': '3'}
    status, url, _, _ = creator.request('/creators/edit/', terms)
    assert status == 200 and not url.endswith('/edit/'), 'Profile terms did not save'
    status, url, portfolio_page, _ = creator.request('/portfolio/new/', {
        'title': 'Synthetic QA coffee campaign', 'description': 'Luxury coffee campaign',
        'video_url': 'https://vimeo.com/123456789', 'format': 'PRODUCT', 'aspect_ratio': '1:1',
        'style': 'Luxury', 'workflow': 'Synthetic test: luxury coffee campaign with warm light.',
        'licensing_notes': 'Synthetic test record. Placeholder URL; not a real portfolio or rights claim.',
    })
    assert status == 200 and '/portfolio/new/' not in url, 'Portfolio did not save'
    project_path = re.search(r'href="(/portfolio/\d+/)"', portfolio_page)[1]
    # Use Kolkata's date, matching the configured application timezone.
    today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
    brief = {'title': 'Synthetic staging matching QA ' + suffix, 'description': 'Luxury coffee campaign',
             'deliverables': 'Coffee campaign', 'style': 'Luxury', 'content_type': 'PRODUCT',
             'aspect_ratio': '1:1', 'currency': 'USD', 'budget_min': '100', 'budget_max': '300',
             'min_experience_years': '3', 'deadline': (today + timedelta(days=10)).isoformat(),
             'commercial_use': 'on', 'usage_requirements': 'Synthetic QA only. No commercial publication.',
             'status': 'ACTIVE'}
    status, url, _, _ = brand.request('/briefs/new/', brief)
    assert status == 200 and re.search(r'/briefs/\d+/$', url), 'Brief did not save'
    brief_path = urlsplit(url).path
    matching = brief_path + 'matches/'
    status, _, page, headers = brand.request(matching)
    perfect = card(page, name)
    assert status == 200 and score(perfect) == 100, 'Expected complete fit to score 100'
    assert 'no-store' in headers['Cache-Control']
    for text in ['Budget', 'Deadline', 'Availability', 'Experience', 'Why this match', 'Local relevance fallback', '12 / 12', '10 / 10']:
        assert text in perfect, 'Missing explanation: ' + text
    assert 'Gemini is not enabled' in page
    print('PASS portfolio, brief, 100/100 score, budget, deadline, experience and explanations')

    # Attempting the disabled AI action must keep local matching available.
    status, _, page, _ = brand.request(matching, {'consent': 'on'})
    assert status == 200 and 'not enabled' in page and score(card(page, name)) == 100
    print('PASS disabled Gemini fallback over HTTPS (no generation)')

    terms.update(price_min='1000', price_max='2000', turnaround_days='30')
    creator.request('/creators/edit/', terms)
    _, _, page, _ = brand.request(matching)
    mismatch = card(page, name)
    assert score(mismatch) < 100
    assert 'Typical price exceeds all or part of the budget.' in mismatch
    assert 'Estimated delivery is later than the brief deadline.' in mismatch
    assert '0 / 12' in mismatch and '0 / 10' in mismatch
    changed_score = score(mismatch)
    profile_id = re.search(r'href="/creators/(\d+)/"', mismatch)[1]
    print('PASS live price/turnaround edits invalidate cache and produce missing-requirement warnings')

    # Comparison POST has no standalone form, so reuse a valid token from matching.
    token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page)[1]
    brand.request('/creators/' + profile_id + '/compare/', {'csrfmiddlewaretoken': token}, csrf=False)
    _, _, comparison, _ = brand.request('/creators/compare/?brief=' + brief_path.strip('/').split('/')[-1])
    assert name in comparison and str(changed_score) + '/100' in re.sub(r'<[^>]+>', '', comparison)
    _, _, filtered, headers = brand.request(matching + '?min_score=100', headers={'HX-Request': 'true'})
    assert name not in filtered and '<html' not in filtered and 'HX-Request' in headers['Vary']
    terms.update(availability='UNAVAILABLE')
    creator.request('/creators/edit/', terms)
    _, _, filtered, _ = brand.request(matching + '?available_only=on')
    assert name not in filtered
    print('PASS consistent comparison scores, HTMX score filter and availability filtering')

    assert creator.request(matching)[0] == 403
    assert anonymous.request(matching)[1].split('?')[0].endswith('/accounts/login/')
    assert brand.request(matching, {'consent': 'on'}, csrf=False)[0] == 403
    assert brand.request(project_path + 'edit/')[0] in (403, 404)
    print('PASS role isolation, anonymous redirect, ownership and CSRF enforcement')

    # Restore terms for readable staging review. The test brief stays private.
    terms.update(price_min='100', price_max='200', turnaround_days='4', availability='AVAILABLE')
    creator.request('/creators/edit/', terms)
    _, _, restored, _ = brand.request(matching)
    assert score(card(restored, name)) == 100
    print('PASS restored matching score; staging workflow complete')


if __name__ == '__main__':
    main()
