import copy
import io
import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from datetime import timedelta
from email.message import Message
from email.utils import format_datetime
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


def oai_feed(papers, token=None, deleted=False):
    records = []
    for item in papers:
        versions = '<r:version version="v1"><r:date>{}</r:date></r:version>'.format(
            format_datetime(tracker.date(item['published'])))
        if item['updated'] != item['published']:
            versions += '<r:version version="v2"><r:date>{}</r:date></r:version>'.format(
                format_datetime(tracker.date(item['updated'])))
        records.append('<record><header><identifier>oai:arXiv.org:{0}</identifier>'
                       '<datestamp>2026-09-06</datestamp></header><metadata><r:arXivRaw>'
                       '<r:id>{0}</r:id><r:title>{1}</r:title><r:abstract>{2}</r:abstract>'
                       '<r:authors>{3}</r:authors>{4}</r:arXivRaw></metadata></record>'.format(
                           item['id'], escape(item['title']), escape(item['abstract']),
                           escape(', '.join(item['authors'])), versions))
    if deleted:
        records.append('<record><header status="deleted"><identifier>oai:arXiv.org:0001.00001</identifier></header></record>')
    continuation = '' if token is None else '<resumptionToken>{}</resumptionToken>'.format(escape(token))
    return ('<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/" '
            'xmlns:r="http://arxiv.org/OAI/arXivRaw/"><ListRecords>{}{}</ListRecords></OAI-PMH>'.format(
                ''.join(records), continuation)).encode()


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
    def test_requests_wait_31_seconds_after_response_and_cache_needs_no_wait(self):
        clock = [0.0]
        starts = []
        def sleep(seconds):
            clock[0] += seconds
        def opener(*args, **kwargs):
            starts.append(clock[0])
            clock[0] += 17
            response = unittest.mock.MagicMock()
            response.__enter__.return_value.read.return_value = b'ok'
            return response
        client = tracker.ArxivClient(opener=opener, sleep=sleep, clock=lambda: clock[0])
        client.request('https://export.arxiv.org/api/query?start=0')
        client.request('https://export.arxiv.org/api/query?start=100')
        self.assertEqual(starts, [0, 48])
        completed = clock[0]
        client.request('https://export.arxiv.org/api/query?start=0')
        self.assertEqual(clock[0], completed)
        self.assertEqual(len(starts), 2)

    def test_transient_retry_also_waits_after_error_body_is_read(self):
        clock = [0.0]
        starts = []
        def sleep(seconds):
            clock[0] += seconds
        def opener(*args, **kwargs):
            starts.append(clock[0])
            clock[0] += 5
            if len(starts) == 1:
                raise urllib.error.HTTPError('https://oaipmh.arxiv.org/oai', 503, 'Unavailable', Message(), io.BytesIO(b'Temporary error'))
            response = unittest.mock.MagicMock()
            response.__enter__.return_value.read.return_value = b'ok'
            return response
        client = tracker.ArxivClient(opener=opener, sleep=sleep, clock=lambda: clock[0])
        self.assertEqual(client.request('https://oaipmh.arxiv.org/oai'), b'ok')
        self.assertEqual(starts, [0, 36])

    def test_shared_candidate_query_keeps_full_local_filtering(self):
        queries = [tracker.make_query(topic, candidate_only=True) for topic in CONFIG['topics']]
        self.assertEqual(len(set(queries)), 1)
        self.assertNotIn('forecasting', queries[0])
        self.assertIn('ti:"time series"', queries[0])
        self.assertFalse(tracker.matches(paper(title='Time series classification', abstract='A classifier.'), CONFIG['topics'][0]))

    def test_successful_responses_are_reused_within_a_run(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = feed([paper()])
        opener = unittest.mock.Mock(return_value=response)
        client = tracker.ArxivClient(opener=opener, sleep=unittest.mock.Mock())
        for topic in CONFIG['topics']:
            list(client.fetch(topic, NOW - timedelta(days=7), NOW))
        self.assertEqual(opener.call_count, 1)
        self.assertIn('application/atom+xml', opener.call_args.args[0].get_header('Accept'))
        self.assertTrue(opener.call_args.args[0].get_header('User-agent').startswith('Mozilla/5.0 '))

    def test_http_406_reports_body_without_repeating_rejected_request(self):
        error = urllib.error.HTTPError('https://export.arxiv.org/api/query', 406, 'Not Acceptable', Message(), io.BytesIO(b'Query not accepted'))
        opener = unittest.mock.Mock(side_effect=error)
        client = tracker.ArxivClient(opener=opener, sleep=unittest.mock.Mock())
        with self.assertRaisesRegex(RuntimeError, 'arXiv HTTP 406: Query not accepted'):
            client.request('https://export.arxiv.org/api/query')
        self.assertEqual(opener.call_count, 1)

    def test_rate_limit_stops_requests_and_records_retry_deadline(self):
        headers = Message()
        headers['Retry-After'] = '7200'
        error = urllib.error.HTTPError('https://export.arxiv.org/api/query', 429, 'Too Many Requests', headers, io.BytesIO(b'Rate exceeded.'))
        opener = unittest.mock.Mock(side_effect=error)
        client = tracker.ArxivClient(opener=opener, sleep=unittest.mock.Mock())
        before = tracker.datetime.now(tracker.timezone.utc)
        with self.assertRaises(tracker.RetryLaterError) as caught:
            client.request('https://export.arxiv.org/api/query')
        self.assertGreaterEqual(tracker.date(caught.exception.retry_not_before), before + timedelta(seconds=7200))
        self.assertEqual(opener.call_count, 1)

    def test_retry_after_supports_http_dates_and_missing_values(self):
        self.assertEqual(tracker.retry_deadline('Sun, 06 Sep 2026 04:00:00 GMT', NOW), tracker.date('2026-09-06T04:00:00Z'))
        for value in (None, '', 'bad', '0'):
            self.assertEqual(tracker.retry_deadline(value, NOW), NOW + timedelta(seconds=60))

    def test_transient_server_failure_retries(self):
        error = urllib.error.HTTPError('https://export.arxiv.org/api/query', 503, 'Service Unavailable', Message(), io.BytesIO(b'Temporarily unavailable'))
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'ok'
        opener = unittest.mock.Mock(side_effect=[error, response])
        sleep = unittest.mock.Mock()
        client = tracker.ArxivClient(opener=opener, sleep=sleep)
        self.assertEqual(client.request('https://export.arxiv.org/api/query'), b'ok')
        self.assertEqual(opener.call_count, 2)
        sleep.assert_any_call(31)

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


class OaiTests(unittest.TestCase):
    def client(self, pages):
        client = tracker.OaiClient(CONFIG['topics'])
        client.request = unittest.mock.Mock(side_effect=pages)
        return client

    def test_pages_shared_across_topics_and_opaque_token_encoded_once(self):
        token = 'verb%3DListRecords%26skip%3D1300'
        client = self.client([oai_feed([paper()], token=token),
                              oai_feed([paper(id='2609.00002', title='Time series foundation model forecasting')])])
        state = tracker.sync(CONFIG, blank(), client, NOW)
        self.assertEqual(len(state['papers']), 2)
        self.assertEqual(client.request.call_count, 2)
        self.assertEqual(state['source'], 'oai')
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(client.request.call_args_list[1].args[0]).query)
        self.assertEqual(query, {'verb': ['ListRecords'], 'resumptionToken': [token]})

    def test_dates_come_from_versions_not_metadata_or_announcement_date(self):
        original = paper(published='2026-09-01T12:13:14Z', updated='2026-09-04T15:16:17Z')
        state = tracker.sync(CONFIG, blank(), self.client([oai_feed([original])]), NOW)
        self.assertEqual(state['papers'][0]['published'], original['published'])
        self.assertEqual(state['papers'][0]['updated'], original['updated'])

    def test_duplicate_records_merge_latest_version_even_when_pages_are_out_of_order(self):
        newer = paper(abstract='Revised time series forecasting.', updated='2026-09-05T00:00:00Z')
        older = paper()
        client = self.client([oai_feed([newer], token='next'),
                              oai_feed([older, newer, paper(id='2609.00002')])])
        state = tracker.sync(CONFIG, blank(), client, NOW)
        self.assertEqual(len(state['papers']), 2)
        self.assertEqual(next(p for p in state['papers'] if p['id'] == older['id'])['abstract'], newer['abstract'])
        self.assertEqual(state['last_new_ids'], ['2609.00001', '2609.00002'])
        self.assertEqual(state['retrieval_stats'], {'pages': 2, 'records': 4, 'duplicates': 2})

    def test_same_version_prefers_latest_metadata_datestamp(self):
        revised = paper(abstract='Corrected time series forecasting metadata.')
        latest_page = oai_feed([revised], token='next').replace(b'2026-09-06</datestamp>', b'2026-09-07</datestamp>')
        client = self.client([latest_page, oai_feed([paper()])])
        state = tracker.sync(CONFIG, blank(), client, NOW + timedelta(days=1))
        self.assertEqual(state['papers'][0]['abstract'], revised['abstract'])
        self.assertEqual(state['papers'][0]['metadata_updated'], '2026-09-07T00:00:00Z')

    def test_incremental_metadata_overlap_keeps_delayed_announcements(self):
        first = tracker.sync(CONFIG, blank(), self.client([oai_feed([], deleted=True)]), NOW)
        # Submitted several days before the latest metadata announcement.
        late = paper(published='2026-09-04T12:00:00Z')
        client = self.client([oai_feed([late])])
        result = tracker.sync(CONFIG, first, client, NOW + timedelta(days=3))
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(client.request.call_args.args[0]).query)
        self.assertEqual(query['from'], ['2026-09-05'])
        self.assertEqual(result['last_new_ids'], [late['id']])

    def test_migration_and_changed_keywords_keep_full_backfill_window(self):
        previous = tracker.sync(CONFIG, blank(), FakeClient([]), NOW)
        client = self.client([oai_feed([], deleted=True)])
        first = tracker.sync(CONFIG, previous, client, NOW + timedelta(days=10))
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(client.request.call_args.args[0]).query)
        self.assertEqual(query['from'], ['2026-08-30'])
        config = copy.deepcopy(CONFIG)
        config['topics'][0]['exclude'] = ['traffic']
        client = self.client([oai_feed([], deleted=True)])
        tracker.sync(config, first, client, NOW + timedelta(days=11))
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(client.request.call_args.args[0]).query)
        self.assertEqual(query['from'], ['2026-08-18'])

    def test_revisions_and_deleted_records_do_not_create_new_papers(self):
        first = tracker.sync(CONFIG, blank(), self.client([oai_feed([paper()])]), NOW)
        revised = paper(abstract='Revised time series prediction.', updated='2026-09-07T00:00:00Z')
        old = paper(id='2001.00001', published='2020-01-01T00:00:00Z', updated='2026-09-07T00:00:00Z')
        result = tracker.sync(CONFIG, first, self.client([oai_feed([revised, old], deleted=True)]), NOW + timedelta(days=1))
        self.assertEqual(result['last_new_ids'], [])
        self.assertEqual(len(result['papers']), 1)
        self.assertEqual(result['papers'][0]['abstract'], revised['abstract'])

    def test_failed_later_page_does_not_advance_or_mutate_state(self):
        previous = tracker.sync(CONFIG, blank(), FakeClient([paper()]), NOW)
        original = copy.deepcopy(previous)
        client = self.client([oai_feed([paper(id='2609.00002')], token='next'), OSError('offline')])
        with self.assertRaises(OSError):
            tracker.sync(CONFIG, previous, client, NOW + timedelta(days=1))
        self.assertEqual(previous, original)

    def test_protocol_errors_duplicates_and_bad_metadata_are_not_success(self):
        invalid = oai_feed([paper()]).replace(b'<r:id>2609.00001</r:id>', b'<r:id>2609.00002</r:id>')
        error = b'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><error code="badResumptionToken">expired</error></OAI-PMH>'
        for pages in ([invalid], [error], [b'<html/>'], [oai_feed([])],
                      [oai_feed([paper()], token='next'), oai_feed([paper()])],
                      [oai_feed([paper()], token='next'), oai_feed([], token='again')]):
            with self.subTest(pages=pages):
                with self.assertRaises(ValueError):
                    tracker.sync(CONFIG, blank(), self.client(pages), NOW)

    def test_no_records_match_is_valid_but_not_after_a_continuation(self):
        empty = b'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><error code="noRecordsMatch">none</error></OAI-PMH>'
        self.assertEqual(tracker.sync(CONFIG, blank(), self.client([empty]), NOW)['papers'], [])
        with self.assertRaises(ValueError):
            tracker.sync(CONFIG, blank(), self.client([oai_feed([paper()], token='next'), empty]), NOW)


class BuildTests(unittest.TestCase):
    def test_cli_respects_persisted_rate_limit_without_network_request(self):
        state = blank()
        state['retry_not_before'] = '2099-01-01T00:00:00Z'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'papers.json')
            tracker.write_json(path, state)
            with patch.object(sys, 'argv', ['paper_tracker.py', 'update', '--data', str(path), '--output', str(Path(directory, 'site'))]), patch.object(tracker.OaiClient, 'fetch') as fetch:
                self.assertEqual(tracker.main(), 1)
                fetch.assert_not_called()
            self.assertEqual(tracker.read_json(path)['retry_not_before'], state['retry_not_before'])

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
            with patch.object(sys, 'argv', ['paper_tracker.py', 'update', '--data', str(path), '--output', str(Path(directory, 'site'))]), patch.object(tracker.OaiClient, 'fetch', side_effect=OSError('offline')):
                self.assertEqual(tracker.main(), 1)
            saved = tracker.read_json(path)
            self.assertEqual(saved['papers'], state['papers'])
            self.assertEqual(saved['last_success'], state['last_success'])
            self.assertTrue(saved['error'])
            self.assertEqual(saved['error_detail'], 'OSError: offline')
            self.assertTrue(Path(directory, 'site/index.html').exists())


if __name__ == '__main__':
    unittest.main()
