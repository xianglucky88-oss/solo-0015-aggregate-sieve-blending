"""Exercise physical archive stock over HTTP, starting with a populated v2 database."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from workbench_check import QuietEndpoint, ROOT
from workbench import profile
from workbench.db import _apply_schema, connect, initialize, transaction
from workbench.service import Service


DOC = (ROOT / 'examples/workbench.json').read_text(encoding='utf-8')
NAME = 'physical-archive'
STOCK = '/api/archive-stock/'


def plan(call, source, doc):
    draft = call('/api/stage', {'dataset': NAME, 'source': source, 'text': doc})
    assert draft['state'] == 'ready'
    call('/api/publish', {'draft': draft['id']})
    return call('/api/run', {'dataset': NAME, 'operations': ['archive-handoff']})['runs'][0]


def v2_fixture(path):
    with closing(connect(path)) as db, transaction(db, write=True):
        _apply_schema(db, 'schema.sql')
        _apply_schema(db, 'handoff.sql')
        db.execute('INSERT INTO identity VALUES(1, ?)', (profile.PRODUCT,))
        db.execute('PRAGMA user_version=2')
    service = Service(path, profile)
    first = plan(service.post, 'initial.json', DOC)
    evidence = service.get(f"/api/evidence/{first['id']}")[0]
    return first, evidence


def exercise(port, first, evidence):
    def call(route, data=None, *, fault=None):
        url = f'http://127.0.0.1:{port}{route}'
        request = Request(url) if data is None else Request(
            url, json.dumps(data).encode(), {'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=10) as response:
                assert fault is None, (route, fault)
                return json.load(response)
        except HTTPError as exc:
            with exc:
                got = exc.code, json.load(exc)['error']
            assert fault == got, (route, got, fault)

    run = first['id']
    assert call('/api/evidence/' + run) == evidence
    call(STOCK + 'history', {'dataset': NAME}, fault=(409, 'stock_not_open'))
    start = {'run': run, 'generation': 1}
    opening = call(STOCK + 'open', start)
    assert not call(STOCK + 'history', {'dataset': NAME})['events']

    def report(selected):
        return call(STOCK + 'reconcile', {'run': selected})

    def a_cell(statement):
        row, = [c for c in statement['cells'] if c['specimen'] == 'a' and c['bounds_um'] == [4000, 2000]]
        return row

    assert a_cell(report(run))['on_hand_g'] == 60
    issue = {**start, 'order': 'bench-east', 'ticket': 'east-1', 'mass_g': 12}
    east = call(STOCK + 'issue', issue)
    assert call(STOCK + 'issue', issue) == east
    call(STOCK + 'issue', {**issue, 'mass_g': 13}, fault=(409, 'stock_ticket_conflict'))
    call(STOCK + 'issue', {**issue, 'ticket': 'bad-mass', 'mass_g': True}, fault=(400, 'stock_mass'))

    west = {**start, 'order': 'bench-west', 'mass_g': 12}
    gate = Barrier(2)

    def send_west(ticket):
        request = Request(f'http://127.0.0.1:{port}{STOCK}issue',
                          json.dumps({**west, 'ticket': ticket}).encode(), {'Content-Type': 'application/json'})
        gate.wait(timeout=5)
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.load(response)['kind']
        except HTTPError as exc:
            with exc:
                return exc.code, json.load(exc)['error']

    with ThreadPoolExecutor(2) as workers:
        assert sorted(workers.map(send_west, ['west-1', 'west-2'])) == [(200, 'issue'), (409, 'stock_order_quota')]

    revised = json.loads(DOC)
    revised['archive_handoff']['holds'][0]['mass_g'] = 40
    del revised['archive_handoff']['orders']['bench-east']
    second = plan(call, 'new-holds.json', json.dumps(revised))
    next_run = second['id']
    assert second['output']['state'] == 'ready'
    assert call(STOCK + 'open', start) == opening
    call(STOCK + 'open', {'run': next_run, 'generation': 2}, fault=(409, 'stock_already_open'))
    assert call(STOCK + 'issue', issue) == east
    call(STOCK + 'issue', {**issue, 'ticket': 'stale'}, fault=(409, 'stock_activation_changed'))
    call(STOCK + 'issue', {**west, 'run': next_run, 'generation': 2, 'ticket': 'no-free-stock', 'mass_g': 1},
         fault=(409, 'stock_hold_or_balance'))

    parent = {'dataset': NAME, 'issue_ticket': 'east-1'}
    returned = {**parent, 'ticket': 'return-1', 'mass_g': 3, 'reason': 'Unused sample weighed back in'}
    receipt = call(STOCK + 'return', returned)
    correction = {**parent, 'ticket': 'amend-1', 'basis': 'east-1', 'issued_g': 16,
                  'reason': 'Corrected reading on the original signed slip'}
    amendment = call(STOCK + 'correct', correction)
    assert receipt['delta_g'] == -3 and amendment['delta_g'] == 4
    assert call(STOCK + 'return', returned) == receipt
    assert call(STOCK + 'correct', correction) == amendment
    call(STOCK + 'correct', {**correction, 'ticket': 'stale-correction', 'issued_g': 14},
         fault=(409, 'stock_correction_stale'))
    call(STOCK + 'correct', {**correction, 'ticket': 'less-than-returned', 'basis': 'amend-1', 'issued_g': 2},
         fault=(409, 'stock_correction_amount'))
    call(STOCK + 'correct', {**correction, 'ticket': 'too-heavy', 'basis': 'amend-1', 'issued_g': 60},
         fault=(409, 'stock_physical_balance'))
    call(STOCK + 'return', {**returned, 'ticket': 'too-much-returned', 'mass_g': 14},
         fault=(409, 'stock_return_amount'))

    now = report(next_run)
    old = report(run)
    removed = next(o for o in now['orders'] if o['order'] == 'bench-east')
    assert removed['plan_status'] == 'removed'
    assert (removed['issued_g'], removed['returned_g'], removed['net_used_g'], removed['difference_g']) == (16, 3, 13, 13)
    assert (a_cell(now)['on_hand_g'], a_cell(now)['unheld_g'], a_cell(old)['unheld_g']) == (35, -5, 15)

    # A new measured value does not replace the old physical balance.
    changed = revised['archived_assays']['a']
    changed['retained_g'][1] -= 2
    changed['pan_g'] += 2
    revised['archive_handoff']['holds'][0]['mass_g'] = 35
    third = plan(call, 'reweighed.json', json.dumps(revised))
    assert third['output']['state'] == 'ready'
    call(STOCK + 'issue', {**west, 'run': third['id'], 'generation': 3, 'ticket': 'mismatch', 'mass_g': 1},
         fault=(409, 'stock_cell_changed'))
    disputed = a_cell(report(third['id']))
    assert (disputed['opening_g'], disputed['plan_measured_g'], disputed['on_hand_g'], disputed['unheld_g']) == (60, 58, 35, None)

    revised['archive_handoff']['orders']['bench-west']['specimen'] = 'b'
    fourth = plan(call, 'retargeted.json', json.dumps(revised))
    call(STOCK + 'issue', {**west, 'run': fourth['id'], 'generation': 4, 'ticket': 'moved', 'mass_g': 1},
         fault=(409, 'stock_order_retargeted'))
    branches = [o for o in report(fourth['id'])['orders'] if o['order'] == 'bench-west']
    assert [(o['plan_status'], o['difference_g']) for o in branches] == [('retargeted', 12), ('planned', -19)]

    call('/api/restore', {'dataset': NAME, 'revision': first['revision'], 'generation': 4})
    call(STOCK + 'issue', {**issue, 'ticket': 'stale-after-restore', 'mass_g': 1}, fault=(409, 'stock_activation_changed'))
    assert call(STOCK + 'issue', {**issue, 'ticket': 'east-2', 'generation': 5, 'mass_g': 7})['delta_g'] == 7
    assert call('/api/run', {'dataset': NAME, 'operations': ['archive-handoff']})['runs'] == [first]
    assert call('/api/evidence/' + run) == evidence
    ledger = call(STOCK + 'history', {'dataset': NAME})
    events = ledger['events']
    assert [event['kind'] for event in events] == ['issue', 'issue', 'return', 'correction', 'issue']
    assert sum(e['delta_g'] for e in events) == 60 - a_cell(report(run))['on_hand_g'] == 32
    assert report(run)['through_seq'] == events[-1]['seq']
    tail = call(STOCK + 'history', {'dataset': NAME, 'after': events[2]['seq']})
    assert tail['events'] == events[3:] and tail['next_after'] is None
    # A downward ticket correction changes the balance but never the return total.
    fixed = call(STOCK + 'correct', {**correction, 'ticket': 'amend-2', 'basis': 'amend-1', 'issued_g': 15})
    assert fixed['delta_g'] == -1
    final = report(run)
    east_order = next(o for o in final['orders'] if o['order'] == 'bench-east')
    assert (east_order['issued_g'], east_order['returned_g'], east_order['net_used_g']) == (22, 3, 19)
    assert a_cell(final)['on_hand_g'] == 29


def main():
    with TemporaryDirectory() as tmp:
        db = Path(tmp) / 'archive.sqlite3'
        first, evidence = v2_fixture(db)
        initialize(db, profile.PRODUCT)
        initialize(db, profile.PRODUCT)
        with closing(connect(db)) as migrated:
            assert migrated.execute('PRAGMA user_version').fetchone()[0] == 4
        with ThreadingHTTPServer(('127.0.0.1', 0), QuietEndpoint) as server:
            server.service = Service(db, profile)
            worker = Thread(target=server.serve_forever)
            worker.start()
            try:
                exercise(server.server_port, first, evidence)
            finally:
                server.shutdown()
                worker.join()
    print('Archive stock: migration, HTTP movements, concurrency and reconciliation passed')


if __name__ == '__main__':
    main()
