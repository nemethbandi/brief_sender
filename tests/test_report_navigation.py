from html.parser import HTMLParser

from reports.report_builder import ReportBuilder


class Navigation(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.targets = []
        self.links = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and attrs.get('name'):
            assert attrs.get('id') == attrs['name']
            self.targets.append(attrs['name'])
        if tag == 'a' and attrs.get('href', '').startswith('#'):
            self.links.append(attrs['href'][1:])


def test_fund_navigation_has_unique_targets_and_no_broken_links():
    report = ReportBuilder().build('Brief', [], fund_portfolios=[
        {'fund_id': 'A / é', 'fund_name': 'Same <Fund>', 'quotes': []},
        {'fund_id': 'B " &', 'fund_name': 'Same <Fund>', 'quotes': []},
    ])
    nav = Navigation(report.html)
    assert len(nav.targets) == 4  # contents, two funds, momentum
    assert len(set(nav.targets)) == len(nav.targets)
    assert set(nav.links) == set(nav.targets)
    assert 'Same &lt;Fund&gt;' in report.html
    assert all(target.isascii() and ' ' not in target for target in nav.targets)


def test_optional_navigation_matches_visible_sections():
    empty = Navigation(ReportBuilder().build('Brief', [], fund_portfolios=[]).html)
    assert set(empty.targets) == {'brief_contents', 'brief_momentum'}
    report = ReportBuilder().build('Brief', [], momentum_changes={
        'entered': [], 'exited': [], 'rank_changes': [],
    }, momentum_sector_comparison=[{
        'sector': 'Technology', 'current_pct': 100, 'previous_pct': 100,
        'three_month_pct': 100, 'change_pp': 0, 'change_3m_pp': 0, 'color': '#123456',
    }])
    nav = Navigation(report.html)
    assert {'brief_changes', 'brief_sectors'} <= set(nav.targets)
    assert set(nav.links) == set(nav.targets)
