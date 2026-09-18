import copy
import csv
import json
import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import collector as c

KEY = 'China|Panda Plushie'


def provider(quantity, update):
    return ({KEY: quantity}, {'China': update})


def payload(quantity=0, update=100):
    return {'stocks': {'chi': {'update': update, 'stocks': [
        {'name': 'Panda Plushie', 'quantity': quantity, 'nextRestock': 'later'}]}}}


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path.cwd() / ("test-data-" + uuid.uuid4().hex)
        self.temp.mkdir()
        self.addCleanup(shutil.rmtree, self.temp)
        for name in ['LATEST', 'HISTORY', 'TRANSITIONS', 'STATE', 'OBSERVATIONS', 'HEALTH']:
            p = patch.object(c, name, self.temp / getattr(c, name).name)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(c, 'DATA_DIR', self.temp)
        p.start()
        self.addCleanup(p.stop)
        self.state = {}
        self.previous = {'stock': {KEY: 0}, 'source_updates': {'China': 100},
                         'observed_at': {'China': c.source_time(100)}, 'timestamp': c.source_time(100)}

    def poll(self, providers):
        self.previous = c.process(providers, self.previous, self.state, c.source_time(1000))
        # Mimic separate scheduled processes, including persisted candidates.
        self.state = json.loads(json.dumps(self.state))
        return self.previous

    def rows(self, path):
        if not path.exists():
            return []
        with path.open(encoding='utf-8') as file:
            return list(csv.DictReader(file))

    def test_candidate_requires_next_fresh_observation(self):
        self.poll({'YATA': provider(20, 101)})
        self.assertEqual(self.previous['stock'][KEY], 20)
        self.assertIn('candidate', self.state['items'][KEY])
        self.assertEqual(self.rows(c.TRANSITIONS), [])
        self.poll({'YATA': provider(30, 101)})
        self.assertEqual(self.rows(c.TRANSITIONS), [])
        self.poll({'YATA': provider(15, 102)})
        rows = self.rows(c.TRANSITIONS)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['type'], 'restock')
        self.assertEqual(rows[0]['upper_bound'], c.source_time(101))
        self.assertEqual(len(self.rows(c.HISTORY)), 2)

    def test_independent_fresh_agreement_confirms_immediately(self):
        self.poll({'YATA': provider(20, 102), 'Prometheus': provider(10, 101)})
        self.assertEqual(len(self.rows(c.TRANSITIONS)), 1)

    def test_stale_agreement_cannot_confirm(self):
        self.poll({'YATA': provider(20, 102), 'Prometheus': provider(10, 100)})
        self.assertEqual(self.rows(c.TRANSITIONS), [])

    def test_disagreement_uses_freshest_and_waits(self):
        result = self.poll({'YATA': provider(0, 101), 'Prometheus': provider(20, 102)})
        self.assertEqual(result['sources']['China'], 'Prometheus')
        self.assertEqual(result['stock'][KEY], 20)
        self.assertEqual(self.rows(c.TRANSITIONS), [])

    def test_positive_increase_never_restocks(self):
        self.previous['stock'][KEY] = 10
        self.poll({'YATA': provider(100, 101)})
        self.poll({'YATA': provider(200, 102)})
        self.assertEqual(self.rows(c.TRANSITIONS), [])

    def test_stockout_and_reversal(self):
        self.previous['stock'][KEY] = 10
        self.poll({'YATA': provider(0, 101)})
        self.poll({'YATA': provider(5, 102)})
        self.assertNotIn('candidate', self.state['items'][KEY])
        self.poll({'YATA': provider(0, 103)})
        self.poll({'YATA': provider(0, 104)})
        rows = self.rows(c.TRANSITIONS)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['type'], 'stockout')
        self.assertEqual(rows[0]['from_stock'], '5')

    def test_missing_preserves_value_and_candidate(self):
        self.poll({'YATA': provider(20, 101)})
        self.poll({'YATA': provider(None, 102)})
        self.assertEqual(self.previous['stock'][KEY], 20)
        self.assertEqual(self.rows(c.TRANSITIONS), [])
        self.poll({'YATA': provider(15, 103)})
        self.assertEqual(len(self.rows(c.TRANSITIONS)), 1)

    def test_older_fallback_does_not_roll_back(self):
        before = copy.deepcopy(self.previous)
        self.poll({'Prometheus': provider(20, 99)})
        self.assertEqual(self.previous['stock'][KEY], before['stock'][KEY])
        self.assertEqual(self.previous['source_updates']['China'], 100)
        self.assertEqual(self.rows(c.HISTORY), [])

    def test_baseline_does_not_generate_transition(self):
        self.previous = None
        self.poll({'YATA': provider(20, 101)})
        self.assertEqual(self.rows(c.TRANSITIONS), [])
        self.assertEqual(self.state['items'][KEY]['confirmed'], 20)

    def test_country_selection_is_independent(self):
        self.poll({'YATA': ({KEY: 0, 'Japan|Xanax': 1}, {'China': 101, 'Japan': 103}),
                   'Prometheus': ({KEY: 20, 'Japan|Xanax': 2}, {'China': 102, 'Japan': 102})})
        self.assertEqual(self.previous['sources'], {'China': 'Prometheus', 'Japan': 'YATA'})

    def test_invalid_quantities_are_missing(self):
        for value in [None, -1, 'bad', True, 0.5]:
            self.assertIsNone(c.parse_stock(payload(value))[0][KEY])
        data = payload()
        del data['stocks']['chi']['stocks'][0]['quantity']
        self.assertIsNone(c.parse_stock(data)[0][KEY])
        self.assertEqual(c.parse_stock(payload('0'))[0][KEY], 0)

    def test_watch_list(self):
        self.assertEqual(c.WATCH['China'], ['Blank Casino Chips', 'Panda Plushie', 'Pangolin Scales'])

    def test_each_source_failure_falls_back(self):
        for failure in ['YATA', 'Prometheus']:
            with self.subTest(failure=failure):
                def fetch(url):
                    if url == (c.URL if failure == 'YATA' else c.PROMETHEUS_URL):
                        raise OSError('offline')
                    return payload(10, 101)
                with patch.dict(os.environ, ENABLE_PROMETHEUS='true'), patch.object(c, 'fetch_data', side_effect=fetch):
                    c.main()
                self.assertEqual(json.loads(c.LATEST.read_text())['stock'][KEY], 10)
                self.assertEqual([r['success'] for r in self.rows(c.HEALTH)[-2:]],
                                 ['False', 'True'] if failure == 'YATA' else ['True', 'False'])

    def test_all_failures_preserve_files_and_log_health(self):
        c.LATEST.write_text(json.dumps(self.previous))
        c.STATE.write_text('{}')
        before = c.LATEST.read_bytes()
        with patch.dict(os.environ, ENABLE_PROMETHEUS='true'), patch.object(c, 'fetch_data', side_effect=OSError('offline')):
            with self.assertRaises(RuntimeError):
                c.main()
        self.assertEqual(c.LATEST.read_bytes(), before)
        self.assertEqual(c.STATE.read_text(), '{}')
        self.assertEqual(len(self.rows(c.HEALTH)), 2)

    def test_optional_provider_and_raw_logging(self):
        with patch.dict(os.environ, ENABLE_PROMETHEUS='false'), patch.object(c, 'fetch_data', return_value=payload()) as fetch:
            c.main()
        fetch.assert_called_once_with(c.URL)
        raw = json.loads(c.OBSERVATIONS.read_text())
        self.assertEqual(set(raw), {'source', 'collection_timestamp', 'observations'})
        self.assertEqual(raw['source'], 'YATA')
        self.assertEqual(raw['observations'][0]['nextRestock'], 'later')
        self.assertEqual(raw['observations'][0]['quantity'], 0)

    def test_observation_log_excludes_unwatched_items_and_payload(self):
        data = payload()
        data['metadata'] = {'large': 'unused' * 1000}
        data['stocks']['chi']['stocks'].extend([
            {'name': 'Peony', 'quantity': 100},
            {'name': ' pangolin SCALES ', 'quantity': 42, 'cost': 999, 'id': 123},
        ])
        data['stocks']['mex'] = {'update': 100, 'stocks': [
            {'name': 'Panda Plushie', 'quantity': 12}]}
        original = copy.deepcopy(data)
        for source in ['YATA', 'Prometheus']:
            c.log_raw(source, data, 'collection-time')
        records = [json.loads(line) for line in c.OBSERVATIONS.read_text().splitlines()]
        self.assertEqual(len(records), 2)
        for source, record in zip(['YATA', 'Prometheus'], records):
            self.assertEqual(record, {
                'source': source, 'collection_timestamp': 'collection-time',
                'observations': [
                    {'country': 'China', 'item': 'Panda Plushie', 'source_update': 100,
                     'quantity': 0, 'nextRestock': 'later'},
                    {'country': 'China', 'item': 'Pangolin Scales', 'source_update': 100,
                     'quantity': 42},
                ],
            })
        self.assertEqual(data, original)

    def test_observation_log_preserves_unknown_values_and_optional_restock(self):
        data = {'stocks': {'chi': {'stocks': [
            {'name': 'Panda Plushie', 'quantity': None, 'nextRestock': None},
            {'name': 'Pangolin Scales'},
        ]}}}
        c.log_raw('Prometheus', data, 'collection-time')
        observations = json.loads(c.OBSERVATIONS.read_text())['observations']
        self.assertEqual(observations, [
            {'country': 'China', 'item': 'Panda Plushie', 'source_update': None,
             'quantity': None, 'nextRestock': None},
            {'country': 'China', 'item': 'Pangolin Scales', 'source_update': None,
             'quantity': None},
        ])

    def test_observation_log_handles_missing_or_malformed_data(self):
        for data in [None, {}, {'stocks': None}, {'stocks': {'chi': None}},
                     {'stocks': {'chi': {'stocks': None}}},
                     {'stocks': {'chi': {'stocks': [None, {}, {'name': 'Peony'}]}}}]:
            c.log_raw('YATA', data, 'collection-time')
        records = [json.loads(line) for line in c.OBSERVATIONS.read_text().splitlines()]
        self.assertEqual(len(records), 6)
        for record in records:
            self.assertEqual(record, {'source': 'YATA', 'collection_timestamp': 'collection-time',
                                      'observations': []})

    def test_existing_csv_rows_and_headers_are_untouched(self):
        c.HISTORY.write_bytes((','.join(c.HISTORY_FIELDS) + '\r\nlegacy,row,kept,exactly,0,YATA,1\r\n').encode())
        original = c.HISTORY.read_bytes()
        self.poll({'YATA': provider(20, 101)})
        self.assertTrue(c.HISTORY.read_bytes().startswith(original))
        self.assertEqual(list(self.rows(c.HISTORY)[-1]), c.HISTORY_FIELDS)


if __name__ == '__main__':
    unittest.main()
