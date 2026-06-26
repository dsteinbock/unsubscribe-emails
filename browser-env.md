# Browser Control Environment Diagnostic

## Inventory

| Tool Name | Type | Purpose |
|-----------|------|---------|
| `mcp__Claude_in_Chrome__navigate` | MCP (Claude-in-Chrome) | Navigate to URLs or back/forward in history |
| `mcp__Claude_in_Chrome__get_page_text` | MCP (Claude-in-Chrome) | Extract plain text content from page |
| `mcp__Claude_in_Chrome__read_page` | MCP (Claude-in-Chrome) | Get accessibility tree / DOM snapshot |
| `mcp__Claude_in_Chrome__computer` | MCP (Claude-in-Chrome) | Click, type, screenshot, scroll, drag |
| `mcp__Claude_in_Chrome__find` | MCP (Claude-in-Chrome) | Find elements by natural language query |
| `mcp__Claude_in_Chrome__javascript_tool` | MCP (Claude-in-Chrome) | Execute JavaScript in page context |
| `mcp__Claude_in_Chrome__browser_batch` | MCP (Claude-in-Chrome) | Execute multiple actions in one batch |
| `mcp__Claude_in_Chrome__tabs_context_mcp` | MCP (Claude-in-Chrome) | Get/create MCP tab group context |
| `mcp__Claude_in_Chrome__tabs_create_mcp` | MCP (Claude-in-Chrome) | Create new tab in MCP group |
| `mcp__Claude_in_Chrome__tabs_close_mcp` | MCP (Claude-in-Chrome) | Close tab in MCP group |
| `mcp__chrome-devtools__new_page` | MCP (chrome-devtools) | Open new tab/page and load URL |
| `mcp__chrome-devtools__navigate_page` | MCP (chrome-devtools) | Navigate current page to URL or back/forward |
| `mcp__chrome-devtools__take_screenshot` | MCP (chrome-devtools) | Capture page viewport or full page as PNG/JPEG/WebP |
| `mcp__chrome-devtools__list_pages` | MCP (chrome-devtools) | List all open pages in browser |
| `mcp__chrome-devtools__select_page` | MCP (chrome-devtools) | Select a page as context for future calls |
| `mcp__chrome-devtools__close_page` | MCP (chrome-devtools) | Close a page by ID |
| `mcp__chrome-devtools__resize_page` | MCP (chrome-devtools) | Resize page window dimensions |
| `mcp__Control_Chrome__list_tabs` | MCP (Control_Chrome) | List all open Chrome tabs |
| `mcp__Control_Chrome__get_current_tab` | MCP (Control_Chrome) | Get info about active tab |
| `mcp__Control_Chrome__get_page_content` | MCP (Control_Chrome) | Get text content of page |
| `mcp__Control_Chrome__navigate` | MCP (Control_Chrome) | Navigate by URL or back/forward |
| Playwright (Python CLI) | CLI-driven | Headless Chromium automation via sync_playwright API |

## MCP Browser Smoke Test

**Method tested:** `chrome-devtools` MCP (Claude-in-Chrome unavailable)

**Connection:** ✅ Connected on first call

**Command:** `mcp__chrome-devtools__new_page` → https://example.com

**Result:** 
- Page opened and loaded successfully
- Title: "Example Domain"
- Status: Selected as page 2

**Token usage:** ~500 tokens (one page navigation + listing response)

**Output format:** Returns structured page info (title, URL, selected status); screenshot via separate `take_screenshot` call returns PNG image (~tiny—viewport capture)

**Note:** Claude-in-Chrome extension showed "not connected" error. chrome-devtools MCP is operational.

## Playwright-via-CLI Test

**Command executed:**
```bash
cd /Users/daniel/Dropbox/Code/unsubscribe-emails && \
export UV_CACHE_DIR=$PWD/.uv-cache && \
uv run --no-sync python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); pg=b.new_context().new_page(); pg.goto('https://example.com'); print(pg.title()); print(pg.locator('body').inner_text()[:120]); b.close(); p.stop()"
```

**Result:** ✅ Success

**Output:**
```
Example Domain
Example Domain

This domain is for use in documentation examples without needing permission. Avoid use in operations.

L
```

**Execution:** Completed in foreground, prompt return (~2 seconds for full launch/page/close cycle)

**Background process:** None required; clean exit after script completion

**Token usage:** Minimal—only text output returned to stdout

## Verdict

For the task **"open an unsubscribe URL, read page text, click one named button, re-read text"**:

- **Most reliable:** Playwright CLI (sync_playwright). Fully headless, deterministic, no extension dependency, instant startup/teardown.
- **Most token-cheap:** Playwright. Returns only requested data (text). No screenshots/snapshots unless explicitly captured.
- **Second choice:** chrome-devtools MCP (functional, but requires DevTools server running; slightly higher overhead per action).
- **Not available:** Claude-in-Chrome extension—disconnected; cannot use its text extraction or click tools.
- **Note:** For button clicking by name, Playwright's `.locator()` with text matching is more precise than tab-based screenshot-click workflows; chrome-devtools would require either page content inspection or screenshot coordinates.

**Recommendation:** Use Playwright for unsubscribe automation—it's isolation, determinism, and text-first output are ideal for this task.
