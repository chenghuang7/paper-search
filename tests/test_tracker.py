import copy
import json
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from xml.sax.saxutils import escape

import paper_tracker as tracker

NOW = tracker.date('2026-09-06T01:17:00Z')
CONFIG = tracker.load_config(tracker.ROOT / 'config.json')


def blank():
    return dict(schema_version=1, papers=[], last_success=None, last_attempt=None,
                last_new_ids=[], error=None, config_fingerprint=None)


def paper(id='2609.00001', title='Time Series Forecasting', abstract='A prediction method.', published='2026-09-01T12:00:00Z', updated=None):
    return dict(id=id, title=title, abstract=abstract, authors=['Test Author'], published=published,
                updated=updated or published, url='https://arxiv.org/abs/' + id, pdf_url='https://arxiv.org/pdf/' + id)


class FakeClient:
    def __init__(self, papers):
        self.papers, self.calls = papers, []

    def fetch(self, topic, start, end):
        self.calls.append((topic, start, end))
        yield from copy.deepcopy(self.papers)


def feed(papers, total=None, offset=0):
    entries = []
    for item in papers:
        entries.append('<entry><id>http://arxiv.org/abs/{}</id><title>{}</title><summary>{}</summary>'
                       '<published>{}</published><updated>{}</updated><author><name>A</name></author></entry>'.format(
                           item['id'], escape(item['title']), escape(item['abstract']), item['published'], item['updated']))
    return ('<feed xmlns="http://www.w3.org/2005/Atom" xmlns:o="http://a9.com/-/spec/opensearch/1.1/">'
            '<o:totalResults>{}</o:totalResults><o:startIndex>{}</o:startIndex>{}</feed>'.format(
                len(papers) if total is None else total, offset, ''.join(entries))).encode()


class MatchingTests(unittest.TestCase):
    def test_positive_variants(self):
        for title, abstract in [('Time Series Forecasting', 'A new model.'),
                                ('Forecasting with transformers', 'For multivariate time series.'),
                                ('TIME-SERIES', 'A predictive model.'),
                                ('Time‑series forecasts', 'A statistical method.')]:
            with self.subTest(title=title):
                self.assertTrue(tracker.matches(paper(title=title, abstract=abstract), CONFIG['topics'][0]))

    def test_negative_and_exclusions(self):
        topic = copy.deepcopy(CONFIG['topics'][0])
        self.assertFalse(tracker.matches(paper(title='Time series classification', abstract='An anomaly detector.'), topic))
        self.assertFalse(tracker.matches(paper(title='Weather forecasting', abstract='A method.'), topic))
        topic['exclude'] = ['traffic']
        self.assertFalse(tracker.matches(paper(abstract='Traffic prediction.'), topic))

    def test_query_uses_documented_sort_only(self):
        query = tracker.make_query(CONFIG['topics'][0])
        self.assertIn('ti:"time series"', query)
        self.assertIn('abs:"forecasting"', query)
        self.assertNotIn('lastUpdatedDate:', query)


class SyncTests(unittest.TestCase):
    def test_first_run_and_idempotence(self):
        client = FakeClient([paper()])
        first = tracker.sync(CONFIG, blank(), client, NOW)
        self.assertEqual(client.calls[0][1], NOW - timedelta(days=30))
        second = tracker.sync(CONFIG, first, client, NOW + timedelta(days=1))
        self.assertEqual(len(second['papers']), 1)
        self.assertEqual(second['last_new_ids'], [])
        self.assertEqual(second['papers'][0]['first_seen'], tracker.stamp(NOW))

    def test_revision_is_not_new(self):
        first = tracker.sync(CONFIG, blank(), FakeClient([paper()]), NOW)
        revised = paper(abstract='Revised time series prediction.', updated='2026-09-07T00:00:00Z')
        result = tracker.sync(CONFIG, first, FakeClient([revised]), NOW + timedelta(days=1))
        self.assertEqual(result['last_new_ids'], [])
        self.assertEqual(result['papers'][0]['abstract'], revised['abstract'])

    def test_missed_days_catchup(self):
        first = tracker.sync(CONFIG, blank(), FakeClient([]), NOW)
        client = FakeClient([paper(published='2026-09-10T00:00:00Z')])
        result = tracker.sync(CONFIG, first, client, NOW + timedelta(days=40))
        self.assertEqual(client.calls[0][1], NOW - timedelta(days=7))
        self.assertEqual(len(result['papers']), 1)

    def test_changed_config_backfills(self):
        first = tracker.sync(CONFIG, blank(), FakeClient([]), NOW)
        config = copy.deepcopy(CONFIG)
        config['topics'][0]['exclude'] = ['traffic']
        client = FakeClient([])
        tracker.sync(config, first, client, NOW + timedelta(days=1))
        self.assertEqual(client.calls[0][1], NOW + timedelta(days=1) - timedelta(days=30))

    def test_no_partial_mutation(self):
        first = tracker.sync(CONFIG, blank(), FakeClient([paper()]), NOW)
        original = copy.deepcopy(first)
        class BrokenClient:
            def fetch(self, *args):
                yield paper(id='2609.00002')
                raise OSError('second page failed')
        with self.assertRaises(OSError):
            tracker.sync(CONFIG, first, BrokenClient(), NOW + timedelta(days=1))
        self.assertEqual(first, original)

    def test_multiple_topics_merge(self):
        config = copy.deepcopy(CONFIG)
        config['topics'].append(dict(id='forecasting', name='Forecasting', groups=[['forecasting']], exclude=[]))
        result = tracker.sync(config, blank(), FakeClient([paper()]), NOW)
        self.assertEqual(result['last_new_ids'], ['2609.00001'])
        self.assertEqual(len(result['papers'][0]['topics']), 2)

    def test_old_revisions_not_initial_new_papers(self):
        old = paper(published='2020-01-01T00:00:00Z', updated='2026-09-05T00:00:00Z')
        result = tracker.sync(CONFIG, blank(), FakeClient([old]), NOW)
        self.assertEqual(result['papers'], [])


class ApiTests(unittest.TestCase):
    def client(self, pages):
        client = tracker.ArxivClient()
        client.request = unittest.mock.Mock(side_effect=pages)
        return client

    def test_pagination_and_version_id(self):
        client = self.client([feed([paper(id='2609.00001v2')], total=2), feed([paper(id='2609.00002')], total=2, offset=1)])
        papers = list(client.fetch(CONFIG['topics'][0], NOW - timedelta(days=7), NOW))
        self.assertEqual(len(papers), 2)
        self.assertEqual(papers[0]['id'], '2609.00001')
        self.assertIn('start=1', client.request.call_args_list[1].args[0])

    def test_incomplete_and_repeated_pages_fail(self):
        for last_page in (feed([], total=2, offset=1), feed([paper()], total=2, offset=1)):
            client = self.client([feed([paper()], total=2), last_page])
            with self.assertRaises(ValueError):
                list(client.fetch(CONFIG['topics'][0], NOW - timedelta(days=7), NOW))

    def test_stop_at_time_boundary_not_global_total(self):
        old = paper(id='2001.00001', published='2020-01-01T00:00:00Z')
        client = self.client([feed([paper(), old], total=100000)])
        result = list(client.fetch(CONFIG['topics'][0], NOW - timedelta(days=7), NOW))
        self.assertEqual(len(result), 1)
        self.assertEqual(client.request.call_count, 1)

    def test_error_feed_and_empty_success(self):
        client = self.client([b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>error</id></entry></feed>'])
        with self.assertRaises(ValueError):
            list(client.fetch(CONFIG['topics'][0], NOW - timedelta(days=7), NOW))
        self.assertEqual(list(self.client([feed([])]).fetch(CONFIG['topics'][0], NOW - timedelta(days=7), NOW)), [])

    def test_retries_are_bounded(self):
        opener = unittest.mock.Mock(side_effect=OSError('offline'))
        sleep = unittest.mock.Mock()
        client = tracker.ArxivClient(opener=opener, sleep=sleep)
        with self.assertRaises(OSError):
            client.request('https://export.arxiv.org/api/query')
        self.assertEqual(opener.call_count, 3)
        self.assertGreaterEqual(sleep.call_count, 2)


class BuildTests(unittest.TestCase):
    def test_escape_embedded_data_and_relative_assets(self):
        state = tracker.sync(CONFIG, blank(), FakeClient([paper(abstract='Time series forecasting </script><script>alert(1)</script>')]), NOW)
        with tempfile.TemporaryDirectory() as directory:
            tracker.build(CONFIG, state, directory)
            output = Path(directory, 'index.html').read_text()
            self.assertNotIn('</script><script>alert(1)', output)
            self.assertIn('src="./app.js"', output)
            self.assertIn('href="./style.css"', output)
            self.assertNotIn('{{DATA}}', output)
            embedded = output.split('type="application/json">')[1].split('</script>')[0]
            self.assertEqual(json.loads(embedded)['papers'][0]['abstract'], state['papers'][0]['abstract'])

    def test_cli_failure_preserves_checkpoint_and_builds(self):
        state = tracker.sync(CONFIG, blank(), FakeClient([paper()]), NOW)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'papers.json')
            tracker.write_json(path, state)
            with patch.object(sys, 'argv', ['paper_tracker.py', 'update', '--data', str(path), '--output', str(Path(directory, 'site'))]), patch.object(tracker.ArxivClient, 'fetch', side_effect=OSError('offline')):
                self.assertEqual(tracker.main(), 1)
            saved = tracker.read_json(path)
            self.assertEqual(saved['papers'], state['papers'])
            self.assertEqual(saved['last_success'], state['last_success'])
            self.assertTrue(saved['error'])
            self.assertTrue(Path(directory, 'site/index.html').exists())


if __name__ == '__main__':
    unittest.main()
