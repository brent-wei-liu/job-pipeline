"""End-to-end UI regression tests for the jobs/companies/digests views."""
import re

import pytest
from playwright.sync_api import Page, expect


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _open_jobs(page: Page, base_url: str):
    page.goto(base_url, wait_until="domcontentloaded")
    # Wait for the first jobs render
    page.wait_for_selector("#jobs-body tr.job-row", timeout=10_000)


def _score_column_values(page: Page) -> list[float]:
    """Read .score-cell text from each visible job row, in DOM order.
    Empty / dash cells become -1 so they sort to the end like the JS does."""
    cells = page.locator("#jobs-body tr.job-row td.score-cell").all_text_contents()
    out = []
    for raw in cells:
        s = (raw or "").strip()
        try:
            out.append(float(s))
        except ValueError:
            out.append(-1.0)
    return out


# ---------------------------------------------------------------------
# A. The race-condition regression
# ---------------------------------------------------------------------

def test_search_then_top_score_sort_no_race(page: Page, base_url: str):
    """Type in search, click TOP SCORE, verify client-side sort + no extra API call."""
    _open_jobs(page, base_url)

    # Capture API requests fired by the page after we set up the listener
    api_calls: list[str] = []
    page.on("request", lambda req: api_calls.append(req.url) if "/api/jobs" in req.url else None)

    # Type the search and wait for the debounced fetch to complete
    page.fill("#search", "uber")
    # Wait for the debounce + fetch to land + render. The /api/jobs request
    # must show up first; once it has, the new rows are in the DOM.
    page.wait_for_function(
        "Array.from(document.querySelectorAll('#jobs-body tr.job-row td.company'))"
        ".some(td => td.textContent.toLowerCase().includes('uber'))",
        timeout=5_000,
    )

    # Snapshot pre-sort state
    pre_sort_calls = list(api_calls)
    pre_sort_scores = _score_column_values(page)
    assert len(pre_sort_scores) >= 2, "expected at least 2 uber-matching rows"
    assert pre_sort_scores != sorted(pre_sort_scores, reverse=True), (
        "pre-sort scores already happen to be in desc order; pick a different "
        "search term to make the assertion meaningful"
    )

    # Click TOP SCORE FAB
    page.click("#fab-score")
    # Wait for FAB to gain .active class (synchronous in our handler, but
    # give the browser a tick)
    expect(page.locator("#fab-score")).to_have_class(re.compile(r"\bactive\b"), timeout=2_000)

    # Verify ordering
    sorted_scores = _score_column_values(page)
    assert sorted_scores == sorted(sorted_scores, reverse=True), (
        f"scores not in desc order after FAB click: {sorted_scores}"
    )

    # Wait a beat to make sure no late /api/jobs request fires from a
    # leftover debounce or race
    page.wait_for_timeout(500)
    new_calls = [u for u in api_calls if u not in pre_sort_calls]
    assert not new_calls, (
        f"expected 0 new /api/jobs requests after FAB click, got: {new_calls}"
    )

    # Click again — should restore original order, FAB inactive
    page.click("#fab-score")
    expect(page.locator("#fab-score")).not_to_have_class(re.compile(r"\bactive\b"), timeout=2_000)
    restored = _score_column_values(page)
    assert restored == pre_sort_scores, (
        f"second click should restore pre-sort order. before={pre_sort_scores} "
        f"after={restored}"
    )

    # Still no new API calls
    page.wait_for_timeout(300)
    new_calls = [u for u in api_calls if u not in pre_sort_calls]
    assert not new_calls, f"unexpected /api/jobs after toggle off: {new_calls}"


# ---------------------------------------------------------------------
# B. Star toggle persists across reload
# ---------------------------------------------------------------------

def _star_state(page: Page, job_id: str) -> bool:
    """True if starred, False if not. Checks for the .badge-starred element
    rather than raw innerHTML (which differs for SSR-rendered vs JS-set markup
    by whitespace alone)."""
    return page.locator(f"#row-{job_id} .star-btn .badge-starred").count() > 0


def test_star_toggle_persists(page: Page, base_url: str):
    _open_jobs(page, base_url)

    first_row = page.locator("#jobs-body tr.job-row").first
    job_id = first_row.get_attribute("id").replace("row-", "")
    initial = _star_state(page, job_id)

    page.locator(f"#row-{job_id} .star-btn").click()
    page.wait_for_function(
        "(args) => Boolean(document.querySelector('#row-' + args.id "
        "+ ' .star-btn .badge-starred')) !== args.initial",
        arg={"id": job_id, "initial": initial},
        timeout=3_000,
    )
    after_click = _star_state(page, job_id)
    assert after_click != initial

    # Reload — DB persistence check
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(f"#row-{job_id}", timeout=10_000)
    after_reload = _star_state(page, job_id)
    assert after_reload == after_click, (
        f"star state did not persist across reload "
        f"(after_click={after_click}, after_reload={after_reload})"
    )

    # Cleanup: toggle back so the suite is idempotent
    page.locator(f"#row-{job_id} .star-btn").click()
    page.wait_for_function(
        "(args) => Boolean(document.querySelector('#row-' + args.id "
        "+ ' .star-btn .badge-starred')) === args.initial",
        arg={"id": job_id, "initial": initial},
        timeout=3_000,
    )


# ---------------------------------------------------------------------
# C. Pagination LOAD MORE
# ---------------------------------------------------------------------

def test_pagination_load_more(page: Page, base_url: str):
    _open_jobs(page, base_url)

    initial = page.locator("#jobs-body tr.job-row").count()
    if initial < 100:
        pytest.skip(
            f"only {initial} jobs in DB — LOAD MORE bar isn't shown until the "
            "first page (100) is full"
        )

    # Capture scroll position
    pre_scroll = page.evaluate("window.scrollY")

    # Scroll the load-more bar into view, then click
    load_more = page.locator("#load-more-bar button")
    load_more.scroll_into_view_if_needed()
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    post_scroll = page.evaluate("window.scrollY")
    assert post_scroll > pre_scroll, "couldn't scroll down to LOAD MORE"

    load_more.click()
    # Wait for new rows to append
    page.wait_for_function(
        f"document.querySelectorAll('#jobs-body tr.job-row').length > {initial}",
        timeout=5_000,
    )
    after = page.locator("#jobs-body tr.job-row").count()
    assert after > initial, f"LOAD MORE did not add rows: {initial} -> {after}"

    # Scroll position should NOT have reset to 0
    final_scroll = page.evaluate("window.scrollY")
    assert final_scroll > 100, (
        f"scroll position reset to {final_scroll} after LOAD MORE — should stay near bottom"
    )


# ---------------------------------------------------------------------
# D. Companies tab → click company → jumps to filtered jobs
# ---------------------------------------------------------------------

def test_jobs_company_link_navigates_to_companies_tab(page: Page, base_url: str):
    """Click a job row's company link — if that company has been analyzed,
    the page should switch to the COMPANIES tab and that card should be present.
    If no analyzed companies exist among visible rows, skip."""
    _open_jobs(page, base_url)

    # Read which companies are analyzed (the JS exposes analyzedCompanies as a Set)
    analyzed = page.evaluate("() => Array.from(analyzedCompanies)")
    if not analyzed:
        pytest.skip("no analyzed companies — run company-analyze first")

    # Find a visible job row whose company is in the analyzed set
    target_company = None
    rows = page.locator("#jobs-body tr.job-row")
    for i in range(rows.count()):
        company = rows.nth(i).locator("td.company").inner_text().strip()
        if company in analyzed:
            target_company = company
            target_row = rows.nth(i)
            break
    if target_company is None:
        pytest.skip(
            f"none of the {rows.count()} visible rows match an analyzed company "
            f"(analyzed: {analyzed[:3]}{'...' if len(analyzed) > 3 else ''})"
        )

    # Click the company link
    target_row.locator("td.company a.company-link").click()

    # Should switch to companies tab
    page.wait_for_function(
        "() => getComputedStyle(document.getElementById('tab-companies')).display !== 'none'",
        timeout=3_000,
    )
    # The matching company card should exist in #companies-grid
    page.wait_for_selector("#companies-grid .company-card", timeout=5_000)
    matching = page.locator(
        f"#companies-grid .company-card[data-company={target_company!r}]"
    )
    assert matching.count() == 1, (
        f"expected exactly one card for '{target_company}', found {matching.count()}"
    )


# ---------------------------------------------------------------------
# E. Digest drawer / modal opens
# ---------------------------------------------------------------------

def test_digest_drawer_opens(page: Page, base_url: str):
    """Digests in this UI are inline accordion cards (.digest-card) whose
    .digest-body toggles display:none/block when the .digest-header is clicked.
    No modal."""
    _open_jobs(page, base_url)

    # Click DIGESTS top-nav tab
    page.locator(".nav button", has_text="DIGESTS").click()
    page.wait_for_function(
        "() => getComputedStyle(document.getElementById('tab-digests')).display !== 'none'",
        timeout=3_000,
    )

    cards = page.locator(".digest-card")
    page.wait_for_selector(".digest-card", timeout=5_000)
    if cards.count() == 0:
        pytest.skip("no digests in DB to open")

    first_card = cards.first
    digest_id = first_card.get_attribute("id").replace("digest-", "")
    body = page.locator(f"#body-{digest_id}")

    # Initially collapsed
    assert body.evaluate("el => getComputedStyle(el).display") == "none"

    first_card.locator(".digest-header").click()
    page.wait_for_function(
        "(id) => getComputedStyle(document.getElementById('body-' + id)).display !== 'none'",
        arg=digest_id,
        timeout=3_000,
    )
    text = body.inner_text()
    assert len(text) >= 100, (
        f"digest body opened but content suspiciously short ({len(text)} chars): {text[:120]}"
    )

    # Click header again to collapse
    first_card.locator(".digest-header").click()
    page.wait_for_function(
        "(id) => getComputedStyle(document.getElementById('body-' + id)).display === 'none'",
        arg=digest_id,
        timeout=3_000,
    )
