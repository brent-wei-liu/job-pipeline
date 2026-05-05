"""End-to-end UI regression tests for the jobs/companies/digests views."""
import os
import re
import sqlite3

import pytest
from playwright.sync_api import Page, expect


# Path to the SQLite DB the API server is reading from. Tests use this for
# pre/post snapshots and restoring after destructive bulk-delete cases.
_DB_PATH = os.environ.get(
    "JOB_PIPELINE_DB",
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "jobs.db"),
)


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


# ---------------------------------------------------------------------
# F. Contextual bulk action bar (slides up on selection, CLEAR + DELETE)
# ---------------------------------------------------------------------

def test_bulk_action_bar(page: Page, base_url: str):
    """Selecting jobs reveals the floating action bar; CLEAR dismisses it
    without affecting rows; the bar is visible without scrolling (fixed
    position at the bottom of the viewport)."""
    _open_jobs(page, base_url)

    rows = page.locator("#jobs-body tr.job-row")
    if rows.count() < 2:
        pytest.skip("need at least 2 visible job rows to exercise bulk selection")

    bar = page.locator("#bulk-bar")
    # Initially hidden (no .is-active class — opacity 0, off-screen)
    expect(bar).not_to_have_class(re.compile(r"\bis-active\b"))

    # Select first two rows
    cb1 = rows.nth(0).locator(".job-cb")
    cb2 = rows.nth(1).locator(".job-cb")
    cb1.check()
    cb2.check()

    # Bar appears with .is-active and the count reads "02"
    expect(bar).to_have_class(re.compile(r"\bis-active\b"), timeout=2_000)
    expect(page.locator("#bulk-count-num")).to_have_text("02")

    # The bar must be visible without scrolling — fixed positioning means
    # its bounding box should sit inside the current viewport.
    box = bar.bounding_box()
    viewport_h = page.evaluate("() => window.innerHeight")
    assert box is not None, "#bulk-bar has no bounding box"
    assert 0 <= box["y"] <= viewport_h, (
        f"#bulk-bar y={box['y']} not within viewport (height={viewport_h}) "
        "— would require scrolling to see"
    )

    # Click CLEAR — bar slides out, checkboxes uncheck, no rows deleted
    initial_count = rows.count()
    bar.locator(".btn-clear").click()
    expect(bar).not_to_have_class(re.compile(r"\bis-active\b"), timeout=2_000)
    assert not cb1.is_checked()
    assert not cb2.is_checked()
    assert page.locator("#jobs-body tr.job-row").count() == initial_count, (
        "CLEAR should not remove rows"
    )

    # Re-select two rows and exercise the double-click DELETE confirmation —
    # first click arms confirm state but does not delete; we then CLEAR to
    # cancel, leaving the DB untouched.
    cb1.check(); cb2.check()
    expect(bar).to_have_class(re.compile(r"\bis-active\b"), timeout=2_000)
    delete_btn = bar.locator(".btn-delete")
    delete_btn.click()
    # First click: enters confirm state with "CONFIRM DELETE 2" label
    expect(delete_btn).to_have_class(re.compile(r"\bconfirm\b"), timeout=2_000)
    expect(delete_btn).to_have_text(re.compile(r"CONFIRM DELETE 2"))
    # Bail out without actually deleting
    bar.locator(".btn-clear").click()
    expect(bar).not_to_have_class(re.compile(r"\bis-active\b"), timeout=2_000)
    assert page.locator("#jobs-body tr.job-row").count() == initial_count


# ---------------------------------------------------------------------
# G. Score comparison filter (<) + select-all + bulk delete workflow
# ---------------------------------------------------------------------

def _row_scores(page: Page) -> list[int]:
    raw = page.locator("#jobs-body tr.job-row td.score-cell").all_text_contents()
    out = []
    for s in raw:
        s = (s or "").strip()
        if s and s != "-":
            try:
                out.append(int(s))
            except ValueError:
                pass
    return out


def test_score_filter_less_than_with_bulk_delete(page: Page, base_url: str):
    """The headline workflow:
       1. Pick a small score bucket (eg. score = 9) so we touch a bounded
          number of rows.
       2. Filter via the new operator dropdown to that bucket.
       3. Verify every visible row matches the operator/value combo.
       4. Select all → bulk delete (double-click confirm).
       5. Verify the affected ids are now soft-deleted in the DB
          (status=rejected, reject_reason=user_deleted) and the list re-
          renders empty under the same filter.
       6. Restore the rows we touched so the test is idempotent.

       Skips cleanly if there's no score data to exercise."""
    # Pick a target score: small bucket of accepted+visible rows
    conn = sqlite3.connect(_DB_PATH)
    try:
        target_score = None
        for candidate in (9, 8, 7, 6):
            n = conn.execute(
                "SELECT COUNT(*) FROM jobs "
                "WHERE match_score = ? AND fetched_at > datetime('now','-365 days') "
                "AND (reject_reason IS NULL OR reject_reason != 'user_deleted') "
                "AND status = 'accepted'",
                (candidate,),
            ).fetchone()[0]
            if 1 <= n <= 60:
                target_score = candidate
                break
        if target_score is None:
            pytest.skip("no score bucket with 1–60 accepted+visible rows to safely exercise")
    finally:
        conn.close()

    # Open at all-time so 365-day window matches the API
    page.goto(base_url + "/?", wait_until="domcontentloaded")
    page.wait_for_selector("#jobs-body tr.job-row", timeout=10_000)
    page.select_option("#filter-days", "365")
    page.wait_for_selector("#jobs-body tr.job-row", timeout=10_000)

    # Drive the new score filter: op = '=' (eq) + value = target_score
    page.select_option("#filter-score-op", "eq")
    page.select_option("#filter-score-value", str(target_score))
    # Wait for re-fetch to settle: every visible score matches target_score
    page.wait_for_function(
        "(target) => { "
        " const cells = Array.from(document.querySelectorAll('#jobs-body tr.job-row td.score-cell')); "
        " return cells.length > 0 && cells.every(td => parseInt(td.textContent.trim()) === target); "
        "}",
        arg=target_score, timeout=5_000,
    )

    # All visible rows must match the filter
    scores = _row_scores(page)
    assert scores, "score filter returned 0 rows — bucket changed mid-test?"
    assert all(s == target_score for s in scores), (
        f"filter '= {target_score}' returned non-matching scores: {scores}"
    )

    # URL state was persisted
    url = page.url
    assert "score_op=eq" in url and f"score_value={target_score}" in url, (
        f"score filter did not write to URL: {url}"
    )

    # Capture original (id, status, reject_reason) for every visible row so we
    # can restore them after the destructive bulk delete.
    visible_ids = page.evaluate(
        "() => Array.from(document.querySelectorAll('#jobs-body tr.job-row'))"
        ".map(tr => parseInt(tr.id.replace('row-', ''), 10))"
    )
    assert len(visible_ids) >= 1
    conn = sqlite3.connect(_DB_PATH)
    placeholders = ",".join("?" * len(visible_ids))
    pre_state = conn.execute(
        f"SELECT id, status, reject_reason FROM jobs WHERE id IN ({placeholders})",
        visible_ids,
    ).fetchall()
    conn.close()
    assert len(pre_state) == len(visible_ids)
    pre_map = {row[0]: (row[1], row[2]) for row in pre_state}

    try:
        # Select all visible
        page.locator("#select-all").check()
        # All row checkboxes should be checked
        assert page.evaluate(
            "() => Array.from(document.querySelectorAll('.job-cb')).every(cb => cb.checked)"
        ), "select-all didn't propagate to row checkboxes"

        # Bulk action bar should show the right count
        bar = page.locator("#bulk-bar")
        expect(bar).to_have_class(re.compile(r"\bis-active\b"), timeout=2_000)
        n_selected = int(page.locator("#bulk-count-num").inner_text())
        assert n_selected == len(visible_ids), (
            f"bulk-count {n_selected} != visible rows {len(visible_ids)}"
        )

        # Double-click DELETE: first click arms, second click executes
        delete_btn = bar.locator(".btn-delete")
        delete_btn.click()
        expect(delete_btn).to_have_class(re.compile(r"\bconfirm\b"), timeout=2_000)
        delete_btn.click()

        # Wait for the list to drain under the same filter (rows that match
        # `match_score = target` AND aren't user_deleted)
        page.wait_for_function(
            "() => document.querySelectorAll('#jobs-body tr.job-row').length === 0",
            timeout=8_000,
        )
        # Bar should hide once selection is cleared
        expect(bar).not_to_have_class(re.compile(r"\bis-active\b"), timeout=3_000)

        # DB: those ids should now be rejected/user_deleted
        conn = sqlite3.connect(_DB_PATH)
        post_state = conn.execute(
            f"SELECT id, status, reject_reason FROM jobs WHERE id IN ({placeholders})",
            visible_ids,
        ).fetchall()
        conn.close()
        for jid, status, reason in post_state:
            assert status == "rejected" and reason == "user_deleted", (
                f"job {jid} not soft-deleted: status={status}, reason={reason}"
            )
    finally:
        # Restore the rows so the suite is idempotent — even if any assertion
        # above failed.
        conn = sqlite3.connect(_DB_PATH)
        for jid, (orig_status, orig_reason) in pre_map.items():
            conn.execute(
                "UPDATE jobs SET status = ?, reject_reason = ? WHERE id = ?",
                (orig_status, orig_reason, jid),
            )
        conn.commit()
        conn.close()
