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


# ---------------------------------------------------------------------
# H. Mobile viewport: select-all is visible & functional
# ---------------------------------------------------------------------

def test_mobile_select_all_visible(page: Page, base_url: str):
    """At an iPhone 12-ish viewport (375x812) the desktop thead is
    `display:none`, so the only place to find a select-all control is the
    new `.mobile-select-bar`. Verify:
      - The mobile button is rendered, visible, inside the viewport
      - Hit target ≥44px tall
      - Clicking it selects every visible row, opens the bulk action bar
        with the right count, and flips the button label to DESELECT ALL
      - Clicking again deselects everything and closes the bar."""
    page.set_viewport_size({"width": 375, "height": 812})
    page.goto(base_url, wait_until="domcontentloaded")
    page.wait_for_selector("#jobs-body tr.job-row", timeout=10_000)

    rows = page.locator("#jobs-body tr.job-row")
    if rows.count() < 2:
        pytest.skip("need at least 2 visible job rows")

    btn = page.locator("#select-all-mobile")
    expect(btn).to_be_visible(timeout=2_000)

    box = btn.bounding_box()
    assert box is not None, "mobile select-all button has no bounding box"
    assert box["height"] >= 44, (
        f"mobile select-all height={box['height']}px — needs ≥44px touch target"
    )
    # Inside the viewport (no scrolling needed)
    viewport_h = page.evaluate("() => window.innerHeight")
    assert 0 <= box["y"] <= viewport_h - box["height"], (
        f"mobile select-all y={box['y']} outside viewport ({viewport_h}px tall)"
    )

    # The desktop thead checkbox should NOT be visible at this width
    expect(page.locator("#select-all")).to_be_hidden()

    # Click → all visible rows selected, bar appears, label flips
    label = btn.locator(".check-label")
    expect(label).to_have_text("SELECT ALL")
    btn.click()
    expect(btn).to_have_class(re.compile(r"\bis-on\b"), timeout=2_000)
    expect(label).to_have_text("DESELECT ALL")
    visible_count = rows.count()
    bar = page.locator("#bulk-bar")
    expect(bar).to_have_class(re.compile(r"\bis-active\b"), timeout=2_000)
    n = int(page.locator("#bulk-count-num").inner_text())
    assert n == visible_count, f"bulk count {n} != visible rows {visible_count}"
    # Every per-row checkbox should be checked
    assert page.evaluate(
        "() => Array.from(document.querySelectorAll('.job-cb')).every(cb => cb.checked)"
    ), "mobile select-all click did not propagate to row checkboxes"

    # Click again → deselect, bar disappears, label resets
    btn.click()
    expect(btn).not_to_have_class(re.compile(r"\bis-on\b"), timeout=2_000)
    expect(label).to_have_text("SELECT ALL")
    expect(bar).not_to_have_class(re.compile(r"\bis-active\b"), timeout=2_000)
    assert page.evaluate(
        "() => Array.from(document.querySelectorAll('.job-cb')).every(cb => !cb.checked)"
    )


# ---------------------------------------------------------------------
# I. Soft-deleted jobs stay hidden from the default REJECTED view
# ---------------------------------------------------------------------

def test_bulk_deleted_jobs_stay_hidden(page: Page, base_url: str):
    """Pre-condition: pick a small bucket of accepted+visible rows, soft-
    delete them via /api/jobs/bulk-delete (matches what the UI button
    does), then reload under the REJECTED status filter and assert those
    ids are NOT visible. Restore in a finally block."""
    # Pick a tiny bucket so the test is fast and reversible
    conn = sqlite3.connect(_DB_PATH)
    try:
        target_score = None
        for candidate in (9, 8, 7):
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
            pytest.skip("no score bucket with 1–60 accepted+visible rows")
    finally:
        conn.close()

    page.goto(base_url, wait_until="domcontentloaded")
    page.wait_for_selector("#jobs-body tr.job-row", timeout=10_000)
    page.select_option("#filter-days", "365")
    page.select_option("#filter-score-op", "eq")
    page.select_option("#filter-score-value", str(target_score))
    page.wait_for_function(
        "(t) => { const c = Array.from(document.querySelectorAll("
        "'#jobs-body tr.job-row td.score-cell')); "
        "return c.length > 0 && c.every(td => parseInt(td.textContent.trim()) === t); }",
        arg=target_score, timeout=5_000,
    )

    visible_ids = page.evaluate(
        "() => Array.from(document.querySelectorAll('#jobs-body tr.job-row'))"
        ".map(tr => parseInt(tr.id.replace('row-', ''), 10))"
    )
    assert visible_ids, "filter returned 0 rows"

    # Snapshot original state for restoration
    conn = sqlite3.connect(_DB_PATH)
    placeholders = ",".join("?" * len(visible_ids))
    pre_state = {
        row[0]: (row[1], row[2])
        for row in conn.execute(
            f"SELECT id, status, reject_reason FROM jobs WHERE id IN ({placeholders})",
            visible_ids,
        )
    }
    conn.close()

    try:
        # Soft-delete via the same endpoint the UI uses
        import urllib.request, json as _json
        body = _json.dumps({"ids": visible_ids}).encode()
        req = urllib.request.Request(
            base_url + "/api/jobs/bulk-delete",
            data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            assert r.status == 200

        # Reload under REJECTED filter (no score filter — just status=rejected)
        page.goto(base_url + "/?", wait_until="domcontentloaded")
        page.select_option("#filter-status", "rejected")
        page.select_option("#filter-days", "365")
        # Wait for the post-filter render: either rows present or empty render
        page.wait_for_function(
            "() => !document.body.dataset.loading || "
            "document.querySelectorAll('#jobs-body tr.job-row').length > 0",
            timeout=5_000,
        )
        # The deleted ids must NOT appear in the REJECTED view (the headline
        # bug: they used to resurface here)
        leaked = page.evaluate(
            "(ids) => ids.filter(id => "
            "  document.getElementById('row-' + id) !== null)",
            visible_ids,
        )
        assert leaked == [], (
            f"REJECTED filter is leaking user-deleted rows: {leaked[:5]}"
            + (f" (and {len(leaked)-5} more)" if len(leaked) > 5 else "")
        )
    finally:
        conn = sqlite3.connect(_DB_PATH)
        for jid, (orig_status, orig_reason) in pre_state.items():
            conn.execute(
                "UPDATE jobs SET status = ?, reject_reason = ? WHERE id = ?",
                (orig_status, orig_reason, jid),
            )
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------
# J. TRASH filter shows only user_deleted rows
# ---------------------------------------------------------------------

def test_user_deleted_filter_shows_trash(page: Page, base_url: str):
    """Selecting the TRASH option (status=user_deleted) should show only
    rows with reject_reason='user_deleted'. Cross-check against a direct
    SQLite query."""
    conn = sqlite3.connect(_DB_PATH)
    try:
        n_trash = conn.execute(
            "SELECT COUNT(*) FROM jobs "
            "WHERE reject_reason = 'user_deleted' "
            "AND fetched_at > datetime('now','-365 days')"
        ).fetchone()[0]
    finally:
        conn.close()
    if n_trash == 0:
        pytest.skip("no user_deleted rows in DB to exercise TRASH filter")

    page.goto(base_url, wait_until="domcontentloaded")
    page.wait_for_selector("#jobs-body tr.job-row", timeout=10_000)
    page.select_option("#filter-days", "365")
    # Switch to TRASH filter — wait on the actual /api/jobs response so we
    # don't read stale rows from the previous fetch.
    with page.expect_response(
        lambda r: "/api/jobs" in r.url and "status=user_deleted" in r.url,
        timeout=5_000,
    ):
        page.select_option("#filter-status", "user_deleted")
    # Now wait for the DOM to reflect the new fetch — every visible row's id
    # must come from the trash query.
    page.wait_for_function(
        "() => document.querySelectorAll('#jobs-body tr.job-row').length > 0",
        timeout=5_000,
    )
    # Give the render a tick to settle past the network response
    page.wait_for_load_state("networkidle", timeout=5_000)

    # Cross-check: every visible id has reject_reason='user_deleted' in the DB
    visible_ids = page.evaluate(
        "() => Array.from(document.querySelectorAll('#jobs-body tr.job-row'))"
        ".map(tr => parseInt(tr.id.replace('row-', ''), 10))"
    )
    assert visible_ids
    conn = sqlite3.connect(_DB_PATH)
    placeholders = ",".join("?" * len(visible_ids))
    rows = conn.execute(
        f"SELECT id, reject_reason FROM jobs WHERE id IN ({placeholders})",
        visible_ids,
    ).fetchall()
    conn.close()
    bad = [(jid, reason) for jid, reason in rows if reason != "user_deleted"]
    assert not bad, f"TRASH filter showed non-user_deleted rows: {bad[:5]}"

    # Switch back to ALL STATUS — those same ids should disappear from view
    page.select_option("#filter-status", "")
    page.wait_for_function(
        "(ids) => !ids.some(id => document.getElementById('row-' + id))",
        arg=visible_ids, timeout=5_000,
    )


# ---------------------------------------------------------------------
# K. LOAD MORE then TOP SCORE — sort applies to all loaded pages,
#    toggle off keeps all pages in API order
# ---------------------------------------------------------------------

def test_load_more_then_top_score_sort(page: Page, base_url: str):
    """Two regressions to lock down:
      1. Click TOP SCORE first (sorts visible 100 by score desc), then click
         LOAD MORE. The newly appended page must end up sorted into the same
         desc order — not pasted on the bottom in API order.
      2. After LOAD MORE merges in, clicking TOP SCORE again must restore
         all 200 rows in API order — NOT shrink the list back to 100 (which
         was the bug: originalJobsSnapshot only captured the first page).
    """
    page.goto(base_url, wait_until="domcontentloaded")
    page.wait_for_selector("#jobs-body tr.job-row", timeout=10_000)
    page.select_option("#filter-days", "365")
    page.wait_for_function(
        "() => document.querySelectorAll('#jobs-body tr.job-row').length === 100",
        timeout=8_000,
    )
    if not page.locator("#load-more-bar").is_visible():
        pytest.skip("LOAD MORE not shown — fewer than 100 jobs match the filter")

    # Click TOP SCORE first
    page.click("#fab-score")
    expect(page.locator("#fab-score")).to_have_class(re.compile(r"\bactive\b"), timeout=2_000)
    pre_load_more = _score_column_values(page)
    assert pre_load_more == sorted(pre_load_more, reverse=True), (
        f"first sort wrong: {pre_load_more[:10]}..."
    )
    assert len(pre_load_more) == 100

    # Click LOAD MORE — newly fetched page must be re-sorted into the existing
    # desc order, not pasted at the end in API order.
    page.locator("#load-more-bar button").click()
    page.wait_for_function(
        "() => document.querySelectorAll('#jobs-body tr.job-row').length > 100",
        timeout=8_000,
    )
    after = page.locator("#jobs-body tr.job-row").count()
    assert after >= 200, f"LOAD MORE should have brought ≥200 rows, got {after}"
    expect(page.locator("#fab-score")).to_have_class(re.compile(r"\bactive\b"))
    merged_scores = _score_column_values(page)
    assert merged_scores == sorted(merged_scores, reverse=True), (
        "LOAD MORE while TOP SCORE was active broke the sort: "
        f"first 10={merged_scores[:10]} last 10={merged_scores[-10:]}"
    )
    # JS state should reflect 200 rows still under the sort
    js_state = page.evaluate(
        "({len: currentJobs.length, sorted: scoreSorted, "
        " snap: originalJobsSnapshot ? originalJobsSnapshot.length : null})"
    )
    assert js_state["len"] == after
    assert js_state["sorted"] is True
    assert js_state["snap"] == after, (
        f"originalJobsSnapshot length {js_state['snap']} != currentJobs {after} "
        "— toggle off would lose appended rows"
    )

    # Click TOP SCORE again — must keep 200 rows AND restore API order
    page.click("#fab-score")
    expect(page.locator("#fab-score")).not_to_have_class(re.compile(r"\bactive\b"), timeout=2_000)
    after_toggle_off = page.locator("#jobs-body tr.job-row").count()
    assert after_toggle_off == after, (
        f"toggling TOP SCORE off shrank the list: {after} -> {after_toggle_off} "
        "(originalJobsSnapshot was missing the appended page)"
    )
    restored = _score_column_values(page)
    # API order is fetched_at desc, not score desc — so the score column is
    # almost certainly NOT in desc order (and nothing here forces a specific
    # value sequence). Just assert it's not the post-sort sequence.
    assert restored != merged_scores, (
        "toggle off produced the sorted sequence — sort state likely got stuck"
    )


# ---------------------------------------------------------------------
# L. Digest read/unread — expanding accordion auto-marks read; toggle works
# ---------------------------------------------------------------------

def test_digest_mark_read_persists(page: Page, base_url: str):
    """Expanding a digest accordion should auto-mark it read (POST
    /api/summaries/{id}/read). After reload, the same card must render
    with .is-read."""
    _open_jobs(page, base_url)
    page.locator(".nav button", has_text="DIGESTS").click()
    page.wait_for_function(
        "() => getComputedStyle(document.getElementById('tab-digests')).display !== 'none'",
        timeout=3_000,
    )
    cards = page.locator(".digest-card")
    page.wait_for_selector(".digest-card", timeout=5_000)
    if cards.count() == 0:
        pytest.skip("no digests in DB")

    first = cards.first
    digest_id = first.get_attribute("id").replace("digest-", "")
    # Force unread starting state
    page.evaluate(
        "(id) => fetch('/api/summaries/' + id + '/unread', {method:'POST'})",
        arg=digest_id,
    )
    page.reload(wait_until="domcontentloaded")
    page.locator(".nav button", has_text="DIGESTS").click()
    page.wait_for_selector(f"#digest-{digest_id}", timeout=5_000)
    card = page.locator(f"#digest-{digest_id}")
    expect(card).to_have_class(re.compile(r"\bis-unread\b"), timeout=3_000)

    # Click the header → expand → auto-mark read
    card.locator(".digest-header").click()
    page.wait_for_function(
        "(id) => document.getElementById('digest-' + id)?.dataset.isRead === '1'",
        arg=digest_id, timeout=3_000,
    )
    expect(card).to_have_class(re.compile(r"\bis-read\b"), timeout=2_000)

    # Reload — persists
    page.reload(wait_until="domcontentloaded")
    page.locator(".nav button", has_text="DIGESTS").click()
    page.wait_for_selector(f"#digest-{digest_id}", timeout=5_000)
    expect(page.locator(f"#digest-{digest_id}")).to_have_class(
        re.compile(r"\bis-read\b"), timeout=3_000
    )


def test_digest_unread_toggle(page: Page, base_url: str):
    """The 'MARK UNREAD/READ' button inside the expanded body reverses
    the auto-mark-read. Both directions must flip the card class + API."""
    _open_jobs(page, base_url)
    page.locator(".nav button", has_text="DIGESTS").click()
    page.wait_for_function(
        "() => getComputedStyle(document.getElementById('tab-digests')).display !== 'none'",
        timeout=3_000,
    )
    page.wait_for_selector(".digest-card", timeout=5_000)
    cards = page.locator(".digest-card")
    if cards.count() == 0:
        pytest.skip("no digests in DB")

    digest_id = cards.first.get_attribute("id").replace("digest-", "")
    page.evaluate(
        "(id) => fetch('/api/summaries/' + id + '/unread', {method:'POST'})",
        arg=digest_id,
    )
    page.reload(wait_until="domcontentloaded")
    page.locator(".nav button", has_text="DIGESTS").click()
    page.wait_for_selector(f"#digest-{digest_id}", timeout=5_000)

    # Expand → auto-read
    page.locator(f"#digest-{digest_id} .digest-header").click()
    page.wait_for_function(
        "(id) => document.getElementById('digest-' + id)?.dataset.isRead === '1'",
        arg=digest_id, timeout=3_000,
    )

    # Click MARK UNREAD in body → flips back
    btn = page.locator(f"#digest-{digest_id} .digest-read-toggle")
    expect(btn).to_be_visible()
    btn.click()
    page.wait_for_function(
        "(id) => document.getElementById('digest-' + id)?.dataset.isRead === '0'",
        arg=digest_id, timeout=3_000,
    )
    expect(page.locator(f"#digest-{digest_id}")).to_have_class(
        re.compile(r"\bis-unread\b"), timeout=2_000
    )

    final = page.evaluate(
        "async (id) => (await (await fetch('/api/summaries?limit=50')).json()).summaries.find(s => s.id == id).is_read",
        arg=digest_id,
    )
    assert final == 0


# ---------------------------------------------------------------------
# M. Back-to-top button on digests tab — appears on scroll, returns top
# ---------------------------------------------------------------------

def test_digest_back_to_top(page: Page, base_url: str):
    """The digests tab uses inline accordion cards rather than a modal, so
    scroll happens on window. The back-to-top button is hidden on every
    other tab and only fades in past 300px of window scroll while the
    digests tab is active."""
    _open_jobs(page, base_url)
    # On the jobs tab the button should be hidden regardless of scroll
    expect(page.locator("#digest-top-btn")).to_be_hidden()

    page.locator(".nav button", has_text="DIGESTS").click()
    page.wait_for_function(
        "() => getComputedStyle(document.getElementById('tab-digests')).display !== 'none'",
        timeout=3_000,
    )

    cards = page.locator(".digest-card")
    page.wait_for_selector(".digest-card", timeout=5_000)
    if cards.count() == 0:
        pytest.skip("no digests in DB to test")

    # Expand the first card so we have plenty of scrollable content
    first = cards.first
    first_id = first.get_attribute("id").replace("digest-", "")
    first.locator(".digest-header").click()
    page.wait_for_function(
        "(id) => getComputedStyle(document.getElementById('body-' + id)).display !== 'none'",
        arg=first_id, timeout=3_000,
    )

    btn = page.locator("#digest-top-btn")
    # Pre-scroll: button is mounted (not [hidden]) but lacks .visible
    expect(btn).not_to_have_class(re.compile(r"\bvisible\b"))

    page_h = page.evaluate("document.body.scrollHeight")
    vp_h = page.evaluate("window.innerHeight")
    if page_h - vp_h < 400:
        pytest.skip(f"page too short for back-to-top test ({page_h - vp_h}px scroll)")

    # Scroll past 300
    page.evaluate("window.scrollTo({top: 600, behavior: 'instant'})")
    page.wait_for_function(
        "() => document.getElementById('digest-top-btn').classList.contains('visible')",
        timeout=2_000,
    )
    expect(btn).to_have_class(re.compile(r"\bvisible\b"))

    # Click → smooth scroll to 0
    btn.click()
    page.wait_for_function(
        "() => window.scrollY < 10",
        timeout=2_000,
    )
    page.wait_for_function(
        "() => !document.getElementById('digest-top-btn').classList.contains('visible')",
        timeout=2_000,
    )

    # Switch back to jobs → button should hide (regardless of any scroll)
    page.locator(".nav button", has_text="JOBS").click()
    page.wait_for_function(
        "() => getComputedStyle(document.getElementById('tab-jobs')).display !== 'none'",
        timeout=3_000,
    )
    expect(btn).to_be_hidden()
