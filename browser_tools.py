"""Web search and page browsing via a persistent, visible Playwright browser."""
from playwright.sync_api import sync_playwright

TOOL_SCHEMAS = [
    {
        "name": "web_search",
        "description": "Search the web and return the top results (title, URL, snippet). Use for current events, facts, or anything you're not certain about.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."}
            },
            "required": ["query"],
        },
    },
    {
        "name": "open_url",
        "description": "Open a URL in the browser and return the page title plus the main visible text. Use after web_search to read a specific result, or when the user gives you a direct link.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to open."}
            },
            "required": ["url"],
        },
    },
]

_playwright = None
_browser = None
_context = None


def _ensure_browser():
    global _playwright, _browser, _context
    if _context is not None:
        return _context
    _playwright = sync_playwright().start()
    _browser = _playwright.chromium.launch(headless=False)
    _context = _browser.new_context()
    return _context


def shutdown():
    global _playwright, _browser, _context
    if _browser:
        _browser.close()
    if _playwright:
        _playwright.stop()
    _playwright = _browser = _context = None


def web_search(query: str) -> str:
    context = _ensure_browser()
    page = context.new_page()
    try:
        page.goto(f"https://html.duckduckgo.com/html/?q={query}", timeout=15000)
        page.wait_for_selector(".result", timeout=10000)
        results = page.eval_on_selector_all(
            ".result",
            """(els) => els.slice(0, 6).map(el => {
                const titleEl = el.querySelector('.result__title a');
                const snippetEl = el.querySelector('.result__snippet');
                return {
                    title: titleEl ? titleEl.innerText : '',
                    url: titleEl ? titleEl.href : '',
                    snippet: snippetEl ? snippetEl.innerText : ''
                };
            })""",
        )
        if not results:
            return f"No results found for '{query}'."
        lines = [f"{r['title']}\n{r['url']}\n{r['snippet']}" for r in results if r["title"]]
        return "\n\n".join(lines)
    except Exception as e:
        return f"Search failed: {e}"
    finally:
        page.close()


def open_url(url: str) -> str:
    context = _ensure_browser()
    page = context.new_page()
    try:
        page.goto(url, timeout=15000, wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        title = page.title()
        text = page.inner_text("body")
        text = " ".join(text.split())[:4000]
        return f"Title: {title}\n\n{text}"
    except Exception as e:
        return f"Couldn't open '{url}': {e}"
    finally:
        page.close()
