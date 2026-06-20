from counterfactual_podcast.titles import humanize_url, is_urlish, resolve_title


def test_is_urlish():
    assert is_urlish("https://example.com/x")
    assert is_urlish("  http://example.com")
    assert not is_urlish("A Real Title")
    assert not is_urlish("")
    assert not is_urlish(None)


def test_humanize_url_basic_slug():
    assert humanize_url("https://rodneybrooks.com/why-todays-humanoids-wont-learn-dexterity/") \
        == "Why Todays Humanoids Wont Learn Dexterity"


def test_humanize_url_strips_extension_and_leading_date():
    assert humanize_url("https://leodemoura.github.io/blog/2026-2-28-when-ai-writes-the-worlds-software/") \
        == "When Ai Writes The Worlds Software"
    assert humanize_url("https://zhengdongwang.com/2025/12/30/2025-letter.html") == "Letter"


def test_humanize_url_falls_back_to_host():
    assert humanize_url("https://android-dreams.ai/") == "android-dreams.ai"


def test_resolve_title_prefers_first_non_url():
    assert resolve_title(["A Good Title", "https://x.com/a"]) == "A Good Title"
    # digest title is a URL -> fall through to the card name
    assert resolve_title(["https://x.com/a", "Card Name Title"]) == "Card Name Title"


def test_resolve_title_humanizes_when_all_urlish():
    out = resolve_title(["https://site.com/the-article-name", "https://site.com/the-article-name"],
                        url="https://site.com/the-article-name")
    assert out == "The Article Name"


def test_resolve_title_empty_candidates_uses_url():
    assert resolve_title([None, ""], url="https://site.com/cool-post") == "Cool Post"
