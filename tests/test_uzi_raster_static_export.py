"""Static exports must capture server-rendered values, never loading UI."""

from io import BytesIO

import pytest

from domain_packs.finance_ashare.reporting.uzi_native_report import (
    ReportImageUnavailableError,
    render_uzi_native_image,
)


@pytest.fixture
def browser_probe(monkeypatch):
    api = pytest.importorskip("playwright.sync_api")
    with api.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch()
        except api.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Chromium is not installed")
            raise
        browser.close()
    observations = []
    screenshot = api.Locator.screenshot

    def capture(locator, **kwargs):
        observations.append(locator.evaluate("""element => ({
            text: element.textContent,
            boot: !!document.getElementById('boot-overlay'),
            position: getComputedStyle(element).position,
            seatStyles: Array.from(element.querySelectorAll('.sc-best-cell')).map(cell => ({
                nameSize: getComputedStyle(cell.querySelector('.name')).fontSize,
                scoreSize: getComputedStyle(cell.querySelector('.score-num')).fontSize
            })),
            voteWrapping: Array.from(element.querySelectorAll('.sc-vote-row > span:last-child'))
                .map(span => getComputedStyle(span).whiteSpace),
            footerInside: (() => {
                const card = element.getBoundingClientRect();
                const footer = element.querySelector('footer')?.getBoundingClientRect();
                return !footer || (footer.top >= card.top && footer.bottom <= card.bottom);
            })()
        })"""))
        return screenshot(locator, **kwargs)

    monkeypatch.setattr(api.Locator, "screenshot", capture)
    return observations


@pytest.mark.parametrize("selector", ["#share-card", "#war-report"])
def test_export_keeps_original_score_and_excludes_boot_overlay(browser_probe, selector):
    # Deliberately deterministic animation surrogate: any executed page script
    # corrupts the score before the screenshot, independently of CPU timing.
    document = """<!doctype html><style>
      body {margin:0} #share-card,#war-report {position:absolute;left:-9999px;
        width:320px;height:180px;background:rgb(10,20,30);color:white}
      footer {position:absolute;bottom:0}
      #boot-overlay {position:fixed;inset:0;z-index:9999;background:rgb(255,0,0)}
    </style><div id="boot-overlay">LOADING</div>
    <article id="share-card"><span class="sc-score-giant">50</span>/100<footer>original footer</footer></article>
    <article id="war-report"><span class="wr-score">50</span>/100<footer>original footer</footer></article>
    <script>document.querySelectorAll('span').forEach(e => e.textContent='0');</script>"""
    payload = render_uzi_native_image(document, selector=selector)
    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    assert browser_probe == [{
        "text": "50/100original footer", "boot": False,
        "position": "relative", "footerInside": True,
        "seatStyles": [], "voteWrapping": [],
    }]
    pillow = pytest.importorskip("PIL.Image")
    raster = pillow.open(BytesIO(payload)).convert("RGB")
    assert raster.size == (640, 360)
    assert raster.getpixel((500, 150)) == (10, 20, 30)


def test_missing_export_card_is_still_an_explicit_error(browser_probe):
    with pytest.raises(ReportImageUnavailableError, match="缺少 #share-card"):
        render_uzi_native_image("<p>No card</p>", selector="#share-card")


def test_long_share_card_grows_without_clipping_content_or_overlapping_footer(browser_probe):
    document = """<style>
      #share-card {position:absolute;left:-9999px;width:320px;height:180px;overflow:hidden}
      .sc-footer {position:absolute;bottom:10px;left:10px;right:10px}
    </style><div id="share-card"><div style="height:500px">complete analysis</div>
      <footer class="sc-footer">full disclaimer</footer></div>"""
    pillow = pytest.importorskip("PIL.Image")
    payload = render_uzi_native_image(document, selector="#share-card")
    raster = pillow.open(BytesIO(payload))
    assert raster.height >= 1100
    assert browser_probe[0]["footerInside"] is True
    assert "full disclaimer" in browser_probe[0]["text"]


def test_native_injected_card_classes_receive_native_template_styles(browser_probe):
    document = """<style>
      #share-card {position:absolute;left:-9999px;width:1080px;height:1920px}
      .sc-bestcell .n {font-size:28px} .sc-bestcell .s {font-size:48px}
      .sc-vote-row {display:flex;font-size:28px}
      .sc-vote-row .bar {flex:1}
    </style><div id="share-card">
      <div class="sc-vote-row"><span>原生票数</span><div class="bar"></div><span style="width:60px">22 人</span></div>
      <div class="sc-best-cell"><div class="name">原生评委</div><div class="score-num">50</div></div>
    </div>"""
    render_uzi_native_image(document, selector="#share-card")
    assert browser_probe[0]["seatStyles"] == [{"nameSize": "28px", "scoreSize": "48px"}]
    assert browser_probe[0]["voteWrapping"] == ["nowrap"]


def test_only_known_export_selectors_are_accepted():
    with pytest.raises(ValueError, match="unsupported"):
        render_uzi_native_image("<p>Report</p>", selector="body")
